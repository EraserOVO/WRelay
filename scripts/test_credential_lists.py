"""Isolated credential-list migration, masking and enablement checks."""
import ast
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.credential_entries import enabled_bots, parse_youtube_keys
from plugins.startup_config import discord_ready


class Response:
    def __init__(self, content, status_code=200):
        self.content, self.status_code = content, status_code


async def test_youtube_round_robin():
    source = Path(__file__).resolve().parents[1] / "plugins/youtube.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    wanted = {"_youtube_data_api_key", "_youtube_data_api_keys", "_youtube_data_api_get"}
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted]
    observed = []
    async def get(url, **kwargs):
        observed.append(kwargs["headers"]["X-Goog-Api-Key"])
        return SimpleNamespace(status_code=200, json=lambda: {"items": []})
    namespace = {"os": os, "asyncio": asyncio, "time": SimpleNamespace(monotonic=lambda: 100),
                 "_youtube_api_request_lock": asyncio.Lock(), "_youtube_api_cooldown_until": 0,
                 "_youtube_api_cooldown_reason": "", "_youtube_api_cooldown_rate_limited": False,
                 "_youtube_api_healthy": False, "_last_youtube_api_request_ts": 0,
                 "_youtube_api_key_index": 0, "_youtube_api_failure_count": 0,
                 "YOUTUBE_DATA_API_REQUEST_GAP": 0, "YOUTUBE_DATA_API_BASE": "https://example.invalid",
                 "FEED_TIMEOUT": 1, "_youtube_get": get, "YoutubeDataAPIError": RuntimeError}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    keys = [{"key": "first", "enabled": True}, {"key": "off", "enabled": False}, {"key": "second", "enabled": True}]
    with patch.dict(os.environ, {"YOUTUBE_DATA_API_KEYS": json.dumps(keys)}, clear=False):
        for _ in range(3): await namespace["_youtube_data_api_get"]("test", {})
    assert observed == ["first", "second", "first"]


async def test_discord_audit():
    import datetime
    from contextlib import redirect_stdout
    from io import StringIO
    source = Path(__file__).resolve().parents[1] / "scripts/diagnose_channels.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    main_node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "main")
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def get(self, url, headers):
            bot = headers["Authorization"].split()[-1]
            if url.endswith("/@me/guilds"):
                return SimpleNamespace(status_code=200, json=lambda: [{"id": "g", "name": "server"}])
            if url.endswith("/guilds/g/channels"):
                return SimpleNamespace(status_code=200, json=lambda: [{"id": bot, "name": bot, "type": 0}])
            return SimpleNamespace(status_code=200)
    snapshots = []
    with tempfile.TemporaryDirectory(prefix="wrelay-audit-") as tmp, redirect_stdout(StringIO()):
        namespace = {"load_token_proxy": lambda: (["one", "two"], None), "httpx": SimpleNamespace(AsyncClient=Client),
                     "asyncio": asyncio, "datetime": datetime, "os": os, "TEXT_TYPES": (0, 5),
                     "OUT_FILE": str(Path(tmp) / "audit.md"), "data_path": lambda _: str(Path(tmp) / "audit.json"),
                     "atomic_write_json": lambda path, data, **kwargs: snapshots.append(data)}
        exec(compile(ast.Module(body=[main_node], type_ignores=[]), str(source), "exec"), namespace)
        assert await namespace["main"]() == 0
    assert {item["id"] for item in snapshots[0]["readable"]} == {"one", "two"}


