"""验证管理面板的认证、Origin、防坏配置和 PID 边界。

不启动 Uvicorn、不修改真实 settings；用法：
.venv/Scripts/python.exe scripts/test_panel_safety.py
"""
import asyncio
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "panel" / "管理面板.pyw"
_TEST_STDOUT = sys.stdout
spec = importlib.util.spec_from_file_location("wrelay_panel_safety", PANEL_PATH)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
# 面板模块为记录日志会接管 sys.stdout；测试结束后恢复控制台输出。
sys.stdout = _TEST_STDOUT


def make_request(path, headers=None):
    raw_headers = [
        (str(k).lower().encode(), str(v).encode())
        for k, v in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": raw_headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("127.0.0.1", 8090),
        "client": ("127.0.0.1", 50000),
    })


async def pass_through(_request):
    return Response("ok", status_code=200)


def check_guard(path, headers):
    return asyncio.run(panel.origin_guard(
        make_request(path, headers), pass_through
    ))


def main():
    assert check_guard("/api/auth/bootstrap", {}).status_code == 200
    assert check_guard(
        "/api/auth/bootstrap", {"Origin": "http://evil.example"}
    ).status_code == 403
    assert check_guard("/api/status", {}).status_code == 401
    assert check_guard(
        "/api/status", {"X-WRelay-Token": panel.PANEL_TOKEN}
    ).status_code == 200
    print("PASS - 面板 Origin 与 API 令牌边界")

    valid_settings = panel.get_default_settings()
    valid_settings.update({
        "qq_group_openids": [],
        "qq_user_openids": [],
        "discord_channels": [],
    })
    assert panel._validate_settings(valid_settings) is None
    assert panel._validate_settings({"qq_group_openids": {}})
    assert panel._validate_settings({
        "qq_group_openids": [],
        "qq_user_openids": [],
        "discord_channels": [],
        "backfill_limit": 0,
    })
    print("PASS - 配置结构校验拒绝非法载荷")

    with tempfile.TemporaryDirectory(prefix="wrelay_panel_test_") as tmp:
        path = Path(tmp) / "settings.json"
        old = panel.SETTINGS_FILE
        try:
            panel.SETTINGS_FILE = str(path)
            path.write_text(json.dumps({"v": 1}), encoding="utf-8")
            first = panel._settings_revision()
            path.write_text(json.dumps({"v": 2, "padding": "changed"}), encoding="utf-8")
            second = panel._settings_revision()
        finally:
            panel.SETTINGS_FILE = old
        assert first != second
    print("PASS - settings revision 能识别外部修改")

    assert panel._proc_exists(0) is False
    assert panel._proc_exists(2 ** 31 - 1) is False
    print("PASS - PID 无效值不会被判定为运行中")


if __name__ == "__main__":
    main()
