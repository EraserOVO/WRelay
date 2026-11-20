"""Focused regressions for per-user relay filters and Steam store links."""
import ast
import asyncio
import re
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.config import _user_c2c_relay_filter
from plugins.filter import DISCORD_MESSAGE_TYPES, STEAM_MESSAGE_TYPES, get_enabled_message_types
from plugins.settings_model import SettingsModel, validate_settings


def test_user_filters():
    key = "qq_c2c_relay_filter"
    settings = SettingsModel.default().to_dict()
    settings[key] = {"filter_message_types": ["文字"]}
    settings["qq_user_openids"] = [
        {"openid": "one", key: {"filter_message_types": ["图片"]}},
        {"openid": "two"},
        {"openid": "three", key: {"filter_message_types": []}},
    ]
    assert _user_c2c_relay_filter(settings, "one", key, DISCORD_MESSAGE_TYPES)["filter_message_types"] == ["图片"]
    assert _user_c2c_relay_filter(settings, "two", key, DISCORD_MESSAGE_TYPES)["filter_message_types"] == ["文字"]
    assert _user_c2c_relay_filter(settings, "three", key, DISCORD_MESSAGE_TYPES)["filter_message_types"] == []
    assert validate_settings(settings) is None
    settings["qq_user_openids"][0]["steam_c2c_relay_filter"] = {"filter_message_types": ["视频"]}
    assert "steam_c2c_relay_filter" in validate_settings(settings)


async def test_steam():
    source = (Path(__file__).resolve().parents[1] / "plugins/command.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [node for node in tree.body if isinstance(node, (ast.AsyncFunctionDef, ast.Assign)) and (
        isinstance(node, ast.AsyncFunctionDef) and node.name in {"_prepare_steam_relay_content", "_prepare_manual_relay_content"}
        or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_STEAM_STORE_URL" for t in node.targets)
    )]
    detail = AsyncMock(return_value={"name": "Game", "type": "game", "price": {"final_formatted": "¥ 10"}, "release": {"date": "2026"}})
    fake_steam = types.ModuleType("plugins.steam")
    fake_steam._details = detail
    old_steam = sys.modules.get("plugins.steam")
    sys.modules["plugins.steam"] = fake_steam
    class Failure(Exception): pass
    class Card:
        def __init__(self, *args, **kwargs): self.args, self.kwargs = args, kwargs
    class Content:
        def __init__(self, text, media, key, **kwargs): self.text, self.media, self.key, self.extra = text, media, key, kwargs
    scope = {"asyncio": asyncio, "re": re, "STEAM_MESSAGE_TYPES": STEAM_MESSAGE_TYPES,
             "get_enabled_message_types": get_enabled_message_types,
             "get_steam_c2c_relay_filter": lambda user: {"filter_message_types": ["文字"] if user == "one" else ["链接"]},
             "get_steam_polling_snapshot": lambda: {"region": "CN"}, "_ManualRelayContentError": Failure,
             "_ManualRelayContent": Content, "CardSource": Card, "make_text": lambda text: text,
             "logger": types.SimpleNamespace(warning=lambda *args: None),
             "is_discord_url": lambda text: False, "is_x_status_url": lambda text: False,
             "parse_manual_post_url": lambda text: False}
    try:
        exec(compile(ast.Module(body=selected, type_ignores=[]), "command.py", "exec"), scope)
        prepare = scope["_prepare_manual_relay_content"]
        url = "https://store.steampowered.com/app/123/Game/?x=1"
        one = await prepare({"content": url}, user="one")
        two = await prepare({"content": url}, user="two")
        assert one.text == ["Game\n价格：¥ 10\n发行日期：2026"]
        assert two.text == ["https://store.steampowered.com/app/123/"]
        assert one.extra["card_source"].args[3] == ""
        assert scope["_STEAM_STORE_URL"].fullmatch("https://store.steampowered.com.evil.test/app/123/") is None
        for detector, handler in (("is_discord_url", "_prepare_discord_relay_content"),
                                  ("is_x_status_url", "_prepare_x_relay_content"),
                                  ("parse_manual_post_url", "_prepare_bilibili_relay_content")):
            scope[detector] = lambda text: True
            scope[handler] = AsyncMock(return_value="ok")
            assert await prepare({"content": "link"}, user="one") == "ok"
            scope[handler].assert_awaited_once_with("link", "one")
            scope[detector] = lambda text: False
        detail.return_value["type"] = "dlc"
        try:
            await prepare({"content": url}, user="one")
            raise AssertionError("DLC page must not be accepted as a game")
        except Failure:
            pass
    finally:
        if old_steam is None: sys.modules.pop("plugins.steam", None)
        else: sys.modules["plugins.steam"] = old_steam


if __name__ == "__main__":
    test_user_filters()
    asyncio.run(test_steam())
    print("PASS - per-user filters and Steam store relay")
