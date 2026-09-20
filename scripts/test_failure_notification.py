"""验证失败通知只在最终投递失败后发送，并按冷却期去重。"""
import asyncio
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import failure_notification as notifier
from plugins import sender
from plugins.media import make_text


class FakeBot:
    def __init__(self):
        self.notifications = []

    async def send_to_c2c(self, openid, message):
        self.notifications.append((openid, message))


async def main():
    notifier._recent_failures.clear()
    bot = FakeBot()
    config = {"enabled": True, "cooldown_seconds": 900}
    with (
        mock.patch.object(notifier, "get_failure_notification_config", return_value=config),
        mock.patch.object(notifier, "get_failure_notification_recipients", return_value=["U1", "U2"]),
    ):
        await notifier.notify_delivery_failure(bot, ["G1"], "C1", "M1")
        await notifier.notify_delivery_failure(bot, ["G1"], "C1", "M1")
    assert len(bot.notifications) == 2
    assert "来源：C1" in bot.notifications[0][1]
    assert "未送达群：G1" in bot.notifications[0][1]

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
    print("PASS - 失败通知在最终失败后发送，并按冷却期去重")


if __name__ == "__main__":
    asyncio.run(main())
