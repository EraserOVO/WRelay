"""验证 QQ 群按绑定 Bot 路由，且旧的未绑定配置保持可用。"""
import asyncio
import json
import tempfile
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import command, config, registration, sender
from plugins.media import make_text


class FakeBot:
    def __init__(self, name):
        self.name = name


async def main():
    duplicate_bindings = {
        "qq_group_openids": [
            {"openid": "G1", "enabled": True, "bot_id": "A"},
            {"openid": "G1", "enabled": False, "bot_id": "B"},
            {"openid": "G2", "enabled": False, "bot_id": "B"},
            {"openid": "G2", "enabled": True, "bot_id": "A"},
            {"openid": "G3", "enabled": False, "bot_id": "A"},
            {"openid": "G3", "enabled": False, "bot_id": "B"},
        ]
    }
    with mock.patch.object(config, "_load_settings", return_value=duplicate_bindings):
        assert config.get_group_bot_ids(["G1", "G2", "G3"]) == {
            "G1": "A",
            "G2": "A",
            "G3": "A",
        }

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

    async def fail_bot_b(bot, groups, _message, **_kwargs):
        if bot.name == "B":
            raise RuntimeError("simulated Bot B batch failure")
        return {group: True for group in groups}

    with (
        mock.patch.object(sender, "get_qq_bot", return_value=bot_a),
        mock.patch.object(sender, "get_qq_bots", return_value={"A": bot_a, "B": bot_b}),
        mock.patch.object(sender, "get_group_bot_ids", return_value={"G1": "A", "G2": "B"}),
        mock.patch.object(sender, "_send_to_groups", side_effect=fail_bot_b),
        mock.patch.object(sender, "_record_trace", new=mock.AsyncMock()) as trace,
        mock.patch.object(sender, "notify_delivery_failure", new=mock.AsyncMock()) as notify,
        mock.patch.object(sender.logger, "exception"),
    ):
        result = await sender.send_relay_message(
            [make_text("x")], [], ["G1", "G2"], "C1", "M2"
        )

    assert result == {"G1": True, "G2": False}
    assert trace.await_args.args[5] == result
    notify.assert_awaited_once_with(bot_a, ["G2"], channel_id="C1", message_id="M2")

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

    with tempfile.TemporaryDirectory(prefix="wrelay_multi_bot_") as tmp:
        discovered = tmp + "/qq_group_openids.json"
        registrations = tmp + "/qq_registrations.json"
        with (
            mock.patch.object(command, "OPENID_FILE", discovered),
            mock.patch.object(registration, "REGISTRATIONS_FILE", registrations),
        ):
            command.save_group_openid("G1", "A")
            command.save_group_openid("G2", "B")
            registration.register_group("G2", "10002", "测试群", "USER", "B")
        with open(discovered, "r", encoding="utf-8") as f:
            assert json.load(f)["group_bot_ids"] == {"G1": "A", "G2": "B"}
        with open(registrations, "r", encoding="utf-8") as f:
            assert json.load(f)["groups"]["G2"]["bot_id"] == "B"
    print("PASS - QQ 群按绑定 Bot 投递，重复项优先启用绑定，旧配置回退默认 Bot")


if __name__ == "__main__":
    asyncio.run(main())
