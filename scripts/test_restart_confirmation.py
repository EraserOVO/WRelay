"""验证 QQ Bot 重连后能读取并清除重启确认标记。"""
import asyncio
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import manage


class FakeQQBot:
    def __init__(self, self_id):
        self.self_id = self_id
        self.messages = []

    async def send_to_c2c(self, openid, message):
        self.messages.append((openid, message))

    async def send(self, event, message):
        self.messages.append((event.author.id, message))


class FakeC2CEvent:
    def __init__(self):
        self.author = SimpleNamespace(id="command-openid", user_openid="command-openid")

    def get_plaintext(self):
        return "restart"


class FakeListEvent(FakeC2CEvent):
    def get_plaintext(self):
        return "list"


async def assert_io_does_not_block(target, invoke, result=None):
    started = threading.Event()
    release = threading.Event()

    def blocking_io(*_args, **_kwargs):
        started.set()
        if not release.wait(1):
            raise AssertionError("测试未及时释放模拟重启标记 I/O")
        return result

    task = None
    try:
        with mock.patch.object(manage, target, side_effect=blocking_io):
            task = asyncio.create_task(invoke())
            began = await asyncio.wait_for(
                asyncio.to_thread(started.wait, 0.75),
                timeout=1,
            )
            assert began
            tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(tick.set)
            await asyncio.wait_for(tick.wait(), timeout=0.2)
            release.set()
            assert await task == result
    finally:
        release.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def verify_cancelled_restart_marker_write_is_cleaned():
    with tempfile.TemporaryDirectory() as directory:
        marker = Path(directory) / "restart_pending.json"
        write_started = threading.Event()
        release_write = threading.Event()
        original_write = manage.atomic_write_json

        def blocking_atomic_write(*args, **kwargs):
            write_started.set()
            if not release_write.wait(1):
                raise AssertionError("测试未及时释放重启标记写入")
            return original_write(*args, **kwargs)

        bot = FakeQQBot("bot-a")
        task = None
        try:
            with (
                mock.patch.object(manage, "RESTART_PENDING_FILE", str(marker)),
                mock.patch.object(manage, "QQBot", FakeQQBot),
                mock.patch.object(manage, "C2CMessageCreateEvent", FakeC2CEvent),
                mock.patch.object(manage, "get_active_user_openids", return_value={"command-openid"}),
                mock.patch.object(manage, "atomic_write_json", side_effect=blocking_atomic_write),
                mock.patch.object(manage, "_panel_post", new=mock.AsyncMock(return_value={"ok": True})) as panel_post,
            ):
                task = asyncio.create_task(manage.handle(bot, FakeC2CEvent()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(write_started.wait, 0.75),
                    timeout=1,
                )
                assert started
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done(), "取消应等待标记原子写入完成"
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_write.set()
                result = await asyncio.gather(task, return_exceptions=True)
                assert isinstance(result[0], asyncio.CancelledError), result
                panel_post.assert_not_called()
            assert not marker.exists(), "取消的重启请求遗留了确认标记"
            assert bot.messages == [("command-openid", "正在重启机器人...")]
        finally:
            release_write.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def main():
    await assert_io_does_not_block(
        "atomic_write_json",
        lambda: manage._write_restart_pending_async("write-openid", "bot-a"),
    )
    await assert_io_does_not_block(
        "load_json",
        manage._load_restart_pending_async,
        result={"openid": "read-openid"},
    )
    await assert_io_does_not_block(
        "_clear_restart_pending",
        manage._clear_restart_pending_async,
    )
    await verify_cancelled_restart_marker_write_is_cleaned()

    authorized_bot = FakeQQBot("bot-a")

    async def invoke_manage_list():
        await manage.handle(authorized_bot, FakeListEvent())
        return {"command-openid"}

    with (
        mock.patch.object(manage, "QQBot", FakeQQBot),
        mock.patch.object(manage, "C2CMessageCreateEvent", FakeListEvent),
        mock.patch.object(
            manage,
            "_dispatch",
            new=mock.AsyncMock(return_value="管理命令清单"),
        ),
    ):
        await assert_io_does_not_block(
            "get_active_user_openids",
            invoke_manage_list,
            result={"command-openid"},
        )
    assert authorized_bot.messages == [("command-openid", "管理命令清单")]

    with tempfile.TemporaryDirectory() as directory:
        marker = Path(directory) / "restart_pending.json"
        event = FakeC2CEvent()
        bot = FakeQQBot("bot-a")
        with (
            mock.patch.object(manage, "RESTART_PENDING_FILE", str(marker)),
            mock.patch.object(manage, "QQBot", FakeQQBot),
            mock.patch.object(manage, "C2CMessageCreateEvent", FakeC2CEvent),
            mock.patch.object(manage, "get_active_user_openids", return_value={"command-openid"}),
            mock.patch.object(
                manage,
                "_panel_post",
                new=mock.AsyncMock(return_value={"ok": False, "msg": "启动失败"}),
            ),
        ):
            await manage.handle(bot, event)
        assert bot.messages == [
            ("command-openid", "正在重启机器人..."),
            ("command-openid", "机器人重启失败，请检查桌面端日志"),
        ]
        assert not marker.exists()

    with tempfile.TemporaryDirectory() as directory:
        marker = Path(directory) / "restart_pending.json"
        marker.write_text(
            json.dumps({
                "openid": "user-openid",
                "time": int(time.time()),
                "bot_id": "bot-b",
            }),
            encoding="utf-8",
        )
        with (
            mock.patch.object(manage, "RESTART_PENDING_FILE", str(marker)),
            mock.patch.object(manage, "QQBot", FakeQQBot),
        ):
            other_bot = FakeQQBot("bot-a")
            await manage._confirm_restart(other_bot)
            assert other_bot.messages == []
            assert marker.exists()

            target_bot = FakeQQBot("bot-b")
            await manage._confirm_restart(target_bot)
            assert target_bot.messages == [("user-openid", "重启完毕")]
            assert not marker.exists()

        # 兼容升级前未记录 bot_id 的短生命周期重启标记。
        marker.write_text(
            json.dumps({"openid": "legacy-openid", "time": int(time.time())}),
            encoding="utf-8",
        )
        with (
            mock.patch.object(manage, "RESTART_PENDING_FILE", str(marker)),
            mock.patch.object(manage, "QQBot", FakeQQBot),
        ):
            legacy_bot = FakeQQBot("bot-a")
            await manage._confirm_restart(legacy_bot)
            assert legacy_bot.messages == [("legacy-openid", "重启完毕")]
            assert not marker.exists()

        invalid_markers = [
            {"openid": [], "time": int(time.time())},
            {"openid": 123, "time": int(time.time())},
            {"openid": "", "time": int(time.time())},
            {"openid": "   ", "time": int(time.time())},
            {"openid": "valid-openid", "bot_id": [], "time": int(time.time())},
            {"openid": "valid-openid", "bot_id": 123, "time": int(time.time())},
        ]
        for invalid_marker in invalid_markers:
            marker.write_text(json.dumps(invalid_marker), encoding="utf-8")
            with (
                mock.patch.object(manage, "RESTART_PENDING_FILE", str(marker)),
                mock.patch.object(manage, "QQBot", FakeQQBot),
            ):
                invalid_bot = FakeQQBot("bot-a")
                await manage._confirm_restart(invalid_bot)
                assert invalid_bot.messages == []
                assert not marker.exists()

        for invalid_time in (
            float("nan"),
            float("inf"),
            float("-inf"),
            True,
            10 ** 1000,
            time.time() + manage.RESTART_CONFIRM_MAX_FUTURE_SKEW + 1,
        ):
            marker.write_text(
                json.dumps({"openid": "invalid-time-openid", "time": invalid_time}),
                encoding="utf-8",
            )
            with (
                mock.patch.object(manage, "RESTART_PENDING_FILE", str(marker)),
                mock.patch.object(manage, "QQBot", FakeQQBot),
            ):
                invalid_bot = FakeQQBot("bot-a")
                await manage._confirm_restart(invalid_bot)
                assert invalid_bot.messages == []
                assert not marker.exists()

        marker.write_text(
            json.dumps({
                "openid": "clock-skew-openid",
                "time": time.time() + manage.RESTART_CONFIRM_MAX_FUTURE_SKEW / 2,
            }),
            encoding="utf-8",
        )
        with (
            mock.patch.object(manage, "RESTART_PENDING_FILE", str(marker)),
            mock.patch.object(manage, "QQBot", FakeQQBot),
        ):
            clock_skew_bot = FakeQQBot("bot-a")
            await manage._confirm_restart(clock_skew_bot)
            assert clock_skew_bot.messages == [("clock-skew-openid", "重启完毕")]
            assert not marker.exists()

    pending_marker = {
        "openid": "legacy-concurrent-openid",
        "time": int(time.time()),
    }
    confirmation_started = asyncio.Event()
    release_confirmation = asyncio.Event()
    clear_calls = 0

    class BlockingFakeQQBot(FakeQQBot):
        async def send_to_c2c(self, openid, message):
            self.messages.append((openid, message))
            confirmation_started.set()
            await release_confirmation.wait()

    async def load_pending_marker():
        return pending_marker

    async def clear_pending_marker():
        nonlocal clear_calls, pending_marker
        clear_calls += 1
        pending_marker = None

    first_bot = BlockingFakeQQBot("bot-a")
    second_bot = BlockingFakeQQBot("bot-b")
    first_confirmation = second_confirmation = None
    try:
        with (
            mock.patch.object(manage, "QQBot", BlockingFakeQQBot),
            mock.patch.object(
                manage,
                "_load_restart_pending_async",
                side_effect=load_pending_marker,
            ),
            mock.patch.object(
                manage,
                "_clear_restart_pending_async",
                side_effect=clear_pending_marker,
            ),
        ):
            first_confirmation = asyncio.create_task(
                manage._confirm_restart(first_bot)
            )
            await asyncio.wait_for(confirmation_started.wait(), timeout=1)
            second_confirmation = asyncio.create_task(
                manage._confirm_restart(second_bot)
            )
            await asyncio.sleep(0)
            assert second_bot.messages == [], "并发连接重复发送了重启确认"

            release_confirmation.set()
            await asyncio.gather(first_confirmation, second_confirmation)
            assert first_bot.messages == [
                ("legacy-concurrent-openid", "重启完毕")
            ]
            assert second_bot.messages == []
            assert pending_marker is None
            assert clear_calls == 1
    finally:
        release_confirmation.set()
        for task in (first_confirmation, second_confirmation):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    pending_marker = {
        "openid": "cancelled-clear-openid",
        "time": int(time.time()),
    }
    clear_started = threading.Event()
    release_clear = threading.Event()
    clear_calls = 0

    def blocking_clear_pending():
        nonlocal clear_calls, pending_marker
        clear_calls += 1
        clear_started.set()
        if not release_clear.wait(1):
            raise AssertionError("测试未及时释放重启标记清理")
        pending_marker = None

    async def load_during_clear():
        return pending_marker

    first_bot = FakeQQBot("bot-a")
    second_bot = FakeQQBot("bot-b")
    first_confirmation = second_confirmation = None
    try:
        with (
            mock.patch.object(manage, "QQBot", FakeQQBot),
            mock.patch.object(
                manage,
                "_load_restart_pending_async",
                side_effect=load_during_clear,
            ),
            mock.patch.object(
                manage,
                "_clear_restart_pending",
                side_effect=blocking_clear_pending,
            ),
        ):
            first_confirmation = asyncio.create_task(
                manage._confirm_restart(first_bot)
            )
            started = await asyncio.wait_for(
                asyncio.to_thread(clear_started.wait, 0.75),
                timeout=1,
            )
            assert started
            first_confirmation.cancel()
            await asyncio.sleep(0)
            assert not first_confirmation.done(), "取消提前释放了重启确认锁"

            second_confirmation = asyncio.create_task(
                manage._confirm_restart(second_bot)
            )
            await asyncio.sleep(0)
            assert second_bot.messages == [], "标记清理完成前发送了重复确认"

            release_clear.set()
            results = await asyncio.gather(
                first_confirmation,
                second_confirmation,
                return_exceptions=True,
            )
            assert isinstance(results[0], asyncio.CancelledError), results
            assert results[1] is None, results
            assert first_bot.messages == [
                ("cancelled-clear-openid", "重启完毕")
            ]
            assert second_bot.messages == []
            assert pending_marker is None
            assert clear_calls == 1
    finally:
        release_clear.set()
        for task in (first_confirmation, second_confirmation):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    print("PASS - 重启确认读取、私聊回复与标记清理")


if __name__ == "__main__":
    asyncio.run(main())
