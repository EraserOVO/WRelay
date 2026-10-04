"""验证失败通知只在最终投递失败后发送，并按冷却期去重。"""
import asyncio
from collections import OrderedDict
import time
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import failure_notification as notifier
from plugins import config as config_module
from plugins import sender
from plugins.media import make_text


class FakeBot:
    def __init__(self):
        self.notifications = []

    async def send_to_c2c(self, openid, message):
        self.notifications.append((openid, message))


class FlakyBot:
    def __init__(self):
        self.attempts = []
        self.fail_u2_once = True

    async def send_to_c2c(self, openid, message):
        self.attempts.append((openid, message))
        if openid == "U2" and self.fail_u2_once:
            self.fail_u2_once = False
            raise RuntimeError("模拟私聊暂时失败")


async def main():
    notifier._recent_failures.clear()
    bot = FakeBot()
    config = {"enabled": True, "cooldown_seconds": 900}

    settings = {
        "failure_notification": config,
        "qq_user_openids": [
            {"openid": "U1", "enabled": True, "failure_notification": True},
            {"openid": "U2", "enabled": True, "failure_notification": True},
        ],
    }
    settings_loads = []
    with mock.patch.object(
        config_module,
        "_load_settings",
        side_effect=lambda: settings_loads.append(1) or settings,
    ):
        assert config_module.get_failure_notification_snapshot() == (
            config,
            ["U1", "U2"],
        )
    assert settings_loads == [1], settings_loads

    with (
        mock.patch.object(
            notifier,
            "get_failure_notification_snapshot",
            return_value=(config, ["U1", "U2"]),
        ),
    ):
        await notifier.notify_delivery_failure(bot, ["G1"], "C1", "M1")
        await notifier.notify_delivery_failure(bot, ["G1"], "C1", "M1")
    assert len(bot.notifications) == 2
    assert "来源：C1" in bot.notifications[0][1]
    assert "未送达群：G1" in bot.notifications[0][1]

    notifier._recent_failures.clear()
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    class SlowBot:
        def __init__(self):
            self.notifications = []

        async def send_to_c2c(self, openid, message):
            self.notifications.append((openid, message))
            send_started.set()
            await release_send.wait()

    slow_bot = SlowBot()
    first_notification = second_notification = None
    try:
        with mock.patch.object(
            notifier,
            "get_failure_notification_snapshot",
            return_value=(config, ["U1"]),
        ):
            first_notification = asyncio.create_task(
                notifier.notify_delivery_failure(slow_bot, ["G1"], "C1", "M-concurrent")
            )
            await asyncio.wait_for(send_started.wait(), timeout=1)
            second_notification = asyncio.create_task(
                notifier.notify_delivery_failure(slow_bot, ["G1"], "C1", "M-concurrent")
            )
            await asyncio.sleep(0)
            assert len(slow_bot.notifications) == 1, "并发失败回调重复发送同一提醒"
            assert not second_notification.done()

            release_send.set()
            await asyncio.gather(first_notification, second_notification)
            assert len(slow_bot.notifications) == 1
            assert notifier._notification_locks == {}
            assert notifier._notification_lock_users == {}
    finally:
        release_send.set()
        for task in (first_notification, second_notification):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    notifier._recent_failures.clear()
    flaky_bot = FlakyBot()
    with (
        mock.patch.object(
            notifier,
            "get_failure_notification_snapshot",
            return_value=(config, ["U1", "U2"]),
        ),
        mock.patch.object(notifier.logger, "warning"),
    ):
        await notifier.notify_delivery_failure(flaky_bot, ["G1"], "C1", "M2")
        await notifier.notify_delivery_failure(flaky_bot, ["G1"], "C1", "M2")
    assert [openid for openid, _ in flaky_bot.attempts] == ["U1", "U2", "U2"]

    notifier._recent_failures.clear()
    notifier._recent_failures.update(OrderedDict((
        (("old",), 1.0),
        (("middle",), 7.0),
        (("new",), 9.0),
    )))
    notifier._prune_recent_failures(10.0, 2.0)
    assert list(notifier._recent_failures) == [("new",)]

    notifier._recent_failures.clear()
    with mock.patch.object(notifier, "RECENT_FAILURE_LIMIT", 2):
        notifier._remember_failure(("first",), 1.0)
        notifier._remember_failure(("second",), 2.0)
        notifier._remember_failure(("third",), 3.0)
    assert list(notifier._recent_failures) == [("second",), ("third",)]

    heartbeat_ticks = [0]
    heartbeat_stop = asyncio.Event()

    async def heartbeat():
        while not heartbeat_stop.is_set():
            heartbeat_ticks[0] += 1
            await asyncio.sleep(0.005)

    def slow_snapshot():
        before = heartbeat_ticks[0]
        time.sleep(0.15)
        assert heartbeat_ticks[0] > before, "失败通知配置读取阻塞了事件循环"
        return {"enabled": False, "cooldown_seconds": 900}, []

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        with mock.patch.object(
            notifier,
            "get_failure_notification_snapshot",
            side_effect=slow_snapshot,
        ):
            await notifier.notify_delivery_failure(bot, ["G1"], "C1", "M3")
    finally:
        heartbeat_stop.set()
        await heartbeat_task

    bot = FakeBot()

    async def fake_send(*_args, **_kwargs):
        return {"G1": False}

    with mock.patch.object(sender, "get_qq_bot", return_value=bot), \
            mock.patch.object(sender, "_send_to_groups", side_effect=fake_send), \
            mock.patch.object(sender, "_record_trace", new=mock.AsyncMock()), \
            mock.patch.object(sender, "notify_delivery_failure") as notify:
        result = await sender.send_relay_message([make_text("x")], [], ["G1"], "C1", "M1")
    assert result == {"G1": False}
    notify.assert_awaited_once_with(bot, ["G1"], channel_id="C1", message_id="M1")
    print("PASS - 失败通知并发去重、冷却、过期清理和容量限制")


if __name__ == "__main__":
    asyncio.run(main())