def test_disabled_discord_startup():
    from plugins.startup_config import needs_discord
    tree = ast.parse((Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_first_run_config_check")
    module_names = ["nonebot", "nonebot.adapters", "nonebot.adapters.qq", "nonebot.adapters.qq.config",
                    "nonebot.adapters.discord", "nonebot.adapters.discord.config"]
    modules = {name: types.ModuleType(name) for name in module_names}
    modules["nonebot.adapters.qq.config"].Config = type("QQ", (), {})
    modules["nonebot.adapters.discord.config"].Config = type("Discord", (), {})
    qq = {"id": "1001", "token": "1001", "secret": "configured"}
    def build(model):
        return SimpleNamespace(qq_bots=[qq]) if model.__name__ == "QQ" else SimpleNamespace(discord_bots=[])
    namespace = {"os": os, "sys": sys, "json": json, "enabled_bots": enabled_bots, "ENV_PROD": __file__,
                 "_build_adapter_config": build, "needs_discord": needs_discord,
                 "data_path": lambda name: name, "load_json": lambda *args, **kwargs: {"discord_channels": [{"enabled": True}]},
                 "_QQ_PLACEHOLDER_APPID": "你的AppID", "_QQ_PLACEHOLDER_APPSECRET": "你的AppSecret",
                 "_DC_PLACEHOLDER_TOKEN": "在这里粘贴你的DiscordBotToken"}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot", "exec"), namespace)
    with patch.dict(sys.modules, modules), patch.dict(os.environ, {"DISCORD_BOTS": json.dumps([{"token": "x", "enabled": False}])}):
        namespace["_first_run_config_check"]()
        assert os.environ["DISCORD_BOTS"] == "[]"


def main():
    assert enabled_bots([{"token": "a"}, {"token": "b", "enabled": False}]) == [{"token": "a"}]
    assert parse_youtube_keys(None, "legacy") == [{"key": "legacy", "enabled": True}]
    encoded = "base64:" + base64.b64encode(json.dumps([{"key": "a", "enabled": False}]).encode()).decode()
    assert parse_youtube_keys(encoded)[0]["enabled"] is False
    assert parse_youtube_keys("[]", "legacy") == []
    try:
        enabled_bots([{"enabled": "false"}])
    except ValueError:
        pass
    else:
        raise AssertionError("invalid enablement accepted")

    tree = ast.parse((Path(__file__).resolve().parents[1] / "panel/管理面板.pyw").read_text(encoding="utf-8"))
    names = {"api_bot_discord_config", "_save_discord_bot_config", "api_youtube_api_key_status", "_save_youtube_api_keys", "_prepare_qq_bots"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in nodes: node.decorator_list = []
    discord = [{"token": "old-secret", "enabled": True}]
    env = {"YOUTUBE_DATA_API_KEY": "legacy-secret"}
    def write_discord(bots, *_):
        discord[:] = bots
    namespace = {"json": json, "hashlib": hashlib, "os": os, "JSONResponse": Response,
                 "_ENV_WRITE_LOCK": threading.RLock(), "_read_discord_bots": lambda: (discord, 0, [""], 0),
                 "_write_discord_bots": write_discord, "discord_ready": discord_ready,
                 "_read_env_secret": lambda key: env.get(key, ""),
                 "_write_ai_env": lambda key, value: env.update({key: value})}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "panel", "exec"), namespace)
    get_discord, save_discord = namespace["api_bot_discord_config"], namespace["_save_discord_bot_config"]
    state = get_discord()
    assert "old-secret" not in json.dumps(state)
    request = {"revision": state["revision"], "entries": [{"id": "0", "token": "", "enabled": False},
                                                                {"id": "", "token": "new-secret", "enabled": True}]}
    assert save_discord(request)["ok"]
    assert discord == [{"token": "old-secret", "enabled": False}, {"token": "new-secret", "enabled": True}]
    assert save_discord(request).status_code == 409
    assert enabled_bots(discord) == [{"token": "new-secret"}]

    get_youtube, save_youtube = namespace["api_youtube_api_key_status"], namespace["_save_youtube_api_keys"]
    state = get_youtube()
    assert "legacy-secret" not in json.dumps(state)
    request = {"revision": state["revision"], "entries": [{"id": "0", "key": "", "enabled": False},
                                                                {"id": "", "key": "new-key", "enabled": True}]}
    with patch.dict(os.environ, {}, clear=False):
        assert save_youtube(request)["ok"]
        result = parse_youtube_keys(env["YOUTUBE_DATA_API_KEYS"])
        assert result == [{"key": "legacy-secret", "enabled": False}, {"key": "new-key", "enabled": True}]
        assert save_youtube(request).status_code == 409

    with patch("plugins.personal_qq_config.enabled_connections", return_value={"12345": {}}):
        result = namespace["_prepare_qq_bots"]([{"appid": "1001", "secret": "", "enabled": False}],
                                                [{"id": "1001", "token": "1001", "secret": "secret"}])
        assert result[0]["secret"] == "secret" and result[0]["enabled"] is False
        assert enabled_bots(result) == []
    asyncio.run(test_youtube_round_robin())
    asyncio.run(test_discord_audit())
    test_disabled_discord_startup()
    # Exercise the real dotenv writer and reader against a temporary file, not user credentials.
    import re
    import uuid
    env_nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in
                 {"_dotenv_line_value", "_valid_env_name", "_read_env_secret", "_write_ai_env"}]
    with tempfile.TemporaryDirectory(prefix="wrelay-credentials-") as tmp, patch.dict(os.environ, {}, clear=True):
        path = Path(tmp) / "temporary-config"
        path.write_text("OTHER=1\n", encoding="utf-8")
        env_scope = {"os": os, "json": json, "uuid": uuid, "ENV_PROD": str(path),
                     "_ENV_NAME_RE": re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$"), "_ENV_WRITE_LOCK": threading.RLock()}
        exec(compile(ast.Module(body=env_nodes, type_ignores=[]), "panel", "exec"), env_scope)
        value = "base64:" + base64.b64encode(json.dumps([{"key": "test-secret", "enabled": True}]).encode()).decode()
        env_scope["_write_ai_env"]("YOUTUBE_DATA_API_KEYS", value)
        assert parse_youtube_keys(env_scope["_read_env_secret"]("YOUTUBE_DATA_API_KEYS"))[0]["key"] == "test-secret"
    print("PASS - credential lists, masking, migration, stale writes and enabled accounts")


if __name__ == "__main__":
    main()
