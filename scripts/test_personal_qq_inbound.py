"""Second-phase authorization/routing tests; isolated files, no real QQ traffic."""
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nonebot
nonebot.init(_env_file=None)
from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent
from plugins import personal_qq as gateway, personal_qq_inbound as inbound, config, scheduled_message as timer

UIN, USER, GROUP = "123456789", 222222222, 987654321
TOKEN = "test-token-123456789"


def event(identifier, text, group=None, user=USER, message=None):
    data = dict(time=1, self_id=int(UIN), post_type="message", message_type="private" if group is None else "group",
                sub_type="friend" if group is None else "normal", message_id=identifier, user_id=user,
                message=message or [{"type": "text", "data": {"text": text}}], raw_message=text,
                font=0, sender={"user_id": user, "nickname": "test"})
    if group is not None:
        data["group_id"] = group
    return (PrivateMessageEvent if group is None else GroupMessageEvent).model_validate(data)


async def main():
    bot = SimpleNamespace(self_id=UIN, send=mock.AsyncMock(), send_private_msg=mock.AsyncMock())
    sender = gateway.PersonalSender(bot, TOKEN)
    gateway._ready[UIN] = sender
    entries = [dict(openid="personal:" + str(GROUP), bot_id="personal:" + UIN, enabled=True, name="source"),
               dict(openid="personal:888888888", bot_id="personal:" + UIN, enabled=True, name="target"),
               dict(openid="official-group", bot_id="official", enabled=True, name="official")]
    authorized = [False]
    async def translate(parts, *args, **kwargs):
        return parts
    with tempfile.TemporaryDirectory() as directory, \
            mock.patch.object(config, "SETTINGS_FILE", str(Path(directory) / "settings.json")), \
            mock.patch.object(gateway.config, "enabled_connections", return_value={UIN: {"token": TOKEN}}), \
            mock.patch.object(config, "is_user_authorized", side_effect=lambda value: authorized[0] and value == "personal:" + str(USER)), \
            mock.patch.object(config, "get_qq_group_entries", return_value=entries), \
            mock.patch.object(config, "get_related_group_openids", return_value={"personal:888888888"}), \
            mock.patch.object(config, "is_group_registered", return_value=True), \
            mock.patch("plugins.sender.send_relay_message", new=mock.AsyncMock(return_value={"personal:888888888": True})) as send, \
            mock.patch("plugins.translation.translate_text_parts", side_effect=translate), \
            mock.patch("plugins.manage._dispatch", new=mock.AsyncMock(return_value="status")) as management:
        await inbound.dispatch(bot, event(1, "/运行状态"))
        management.assert_not_awaited()
        await inbound.dispatch(bot, event(2, "/注册"))
        registered = json.loads(Path(config.SETTINGS_FILE).read_text(encoding="utf-8"))
        assert registered["qq_user_openids"][0]["enabled"] is False
        assert registered["qq_user_openids"][0]["openid"] == "personal:" + str(USER)
        authorized[0] = True
        await inbound.dispatch(bot, event(3, "/运行状态"))
        management.assert_awaited_once_with("/运行状态", [])
        original = event(4, "hello", GROUP)
        with mock.patch.object(inbound, "group_link", new=mock.AsyncMock()):
            await inbound.dispatch(bot, original)
        send.assert_not_awaited()  # ordinary group messages never auto-relay
        command = event(5, "/转发 1", GROUP)
        await inbound.dispatch(bot, command)
        assert send.await_args.kwargs["groups"] == ["personal:888888888"]
        assert send.await_args.kwargs["channel_id"].startswith("qq_manual:")
        assert send.await_args.kwargs["message_id"] == "5"
        await inbound.dispatch(bot, command)
        assert send.await_count == 1  # duplicate inbound event
        assert inbound.content_event(bot, event(6, "/转发 1", GROUP + 1)) is None
        private_original = event(60, "private secret")
        inbound.remember(bot, private_original)
        other_private = event(61, "/转发 1", user=333333333,
            message=[{"type": "reply", "data": {"id": "60"}}, {"type": "text", "data": {"text": "/转发 1"}}])
        assert inbound.content_event(bot, other_private) is None
        # Adapter strips reply segment after resolving it: cached conversation still governs access.
        quoted = event(7, "/转发 1", GROUP)
        quoted.reply = SimpleNamespace(message_id=4)
        assert inbound.content_event(bot, quoted) is original
        async def revoke(_):
            authorized[0] = False
            return [], []
        with mock.patch.object(inbound, "prepare", side_effect=revoke):
            await inbound.dispatch(bot, event(8, "/转发 1", GROUP))
        assert send.await_count == 1
        authorized[0] = True
        with mock.patch("plugins.manage._write_restart_pending_async", new=mock.AsyncMock()) as marker, \
                mock.patch("plugins.manage._panel_post", new=mock.AsyncMock(return_value={"ok": True})) as restart:
            await inbound.dispatch(bot, event(14, "/重启"))
            marker.assert_awaited_once_with("personal:" + str(USER), "personal:" + UIN)
            restart.assert_awaited_once_with("/api/bot/restart")
        import time
        with mock.patch("plugins.manage._load_restart_pending_async", new=mock.AsyncMock(return_value={
                "openid": "personal:" + str(USER), "bot_id": "personal:" + UIN, "time": time.time()})), \
                mock.patch("plugins.manage._clear_restart_pending_async", new=mock.AsyncMock()) as clear:
            await inbound.confirm_restart(bot)
            bot.send_private_msg.assert_awaited_once_with(user_id=USER, message="重启完毕")
            clear.assert_awaited_once()
        wrong = SimpleNamespace(self_id="333333333")
        assert inbound.targets(wrong, event(9, "/转发 列表", GROUP)) == []
        # Passive link parsing respects the existing group + bot binding gate.
        from plugins import bilibili_auto_resolve as links
        with mock.patch.object(links, "_read_gate", return_value=False), \
                mock.patch.object(links, "_resolve_and_send", new=mock.AsyncMock()) as resolve:
            await inbound.group_link(bot, event(10, "https://www.bilibili.com/video/BV1234567890", GROUP), sender)
            resolve.assert_not_awaited()
        with mock.patch.object(links, "_read_gate", return_value=True), \
                mock.patch.object(links, "_resolve_and_send", new=mock.AsyncMock()) as resolve:
            await inbound.group_link(bot, event(11, "https://www.bilibili.com/video/BV1234567890", GROUP), sender)
            resolve.assert_awaited_once()
        with mock.patch("plugins.media.fetch_bytes", new=mock.AsyncMock(return_value=b"test")):
            parts, media = await inbound.prepare(event(12, "", message=[{"type": "image", "data": {"url": "https://example.org/image.png"}}]))
            assert not parts and media[0]["segment"].type == "file_image"
        try:
            await inbound.prepare(event(13, "", message=[{"type": "image", "data": {"file": "file:///secret"}}]))
        except ValueError:
            pass
        else:
            raise AssertionError("local file accepted")
        inbound.clear_connection(UIN)
        assert inbound.content_event(bot, quoted) is None
    gateway._ready.clear()
    print("PASS - registration never grants access, commands require authorization, manual relay is explicit and rechecks grants")
    print("PASS - duplicate events, conversation boundaries, ownership, media paths and passive link gates")

    message = dict(id="timer-test", content="hello", target_mode="groups", target_groups=["personal:888888888"])
    with mock.patch.object(timer, "get_qq_group_entries", return_value=entries), \
            mock.patch.object(timer, "get_qq_forwarding_groups", return_value=[]), \
            mock.patch.object(timer, "get_active_groups", return_value=[e["openid"] for e in entries]), \
            mock.patch.object(timer, "get_scheduled_message_config", return_value=dict(enabled=True, target_groups=[], messages=[message])), \
            mock.patch.object(timer, "next_scheduled_at", return_value=datetime.now() - timedelta(seconds=1)), \
            mock.patch.object(timer, "send_relay_message", new=mock.AsyncMock(return_value={"personal:888888888": True})) as send:
        assert timer._message_target_groups(message) == ["personal:888888888"]
        async def stop(_):
            raise asyncio.CancelledError()
        with mock.patch.object(timer.asyncio, "sleep", side_effect=stop):
            try:
                await timer._scheduled_message_loop()
            except asyncio.CancelledError:
                pass
        assert send.await_args.kwargs["channel_id"] == "scheduled:timer-test"
        assert send.await_args.kwargs["message_id"]
    print("PASS - personal scheduled targets and stable per-occurrence delivery identities")


if __name__ == "__main__":
    asyncio.run(main())
