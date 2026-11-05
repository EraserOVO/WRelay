"""Isolated personal QQ gateway tests: no real account, server or production files."""
import asyncio
import ast
import json
import tempfile
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nonebot.adapters.qq import Message, MessageSegment as Segment
from nonebot.adapters.onebot.v11 import ActionFailed
from starlette.websockets import WebSocketDisconnect

nonebot.init(_env_file=None)

from plugins import personal_qq as gateway, personal_qq_config as config, delivery_dedup, sender
from plugins.json_io import file_revision
from plugins.settings_model import SettingsModel, validate_settings
from panel.personal_qq_api import register_routes

UIN, GROUP = "123456789", "personal:987654321"
TOKEN = "test-only-token-123456789"


def item(implementation="napcat"):
    return dict(uin=UIN, name="Test", implementation=implementation, enabled=True, token=TOKEN)


def check_config():
    result = config.save_config([item()], file_revision(config.CONFIG_FILE), set())
    assert "token" not in json.dumps(result)
    assert config.enabled_connections()[UIN]["token"] == TOKEN
    config.save_config([{**item("snowluma"), "token": ""}], result["revision"], set())
    assert config.enabled_connections()[UIN]["implementation"] == "snowluma"
    for items in ([item(), item()], [{**item(), "token": "short"}], [{**item(), "uin": "bad"}]):
        try:
            config.prepare_connections(items, {})
        except ValueError:
            pass
        else:
            raise AssertionError("invalid account accepted")
    try:
        config.save_config([], file_revision(config.CONFIG_FILE), {"personal:" + UIN})
    except ValueError:
        pass
    else:
        raise AssertionError("bound account removed")
    data = SettingsModel.default().to_dict()
    data["qq_group_openids"] = [dict(openid=GROUP, bot_id="personal:" + UIN, enabled=True)]
    assert validate_settings(data) is None
    data["qq_group_openids"][0]["bot_id"] = "official-appid"
    assert validate_settings(data)
    app = FastAPI()
    register_routes(app, {"bot_api_get": lambda *a, **kw: None, "BOT_PORT": 18082,
                          "_bound_qq_bot_ids": lambda: set()})
    client = TestClient(app)
    assert TOKEN not in client.get("/api/personal-qq/config").text
    assert client.get("/api/personal-qq/groups", params={"uin": "../health"}).json()["ok"] is False
    tree = ast.parse((Path(__file__).resolve().parents[1] / "panel" / "管理面板.pyw").read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ("_prepare_qq_bots", "_public_qq_bots")]
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "panel-account-check", "exec"), namespace)
    assert namespace["_prepare_qq_bots"]([], []) == []
    assert namespace["_public_qq_bots"]([{"id": "你的AppID"}]) == []
    print("PASS - private credentials, revisions, account validation and panel API")


def check_conversion():
    media = Message([Segment.text("hello"), Segment.file_image(b"image"),
                     Segment.file_audio(b"audio"), Segment.file_video(b"video")])
    result = gateway.convert_message(media)
    assert [part["type"] for part in result] == ["text", "image", "record", "video"]
    assert result[3]["data"]["file"] == "base64://dmlkZW8="
    assert gateway.convert_message(Message([Segment.image("https://example.org/a.png")]))[0]["type"] == "image"
    try:
        gateway.convert_message(Message([Segment.image("file:///private-file")]))
    except ValueError:
        pass
    else:
        raise AssertionError("local path forwarded")
    print("PASS - text/image/audio/video conversion and inaccessible path rejection")


