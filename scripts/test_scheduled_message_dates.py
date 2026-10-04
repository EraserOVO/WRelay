"""验证定时消息年度日期校验及无效计划隔离。"""

import asyncio
import math
import threading
import time
from datetime import datetime, timedelta
from unittest import mock

import nonebot

nonebot.init()

from plugins import scheduled_message
from plugins import config as config_mod


async def verify_shutdown_blocks_reconnect_restart():
    previous_loop_task = scheduled_message._loop_task
    previous_loop_started = scheduled_message._loop_started
    previous_stopping = scheduled_message._stopping
    loop_started = asyncio.Event()
    cancellation_seen = asyncio.Event()
    release_cancelled_loop = asyncio.Event()

    class TestQQBot:
        pass

    async def wait_for_shutdown_race():
        loop_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await release_cancelled_loop.wait()
            raise

    scheduled_message._loop_task = None
    scheduled_message._loop_started = False
    scheduled_message._stopping = False
    stopper = None
    try:
        with mock.patch.object(scheduled_message, "QQBot", TestQQBot), mock.patch.object(
            scheduled_message,
            "_scheduled_message_loop",
            side_effect=wait_for_shutdown_race,
        ):
            bot = TestQQBot()
            await scheduled_message._start_scheduled_message_loop(bot)
            old_loop_task = scheduled_message._loop_task
            assert old_loop_task is not None
            await asyncio.wait_for(loop_started.wait(), timeout=1)

            stopper = asyncio.create_task(
                scheduled_message._stop_scheduled_message_loop()
            )
            await asyncio.wait_for(cancellation_seen.wait(), timeout=1)
            assert scheduled_message._stopping is True
            assert scheduled_message._loop_task is None

            await scheduled_message._start_scheduled_message_loop(bot)
            assert scheduled_message._loop_task is None, "关闭期间重连重启了定时消息循环"

            release_cancelled_loop.set()
            await stopper
            assert old_loop_task.done()
            assert scheduled_message._loop_task is None
        print("PASS - 定时消息循环关闭期间的重连不会创建遗留任务")
    finally:
        release_cancelled_loop.set()
        if stopper is not None and not stopper.done():
            stopper.cancel()
            await asyncio.gather(stopper, return_exceptions=True)
        task = scheduled_message._loop_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        scheduled_message._loop_task = previous_loop_task
        scheduled_message._loop_started = previous_loop_started
        scheduled_message._stopping = previous_stopping


