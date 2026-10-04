"""隔离向导 UI 预览：所有账号、注册、连接和 RSS 响应均为示例。

运行后访问 http://127.0.0.1:18109/?desktop=1；Ctrl+C 退出。
只写临时目录，不读取真实 .env.prod，不启动 Bot 或连接外部服务。
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import httpx
    import uvicorn
    import plugins.paths as paths
    with tempfile.TemporaryDirectory(prefix="wrelay_setup_preview_") as tmp:
        paths.DATA_DIR = Path(tmp)
        out, err = sys.stdout, sys.stderr
        spec = importlib.util.spec_from_file_location("wrelay_setup_preview", ROOT / "panel" / "管理面板.pyw")
        panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(panel)
        sys.stdout, sys.stderr = out, err
        panel._panel_log._file.close()
        panel._log = lambda *args: None
        panel.PROJECT_ROOT = tmp
        from fastapi.staticfiles import StaticFiles
        panel.app.mount("/", StaticFiles(directory=ROOT / "panel" / "web", html=True), name="preview-web")
        panel.ENV_PROD = str(Path(tmp, "preview.env"))
        Path(tmp, ".env.example").write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        runtime = {"running": False, "pid": None, "managed": False}
        panel.bot_status = lambda: dict(runtime)
        panel._bot_health_cached = lambda: {"ok": True, "qq": runtime["running"], "qq_bots": ["1001"] if runtime["running"] else [], "discord": runtime["running"], "retry": {}}
        panel.get_autostart = lambda: False
        panel.api_message_trace = lambda *args: {"ok": True, "entries": []}
        audit = {"ok": True, "audit": {"readable": [{"id": "123", "name": "示例频道", "guild": "测试服务器"}]}}
        panel.api_channels_audit = lambda: audit
        panel.bot_api_get = lambda *args, **kwargs: {"ok": True, "entries": [], "names": {}, "audit": audit["audit"]}
        panel.bot_api_post = lambda *args, **kwargs: audit

        def start():
            runtime.update(running=True, pid=12345, managed=True)
            panel.atomic_write_json(panel.REGISTRATIONS_FILE, {
                "users": {"user1": {"nickname": "示例用户", "qq_id": "123456789"}},
                "groups": {"group1": {"group_name": "示例接收群", "qq_id": "987654321", "bot_id": "1001", "operator_openid": "user1"}},
            })
            return {"ok": True, "pid": 12345}

        panel.start_bot = start
        panel.restart_bot = start
        client = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<rss><channel><title>示例来源</title></channel></rss>"))
        httpx.AsyncClient = lambda **kwargs: client(transport=transport, **kwargs)
        try:
            print("Isolated onboarding preview: http://127.0.0.1:18109/?desktop=1", flush=True)
            uvicorn.run(panel.app, host="127.0.0.1", port=18109, log_level="warning", access_log=False)
        finally:
            httpx.AsyncClient = client


if __name__ == "__main__":
    main()
