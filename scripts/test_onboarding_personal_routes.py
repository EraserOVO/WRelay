"""Personal QQ support in the Discord, X and Bilibili onboarding paths."""
import ast
from copy import deepcopy
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panel.onboarding import apply_draft, clean_draft, source_spec
from plugins import bot_profiles, personal_qq_config
from plugins.startup_config import discord_ready
from unittest.mock import patch


def make_draft(purpose, value):
    return clean_draft({"purpose": purpose, "step": "4", "bot_id": "personal:12345",
                        "source_value": value, "source_kind": "up", "rsshub": "http://127.0.0.1:1200",
                        "groups": ["personal:22222"]})


def test_routes():
    original = {"qq_group_openids": [{"openid": "personal:22222", "bot_id": "personal:12345", "enabled": False},
                                      {"openid": "personal:33333", "bot_id": "personal:54321", "enabled": False}],
                "discord_channels": [], "x_rss_sources": [], "bilibili_sources": [],
                "forwarding_groups": [], "x_forwarding_groups": [], "bilibili_forwarding_groups": []}
    for purpose, value, sources, routes in (
        ("discord", "12345", "discord_channels", "forwarding_groups"),
        ("x", "example", "x_rss_sources", "x_forwarding_groups"),
        ("bilibili", "12345", "bilibili_sources", "bilibili_forwarding_groups"),
    ):
        draft = make_draft(purpose, value)
        result = apply_draft(original, draft, "test", ["personal:12345", "personal:54321"])
        assert len(result[sources]) == 1 and result[sources][0]["enabled"]
        assert result[routes][0]["groups"] == ["personal:22222"]
        assert result["qq_group_openids"][0]["enabled"] and not result["qq_group_openids"][1]["enabled"]
        assert apply_draft(result, draft, "test", ["personal:12345", "personal:54321"]) == result
        wrong = deepcopy(draft)
        wrong["groups"] = ["personal:33333"]
        try:
            apply_draft(original, wrong, "test", ["personal:12345", "personal:54321"])
        except ValueError:
            pass
        else:
            raise AssertionError("another account's group must not be accepted")
    assert not any(original[key] for key in ("discord_channels", "x_rss_sources", "bilibili_sources"))


def test_discord_credentials():
    source = Path(__file__).resolve().parents[1] / "panel/onboarding_api.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    install = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install_onboarding")
    handler = next(node for node in install.body if isinstance(node, ast.FunctionDef) and node.name == "save_credentials")
    handler.decorator_list = []
    with tempfile.TemporaryDirectory(prefix="wrelay-guide-discord-") as tmp:
        root = Path(tmp)
        (root / ".env.example").write_text("template", encoding="utf-8")
        state_data, writes = {}, []
        bots = [{"token": ""}, {"token": "existing-second", "name": "keep"}]
        panel = SimpleNamespace(ENV_PROD=str(root / "test-config"), PROJECT_ROOT=tmp,
                                _ENV_WRITE_LOCK=threading.RLock(),
                                _read_discord_bots=lambda: (deepcopy(bots), 2, ["placeholder"], 2),
                                _write_discord_bots=lambda *args: writes.append(args),
                                atomic_write_json=lambda path, state: None)
        namespace = {"clean_draft": clean_draft, "p": panel, "state": lambda: state_data,
                     "public_credentials": lambda: {"discord_configured": True}, "path": lambda: "test",
                     "lock": threading.RLock(), "os": os, "shutil": shutil, "discord_ready": discord_ready}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), "exec"), namespace)
        save = namespace["save_credentials"]
        with patch.object(personal_qq_config, "enabled_connections", return_value={"12345": {}}):
            response = save({"draft": make_draft("discord", "12345"), "token": "new-token"})
            assert response["ok"] and state_data["restart_needed"]
            assert writes[0][0][0]["token"] == "new-token" and writes[0][0][1] == bots[1]
            for body in ({"draft": make_draft("x", "example"), "token": "new-token"},
                         {"draft": make_draft("discord", "12345"), "secret": "unexpected"}):
                try:
                    save(body)
                except ValueError:
                    continue
                raise AssertionError("invalid credentials request accepted")
            with patch.object(panel, "_read_discord_bots", return_value=([{"token": ""}], 2, ["placeholder"], 2)):
                try:
                    save({"draft": make_draft("discord", "12345"), "token": ""})
                except ValueError:
                    pass
                else:
                    raise AssertionError("missing Discord token accepted")
        with patch.object(personal_qq_config, "enabled_connections", return_value={}):
            try:
                save({"draft": make_draft("discord", "12345"), "token": "new-token"})
            except ValueError:
                pass
            else:
                raise AssertionError("disabled personal QQ accepted")
        assert len(writes) == 1


def test_apply_handler():
    source = Path(__file__).resolve().parents[1] / "panel/onboarding_api.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    install = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install_onboarding")
    handler = next(node for node in install.body if isinstance(node, ast.FunctionDef) and node.name == "apply_configuration")
    saved = {"flow_id": "test", "status": "draft"}
    writes = []
    data = {"qq_group_openids": [{"openid": "personal:22222", "bot_id": "personal:12345", "enabled": False}],
            "discord_channels": [], "x_rss_sources": [], "bilibili_sources": [],
            "forwarding_groups": [], "x_forwarding_groups": [], "bilibili_forwarding_groups": []}
    panel = SimpleNamespace(SETTINGS_FILE="test", _load_settings_snapshot=lambda: (deepcopy(data), "r"),
                            api_channels_audit=lambda: {"audit": {"readable": [{"id": "12345"}]}},
                            _validate_settings=lambda value: None, api_message_trace=lambda limit: {"entries": []},
                            save_settings=lambda value, expected_revision: writes.append(value),
                            atomic_write_json=lambda *args: None)
    creds = {"bots": [], "personal_bots": [{"appid": "personal:12345"}], "discord_configured": True}
    namespace = {"clean_draft": clean_draft, "apply_draft": apply_draft, "source_spec": source_spec,
                 "p": panel, "lock": threading.RLock(), "state": lambda: saved,
                 "public_credentials": lambda: creds, "error": lambda message, code=400: {"ok": False},
                 "path": lambda: "test", "time": SimpleNamespace(time=lambda: 100)}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), "exec"), namespace)
    with patch.object(bot_profiles, "ids", return_value=["personal:12345"]), bot_profiles.scope("personal:12345"):
        for purpose, value in (("discord", "12345"), ("x", "example"), ("bilibili", "12345")):
            saved.update(status="draft", applied_at=0)
            result = namespace["apply_configuration"]({"draft": make_draft(purpose, value), "flow_id": "test", "revision": "r"})
            assert result["ok"] and writes[-1]["qq_group_openids"][0]["enabled"]
        creds["personal_bots"] = []
        try:
            namespace["apply_configuration"]({"draft": make_draft("x", "example"), "flow_id": "test", "revision": "r"})
        except ValueError:
            pass
        else:
            raise AssertionError("disabled personal QQ may not apply a route")
    assert len(writes) == 3


if __name__ == "__main__":
    test_routes()
    test_discord_credentials()
    test_apply_handler()
    print("PASS - personal QQ routes and Discord credential setup")