async def main():
    await verify_shutdown_blocks_reconnect_restart()

    def slow_disabled_config():
        time.sleep(0.25)
        return {"enabled": False, "target_groups": [], "messages": []}

    with mock.patch.object(
        scheduled_message,
        "get_scheduled_message_config",
        side_effect=slow_disabled_config,
    ):
        task = asyncio.create_task(scheduled_message._scheduled_message_loop())
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert elapsed < 0.15, "定时消息配置读取阻塞了事件循环: {:.3f}s".format(elapsed)

    active_started = threading.Event()
    active_finished = threading.Event()
    send_finished = asyncio.Event()

    def slow_active_groups():
        active_started.set()
        time.sleep(0.3)
        active_finished.set()
        return ["group-1"]

    async def finish_send(*_args, **_kwargs):
        send_finished.set()
        return {"group-1": True}

    due_config = {
        "enabled": True,
        "target_groups": ["group-1"],
        "messages": [{"id": "due", "content": "scheduled", "schedule_type": "interval"}],
    }
    schedule_calls = 0

    def due_once(current, _message):
        nonlocal schedule_calls
        schedule_calls += 1
        if schedule_calls == 1:
            return current - timedelta(seconds=1)
        return current + timedelta(hours=1)

    with (
        mock.patch.object(
            scheduled_message,
            "get_scheduled_message_config",
            return_value=due_config,
        ),
        mock.patch.object(
            scheduled_message,
            "get_active_groups",
            side_effect=slow_active_groups,
        ),
        mock.patch.object(scheduled_message, "next_scheduled_at", side_effect=due_once),
        mock.patch.object(
            scheduled_message,
            "send_relay_message",
            side_effect=finish_send,
        ),
    ):
        task = asyncio.create_task(scheduled_message._scheduled_message_loop())
        assert await asyncio.wait_for(
            asyncio.to_thread(active_started.wait, 1), timeout=1
        )
        assert not active_finished.is_set(), "启用群配置读取阻塞了事件循环"
        await asyncio.wait_for(send_finished.wait(), timeout=2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert active_finished.is_set()

    malformed_schedule_settings = {
        "scheduled_message": {
            "enabled": True,
            "target_groups": ["group-1"],
            "messages": [
                {
                    "id": "overflow-interval",
                    "content": "interval fallback",
                    "schedule_type": "interval",
                    "interval_days": math.inf,
                    "interval_hours": math.inf,
                    "interval_minutes": math.inf,
                },
                {
                    "id": "overflow-monthly",
                    "content": "monthly fallback",
                    "schedule_type": "calendar",
                    "calendar_type": "monthly",
                    "day_of_month": math.inf,
                    "send_time": "09:00",
                },
                {
                    "id": "overflow-yearly",
                    "content": "yearly fallback",
                    "schedule_type": "calendar",
                    "calendar_type": "yearly",
                    "month": math.inf,
                    "day": math.inf,
                    "send_time": "09:00",
                },
            ],
        },
    }
    with mock.patch.object(
        config_mod, "_load_settings", return_value=malformed_schedule_settings
    ):
        normalized = config_mod.get_scheduled_message_config()["messages"]
    assert normalized[0]["interval_days"] == 0
    assert normalized[0]["interval_hours"] == 0
    assert normalized[0]["interval_minutes"] == 1
    assert normalized[1]["day_of_month"] == 1
    assert normalized[2]["month"] == 1
    assert normalized[2]["day"] == 1

    now = datetime(2025, 1, 1, 8, 0)
    leap_day = scheduled_message._next_calendar_at(
        now,
        {"calendar_type": "yearly", "month": 2, "day": 29, "send_time": "09:00"},
    )
    assert leap_day == datetime(2028, 2, 29, 9, 0)
    assert scheduled_message._next_calendar_at(
        now,
        {"calendar_type": "yearly", "month": 4, "day": 31, "send_time": "09:00"},
    ) is None
    assert scheduled_message._next_calendar_at(
        now,
        {"calendar_type": "yearly", "month": 2, "day": 30, "send_time": "09:00"},
    ) is None

    config = {
        "enabled": True,
        "target_groups": ["group-1"],
        "messages": [
            {
                "id": "invalid-date",
                "content": "不会发送",
                "schedule_type": "calendar",
                "calendar_type": "yearly",
                "month": 4,
                "day": 31,
                "send_time": "09:00",
            },
            {
                "id": "valid-message",
                "content": "正常消息",
                "schedule_type": "interval",
            },
        ],
    }
    valid_schedule_calls = 0
    sleep_calls = 0

    def next_time(current, message):
        nonlocal valid_schedule_calls
        if message["id"] == "invalid-date":
            return None
        valid_schedule_calls += 1
        if valid_schedule_calls == 1:
            return current - timedelta(seconds=1)
        return current + timedelta(hours=1)

    async def stop_after_two_checks(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            raise asyncio.CancelledError

    with (
        mock.patch.object(
            scheduled_message,
            "get_scheduled_message_config",
            return_value=config,
        ),
        mock.patch.object(scheduled_message, "get_active_groups", return_value=["group-1"]),
        mock.patch.object(scheduled_message, "next_scheduled_at", side_effect=next_time),
        mock.patch.object(
            scheduled_message, "send_relay_message", new_callable=mock.AsyncMock,
            return_value={"group-1": True},
        ) as send,
        mock.patch.object(scheduled_message.logger, "error") as log_error,
        mock.patch.object(scheduled_message.asyncio, "sleep", side_effect=stop_after_two_checks),
    ):
        try:
            await scheduled_message._scheduled_message_loop()
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("测试循环应由模拟取消结束")

    assert send.await_count == 1
    assert log_error.call_count == 1

    exception_config = {
        "enabled": True,
        "target_groups": ["group-1"],
        "messages": [
            {
                "id": "fails-once",
                "content": "第一条",
                "schedule_type": "interval",
            },
            {
                "id": "continues",
                "content": "第二条",
                "schedule_type": "interval",
            },
        ],
    }
    scheduled_calls = {}

    def next_time_after_attempt(current, message):
        message_id = message["id"]
        scheduled_calls[message_id] = scheduled_calls.get(message_id, 0) + 1
        if scheduled_calls[message_id] == 1:
            return current - timedelta(seconds=1)
        return current + timedelta(hours=1)

    sent_content = []

    async def fail_first_send(parts, *_args, **_kwargs):
        content = "".join(part.data.get("text", "") for part in parts)
        sent_content.append(content)
        if content == "第一条":
            raise RuntimeError("simulated scheduled send failure")
        return {"group-1": True}

    sleep_calls = 0

    async def stop_exception_loop_after_two_checks(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            raise asyncio.CancelledError

    with (
        mock.patch.object(
            scheduled_message,
            "get_scheduled_message_config",
            return_value=exception_config,
        ),
        mock.patch.object(scheduled_message, "get_active_groups", return_value=["group-1"]),
        mock.patch.object(
            scheduled_message,
            "next_scheduled_at",
            side_effect=next_time_after_attempt,
        ),
        mock.patch.object(
            scheduled_message,
            "send_relay_message",
            side_effect=fail_first_send,
        ),
        mock.patch.object(scheduled_message.logger, "exception") as log_exception,
        mock.patch.object(
            scheduled_message.asyncio,
            "sleep",
            side_effect=stop_exception_loop_after_two_checks,
        ),
    ):
        try:
            await scheduled_message._scheduled_message_loop()
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("测试循环应由模拟取消结束")

    assert sent_content == ["第一条", "第二条"]
    assert log_exception.call_count == 1

    calculation_error_config = {
        "enabled": True,
        "target_groups": ["group-1"],
        "messages": [
            {"id": "bad-initial", "content": "不发送", "schedule_type": "interval"},
            {"id": "bad-recalculation", "content": "重算异常", "schedule_type": "interval"},
            {"id": "after-errors", "content": "继续发送", "schedule_type": "interval"},
        ],
    }
    calculation_calls = {}
    sent_after_calculation_errors = []

    def fail_schedule_calculation(current, message):
        message_id = message["id"]
        calculation_calls[message_id] = calculation_calls.get(message_id, 0) + 1
        call_count = calculation_calls[message_id]
        if message_id == "bad-initial":
            raise RuntimeError("simulated initial calculation failure")
        if message_id == "bad-recalculation" and call_count == 2:
            raise RuntimeError("simulated next-time calculation failure")
        if call_count == 1:
            return current - timedelta(seconds=1)
        return current + timedelta(hours=1)

    async def record_calculation_test_send(parts, *_args, **_kwargs):
        sent_after_calculation_errors.append(
            "".join(part.data.get("text", "") for part in parts)
        )
        return {"group-1": True}

    sleep_calls = 0

    async def stop_calculation_loop_after_one_check(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            raise asyncio.CancelledError

    with (
        mock.patch.object(
            scheduled_message,
            "get_scheduled_message_config",
            return_value=calculation_error_config,
        ),
        mock.patch.object(scheduled_message, "get_active_groups", return_value=["group-1"]),
        mock.patch.object(
            scheduled_message,
            "next_scheduled_at",
            side_effect=fail_schedule_calculation,
        ),
        mock.patch.object(
            scheduled_message,
            "send_relay_message",
            side_effect=record_calculation_test_send,
        ),
        mock.patch.object(scheduled_message.logger, "exception") as calculation_errors,
        mock.patch.object(
            scheduled_message.asyncio,
            "sleep",
            side_effect=stop_calculation_loop_after_one_check,
        ),
    ):
        try:
            await scheduled_message._scheduled_message_loop()
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("测试循环应由模拟取消结束")

    assert sent_after_calculation_errors == ["重算异常", "继续发送"]
    assert calculation_errors.call_count == 2
    print("PASS - 年度计划有效性、计算与投递异常隔离、闰年计划")


if __name__ == "__main__":
    asyncio.run(main())
