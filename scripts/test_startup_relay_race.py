"""验证实时/补发不会重复投递，并在 QQ 重连后重启缺口补发。"""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import backfill
from plugins import relay_inflight


async def test_backfill_request_during_run_schedules_follow_up():
    previous_running = backfill._backfill_running
    previous_rerun = backfill._backfill_rerun_requested
    backfill._backfill_running = False
    backfill._backfill_rerun_requested = False
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = []

    async def fake_backfill_channel(*args):
        calls.append(args[0])
        if len(calls) == 1:
            first_started.set()
            await release_first.wait()

    try:
        with mock.patch.object(
            backfill,
            "get_backfill_runtime_snapshot",
            return_value={
                "enabled": True,
                "limit": 10,
                "channels": {"C1": "频道"},
                "groups_by_channel": {"C1": ["G1"]},
            },
        ), \
                mock.patch.object(backfill, "_backfill_channel", side_effect=fake_backfill_channel):
            first_task = asyncio.create_task(backfill._backfill_missed())
            await asyncio.wait_for(first_started.wait(), timeout=1)
            await backfill._backfill_missed()
            release_first.set()
            await asyncio.wait_for(first_task, timeout=1)

        assert calls == ["C1", "C1"], calls
        assert not backfill._backfill_running
        assert not backfill._backfill_rerun_requested
        print("PASS - 补发运行中再次触发会安排一轮后续补发")
    finally:
        backfill._backfill_running = previous_running
        backfill._backfill_rerun_requested = previous_rerun


async def main():
    async def test_qq_connect_triggers_backfill():
        started = asyncio.Event()

        class FakeQQBot:
            pass

        class FakeDiscordBot:
            pass

        async def fake_backfill():
            started.set()

        with (
            mock.patch.object(backfill, "QQBot", FakeQQBot),
            mock.patch.object(backfill, "DiscordBot", FakeDiscordBot),
            mock.patch.object(backfill, "_backfill_missed", fake_backfill),
        ):
            await backfill._backfill_wrapper(FakeQQBot())
            await asyncio.wait_for(
                started.wait(),
                timeout=1,
            )
            await asyncio.sleep(0)

        assert started.is_set()
        assert backfill._backfill_tasks == set()
        print("PASS - QQ Bot 重连会触发 Discord 缺口补发")

    async def test_shutdown_reaps_backfill_tasks():
        previous_stopping = backfill._backfill_stopping
        previous_running = backfill._backfill_running
        previous_rerun = backfill._backfill_rerun_requested
        previous_tasks = set(backfill._backfill_tasks)
        backfill._backfill_stopping = False
        backfill._backfill_running = False
        backfill._backfill_rerun_requested = False
        backfill._backfill_tasks.clear()
        started = asyncio.Event()
        stopped = asyncio.Event()

        class FakeQQBot:
            pass

        async def blocked_backfill():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stopped.set()
                raise

        try:
            with (
                mock.patch.object(backfill, "QQBot", FakeQQBot),
                mock.patch.object(backfill, "DiscordBot", FakeQQBot),
                mock.patch.object(backfill, "_backfill_missed", blocked_backfill),
            ):
                await backfill._backfill_wrapper(FakeQQBot())
                await asyncio.wait_for(started.wait(), timeout=1)
                assert len(backfill._backfill_tasks) == 1

                await backfill._stop_backfill_tasks()

                assert stopped.is_set()
                assert backfill._backfill_tasks == set()
                assert backfill._backfill_running is False
                assert backfill._backfill_rerun_requested is False
                await backfill._backfill_wrapper(FakeQQBot())
                assert backfill._backfill_tasks == set()
            print("PASS - 关闭时补发后台任务会停止并等待回收")
        finally:
            backfill._backfill_stopping = previous_stopping
            backfill._backfill_running = previous_running
            backfill._backfill_rerun_requested = previous_rerun
            backfill._backfill_tasks.clear()
            backfill._backfill_tasks.update(previous_tasks)

    relay_inflight._processing.clear()
    assert relay_inflight.claim_processing("C1", "101")

    message = {
        "message_id": "101",
        "author_username": "author",
        "content": "body",
        "embeds": [],
    }
    with (
        mock.patch.object(backfill, "load_last_messages", return_value={"C1": {"G1": "100"}}),
        mock.patch.object(backfill, "fetch_channel_messages_after", new=mock.AsyncMock(return_value=[message])),
        mock.patch.object(backfill, "prepare_discord_message", new=mock.AsyncMock()) as prepare,
        mock.patch.object(backfill, "send_and_record", new=mock.AsyncMock()) as send,
    ):
        await backfill._backfill_channel("C1", "频道", ["G1"], 10)

    assert not prepare.await_count
    assert not send.await_count
    relay_inflight.release_processing("C1", "101")
    assert not relay_inflight.is_processing("C1", "101")
    print("PASS - 实时路径在途时，启动补发跳过同一消息")

    messages = [
        {"message_id": "101", "author_username": "author", "content": "one"},
        {"message_id": "102", "author_username": "author", "content": "two"},
    ]
    with (
        mock.patch.object(backfill, "load_last_messages", return_value={"C1": {"G1": "100"}}),
        mock.patch.object(backfill, "fetch_channel_messages_after", new=mock.AsyncMock(return_value=messages)),
        mock.patch.object(backfill, "load_channel_filter", new=mock.AsyncMock(return_value={})),
        mock.patch.object(backfill, "check_message_filter", return_value=True),
        mock.patch.object(backfill, "prepare_discord_message", new=mock.AsyncMock(return_value=(["text"], []))),
        mock.patch.object(backfill, "send_and_record", new=mock.AsyncMock(return_value=None)) as send,
        mock.patch.object(backfill, "record", new=mock.AsyncMock()) as record,
    ):
        await backfill._backfill_channel("C1", "频道", ["G1"], 10)

    assert send.await_count == 1
    assert not record.await_count
    print("PASS - QQ Bot 未连接时停止处理本批后续补发消息")

    failed_message = {"message_id": "103", "content": "body"}
    with (
        mock.patch.object(
            backfill,
            "send_and_record",
            new=mock.AsyncMock(return_value={"G1": False, "G2": True}),
        ),
        mock.patch.object(backfill, "log_partial_failure") as partial_log,
        mock.patch.object(backfill, "record", new=mock.AsyncMock()) as record,
        mock.patch.object(backfill, "schedule_retry_async", new=mock.AsyncMock()) as retry_schedule,
    ):
        should_stop = await backfill._deliver_backfill_message(
            "C1",
            "103",
            failed_message,
            ["G1", "G2"],
            (["text"], []),
        )

    assert should_stop is False
    record.assert_awaited_once_with(True)
    partial_log.assert_called_once_with({"G1": False, "G2": True}, "C1", "103")
    retry_schedule.assert_awaited_once_with(
        "C1",
        "103",
        failed_message,
        ["G1"],
        source_label="自动补发来自",
    )
    print("PASS - 补发部分失败只为失败群排队，成功群仍记录已发送")
    await test_qq_connect_triggers_backfill()
    await test_backfill_request_during_run_schedules_follow_up()
    await test_shutdown_reaps_backfill_tasks()


if __name__ == "__main__":
    asyncio.run(main())