async def check_delivery():
    bot = SimpleNamespace(self_id=UIN, send_group_msg=mock.AsyncMock(return_value={"message_id": 1}))
    personal = gateway.PersonalSender(bot)
    gateway._ready[UIN] = personal
    text = Message([Segment.text("hello")])
    for provider in ("napcat", "snowluma"):
        with mock.patch.object(config, "enabled_connections", return_value={UIN: item(provider)}):
            await personal.send_to_group(GROUP, text, "x_rss:test", "100")
    assert bot.send_group_msg.await_count == 1, "provider change resent completed message"
    delivery_dedup._volatile_deliveries.clear()
    gateway._send_locks.clear()
    await personal.send_to_group(GROUP, text, "x_rss:test", "100")
    assert bot.send_group_msg.await_count == 1, "persistent dedup did not survive cache reset"
    bot.send_group_msg.side_effect = TimeoutError("unknown")
    video = Message([Segment.file_video(b"video")])
    for _ in range(2):
        try:
            await personal.send_to_group(GROUP, video, "x_rss:test", "101")
        except gateway.DeliveryUnknown:
            pass
        else:
            raise AssertionError("unknown delivery was accepted")
    assert bot.send_group_msg.await_count == 2, "unknown delivery retried automatically"
    try:
        await personal.send_to_group(GROUP, Message([Segment.text("changed render")]), "x_rss:test", "101")
    except gateway.DeliveryUnknown:
        pass
    else:
        raise AssertionError("changed content bypassed pending uncertainty")
    assert bot.send_group_msg.await_count == 2
    key = next(iter(gateway.uncertain_records()))
    await gateway.resolve_uncertain(key, "delivered")
    await personal.send_to_group(GROUP, video, "x_rss:test", "101")
    assert bot.send_group_msg.await_count == 2
    bot.send_group_msg.side_effect = TimeoutError()
    try:
        await personal.send_to_group(GROUP, text, "x_rss:test", "102")
    except gateway.DeliveryUnknown:
        pass
    await gateway.resolve_uncertain(next(iter(gateway.uncertain_records())), "retry")
    bot.send_group_msg.side_effect = None
    await personal.send_to_group(GROUP, text, "x_rss:test", "102")
    bot.send_group_msg.side_effect = ActionFailed(status="failed", retcode=100, data=None)
    try:
        await personal.send_to_group(GROUP, text, "x_rss:test", "103")
    except ActionFailed:
        pass
    assert not gateway.uncertain_records(), "explicit failure incorrectly blocked retry"
    bot.send_group_msg.side_effect = None
    bot.send_group_msg.reset_mock()
    official = SimpleNamespace(self_id="official", send_to_group=mock.AsyncMock())
    with mock.patch.object(sender, "get_qq_bot", return_value=official), \
            mock.patch.object(sender, "get_qq_bots", return_value={"official": official}), \
            mock.patch.object(sender, "get_group_bot_ids", return_value={"G1": "official", GROUP: "personal:" + UIN}), \
            mock.patch.object(sender, "_record_trace", new=mock.AsyncMock()), \
            mock.patch.object(sender, "notify_delivery_failure", new=mock.AsyncMock()):
        for channel in ("123456789012345678", "x_rss:abc", "bilibili:abc", "youtube:abc",
                        "qq_manual:abc", "scheduled:abc", "personal-link:abc"):
            result = await sender.send_relay_message([Segment.text("hi")], [], ["G1", GROUP], channel, "200")
            assert result == {"G1": True, GROUP: True}
        before = bot.send_group_msg.await_count
        manual = await sender.send_relay_message([Segment.text("hi")], [], [GROUP])
        assert manual[GROUP] is False and bot.send_group_msg.await_count == before
    gateway._ready.clear()
    print("PASS - both providers, four automatic chains, mixed routing, restart dedup, unknown delivery and resolution")


async def check_connection():
    driver = nonebot.get_driver()
    adapter = gateway.PersonalQQAdapter(driver)
    request = SimpleNamespace(headers={"x-self-id": UIN, "authorization": "Bearer " + TOKEN})
    assert adapter._check_access_token(request) is None
    assert adapter._check_access_token(SimpleNamespace(headers={"x-self-id": "222222222", "authorization": "Bearer " + TOKEN})) is not None
    assert adapter._check_access_token(SimpleNamespace(headers={"x-self-id": UIN, "authorization": "Bearer wrong"})) is not None
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent
    assert isinstance(adapter.json_to_event(dict(time=1, self_id=int(UIN), post_type="message",
        message_type="private", sub_type="friend", message_id=1, user_id=222222222,
        message=[{"type": "text", "data": {"text": "/转发 1"}}], raw_message="/转发 1",
        font=0, sender={"user_id": 222222222})), PrivateMessageEvent)
    assert adapter.json_to_event({"post_type": "notice"}) is None
    socket = SimpleNamespace(close=mock.AsyncMock(), request=request)
    fake = SimpleNamespace(self_id=UIN, get_login_info=mock.AsyncMock(return_value={"user_id": "222222222"}))
    adapter.bots[UIN] = fake
    await adapter._verify_connection(UIN, socket)
    assert UIN not in gateway._ready
    socket.close.assert_awaited_once()
    fake.get_login_info.return_value = {"user_id": UIN}
    fake.get_version_info = mock.AsyncMock(return_value={"app_name": "SnowLuma", "app_version": "test"})
    fake.get_status = mock.AsyncMock(return_value={"online": True, "good": True})
    with mock.patch("plugins.backfill._backfill_wrapper", new=mock.AsyncMock()), \
            mock.patch("plugins.scheduled_message._start_scheduled_message_loop", new=mock.AsyncMock()), \
            mock.patch("plugins.personal_qq_inbound.confirm_restart", new=mock.AsyncMock()):
        task = asyncio.create_task(adapter._verify_connection(UIN, socket))
        for _ in range(100):
            if UIN in gateway._ready:
                break
            await asyncio.sleep(0.01)
        assert UIN in gateway._ready
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    duplicate = SimpleNamespace(request=request, close=mock.AsyncMock())
    gateway._connecting.add(UIN)
    await adapter._handle_ws(duplicate)
    duplicate.close.assert_awaited_once_with(1008, "Account already connected")
    gateway._connecting.clear()
    adapter.bots.clear()
    gateway._ready.clear()
    print("PASS - account/token checks, duplicate rejection, actual UIN verification and inbound isolation")


