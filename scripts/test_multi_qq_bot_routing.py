"""验证 QQ 群按绑定 Bot 路由，且旧的未绑定配置保持可用。"""
import asyncio
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import sender
from plugins.media import make_text


class FakeBot:
    def __init__(self, name):
        self.name = name


async def main():
    bot_a = FakeBot("A")
    bot_b = FakeBot("B")
    sent = []

    async def fake_send(bot, groups, _message, **_kwargs):
        sent.append((bot.name, tuple(groups)))
        return {group: True for group in groups}

    with (
        mock.patch.object(sender, "get_qq_bot", return_value=bot_a),
        mock.patch.object(sender, "get_qq_bots", return_value={"A": bot_a, "B": bot_b}),
        mock.patch.object(sender, "get_group_bot_ids", return_value={"G1": "A", "G2": "B", "G3": "MISSING"}),
        mock.patch.object(sender, "_send_to_groups", side_effect=fake_send),
        mock.patch.object(sender, "_record_trace", new=mock.AsyncMock()),
        mock.patch.object(sender, "notify_delivery_failure") as notify,
    ):
        result = await sender.send_relay_message([make_text("x")], [], ["G1", "G2", "G3"], "C1", "M1")

    assert result == {"G1": True, "G2": True, "G3": False}
    assert set(sent) == {("A", ("G1",)), ("B", ("G2",))}
    notify.assert_awaited_once_with(bot_a, ["G3"], channel_id="C1", message_id="M1")

    sent.clear()
    with (
        mock.patch.object(sender, "get_qq_bot", return_value=bot_a),
        mock.patch.object(sender, "get_qq_bots", return_value={"A": bot_a}),
        mock.patch.object(sender, "get_group_bot_ids", return_value={"G1": ""}),
        mock.patch.object(sender, "_send_to_groups", side_effect=fake_send),
        mock.patch.object(sender, "_record_trace", new=mock.AsyncMock()),
    ):
        result = await sender.send_relay_message([make_text("x")], [], ["G1"])
    assert result == {"G1": True}
    assert sent == [("A", ("G1",))]
    print("PASS - QQ 群按绑定 Bot 投递，旧单 Bot 配置仍回退默认 Bot")


if __name__ == "__main__":
    asyncio.run(main())
