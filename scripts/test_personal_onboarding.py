"""Personal onboarding startup and welcome detection in isolated files."""
import ast
import os
import shutil
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
from plugins import personal_qq_config


def load_handler(name, namespace):
    tree = ast.parse((ROOT / "panel/onboarding_api.py").read_text(encoding="utf-8"))
    install = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install_onboarding")
    handler = next(node for node in install.body if isinstance(node, ast.FunctionDef) and node.name == name)
    handler.decorator_list = []
    exec(compile(ast.Module(body=[handler], type_ignores=[]), "onboarding_api.py", "exec"), namespace)
    return namespace[name]


def main():
    with tempfile.TemporaryDirectory(prefix="wrelay-personal-guide-") as tmp:
        root = Path(tmp)
        (root / ".env.example").write_bytes(b"TEMPLATE=1\n")
        starts = []
        panel = SimpleNamespace(PROJECT_ROOT=tmp, ENV_PROD=str(root / "test-config"),
                                _ENV_WRITE_LOCK=threading.RLock(), bot_status=lambda: {"running": False},
                                start_bot=lambda: starts.append(True) or {"ok": True}, load_settings=lambda: {})
        scope = {"p": panel, "os": os, "shutil": shutil, "error": lambda message, code=400: {"ok": False, "msg": message},
                 "state": lambda: {}, "public_credentials": lambda saved: {"bots": [], "discord_configured": False},
                 "OnboardingStateError": RuntimeError}
        start = load_handler("personal_start", scope)
        welcome = load_handler("get_state", scope)
        with patch.object(personal_qq_config, "enabled_connections", return_value={}):
            assert start()["ok"] is False and not Path(panel.ENV_PROD).exists()
        with patch.object(personal_qq_config, "enabled_connections", return_value={"1": {}}):
            assert start()["ok"] and Path(panel.ENV_PROD).read_bytes() == b"TEMPLATE=1\n"
            Path(panel.ENV_PROD).write_bytes(b"EXISTING\n")
            assert start()["ok"] and Path(panel.ENV_PROD).read_bytes() == b"EXISTING\n"
            panel.bot_status = lambda: {"running": True}
            assert start()["already_running"] and len(starts) == 2
        with patch.object(personal_qq_config, "public_config", return_value={"connections": []}):
            assert welcome()["show_welcome"] is True
            panel.load_settings = lambda: {"youtube_sources": [{"id": "y"}]}
            assert welcome()["show_welcome"] is False
            panel.load_settings = lambda: {"steam_sources": [{"id": "s"}]}
            assert welcome()["show_welcome"] is False
        panel.load_settings = lambda: {}
        with patch.object(personal_qq_config, "public_config", return_value={"connections": [{"uin": "12345"}]}):
            assert welcome()["show_welcome"] is False
    print("PASS - personal onboarding startup and welcome detection")


if __name__ == "__main__":
    main()