def check_websocket(client):
    # In-process ASGI connection exercises the actual adapter framing and echo store.
    headers = {"x-self-id": UIN, "authorization": "Bearer " + TOKEN}
    try:
        with client.websocket_connect("/onebot/v11/ws", headers={**headers, "authorization": "Bearer wrong"}):
            raise AssertionError("bad token admitted")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008
    with mock.patch("plugins.backfill._backfill_wrapper", new=mock.AsyncMock()), \
            mock.patch("plugins.scheduled_message._start_scheduled_message_loop", new=mock.AsyncMock()), \
            mock.patch("plugins.personal_qq_inbound.confirm_restart", new=mock.AsyncMock()):
        with client.websocket_connect("/onebot/v11/ws", headers=headers) as ws:
            for action, result in (
                ("get_login_info", {"user_id": int(UIN), "nickname": "test"}),
                ("get_version_info", {"app_name": "NapCat", "app_version": "test"}),
                ("get_status", {"online": True, "good": True}),
            ):
                request = ws.receive_json()
                assert request["action"] == action, request
                ws.send_json({"status": "ok", "retcode": 0, "data": result, "echo": request["echo"]})
            # Ask for another API action only once verification has published readiness.
            async def probe():
                for _ in range(100):
                    if UIN in gateway._ready:
                        return True
                    await asyncio.sleep(.01)
                return False
            assert ws.portal.call(probe)
            try:
                with client.websocket_connect("/onebot/v11/ws", headers=headers):
                    raise AssertionError("duplicate websocket admitted")
            except WebSocketDisconnect as exc:
                assert exc.code == 1008
            ws.send_json({"post_type": "notice", "notice_type": "friend_add"})
            ws.close()
            async def disconnected():
                for _ in range(100):
                    if UIN not in gateway._connecting:
                        return True
                    await asyncio.sleep(.01)
                return False
            assert ws.portal.call(disconnected)
    assert UIN not in gateway._ready and UIN not in nonebot.get_bots()
    print("PASS - real ASGI reverse WebSocket handshake, API echo, readiness and duplicate connection rejection")


async def check_source_gates():
    from plugins import x_rss, bilibili, youtube
    for module in (x_rss, bilibili, youtube):
        async def run(**kwargs):
            assert kwargs["has_connected_bot"]() is True
            with mock.patch.object(gateway, "any_connected", return_value=False):
                assert kwargs["has_connected_bot"]() is False
        with mock.patch.object(module, "get_bots", return_value={}), \
                mock.patch.object(gateway, "any_connected", return_value=True), \
                mock.patch.object(module, "run_rss_poll_loop", side_effect=run):
            await module._poll_loop()
    print("PASS - all three RSS loops accept personal-only connectivity and stop when disconnected")


def main():
    with tempfile.TemporaryDirectory(prefix="wrelay_personal_qq_") as directory:
        with mock.patch.object(config, "CONFIG_FILE", str(Path(directory) / "personal.json")), \
                mock.patch.object(gateway, "UNCERTAIN_FILE", str(Path(directory) / "uncertain.json")), \
                mock.patch.object(delivery_dedup, "DELIVERY_DEDUP_DB_FILE", str(Path(directory) / "delivery.sqlite3")):
            check_config()
            check_conversion()
            asyncio.run(check_delivery())
            asyncio.run(check_connection())
            with TestClient(nonebot.get_app()) as client:
                check_websocket(client)
            asyncio.run(check_source_gates())


if __name__ == "__main__":
    main()
