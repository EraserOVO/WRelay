"""In-process management API dispatch and Windows desktop/bot IPC."""

import asyncio
import base64
import binascii
import hashlib
import os
import json
import threading
from multiprocessing.connection import Client, Listener

import httpx

from plugins.paths import COMPILED, EDITION, PROJECT_ROOT, data_path
from plugins.json_io import load_json


def compiled_pipe_address(project_root, edition="test"):
    """Return a stable, install-specific management pipe for a compiled copy."""
    root = os.path.normcase(os.path.abspath(os.fspath(project_root)))
    identity = hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]
    prefix = "WRelayManager-" if edition == "release" else "WRelayTestManager-"
    return "\\\\.\\pipe\\" + prefix + identity


PIPE_ADDRESS = compiled_pipe_address(PROJECT_ROOT, EDITION) if COMPILED else r"\\.\pipe\WRelayManager"
TOKEN_FILE = data_path("panel_auth.json")
DEFAULT_RESPONSE_TIMEOUT = 12.0
BACKFILL_RESPONSE_TIMEOUT = 35.0


def _token():
    data = load_json(TOKEN_FILE, default=None)
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise ConnectionError("桌面管理程序尚未启动")
    return token.encode("utf-8")


def dispatch(app, token, method, path, headers=None, body_base64=""):
    """Run an existing FastAPI route without opening a listening socket."""
    if method not in ("GET", "POST") or not isinstance(path, str) or not path.startswith("/api/") or "//" in path:
        return {"status": 400, "headers": {"content-type": "application/json"},
                "body": base64.b64encode(json.dumps({"ok": False, "msg": "无效请求"}).encode("utf-8")).decode("ascii")}
    try:
        body = base64.b64decode(body_base64, validate=True) if body_base64 else b""
    except (ValueError, binascii.Error):
        return {"status": 400, "headers": {"content-type": "application/json"},
                "body": base64.b64encode(json.dumps({"ok": False, "msg": "无效请求体"}).encode("utf-8")).decode("ascii")}
    safe_headers = {str(k): str(v) for k, v in (headers or {}).items()
                    if str(k).lower() in ("content-type", "accept")}
    safe_headers["X-WRelay-Token"] = token

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.request(method, path, headers=safe_headers, content=body)

    response = asyncio.run(run())
    return {"status": response.status_code,
            "headers": {"content-type": response.headers.get("content-type", "application/octet-stream"),
                        "content-disposition": response.headers.get("content-disposition", "")},
            "body": base64.b64encode(response.content).decode("ascii")}


class ManagerPipeServer:
    def __init__(self, app, token):
        self.app = app
        self.token = token
        self.listener = None

    def start(self):
        self.listener = Listener(PIPE_ADDRESS, family="AF_PIPE", authkey=self.token.encode("utf-8"))
        threading.Thread(target=self._accept, name="wrelay-manager-pipe", daemon=True).start()

    def _accept(self):
        while True:
            try:
                connection = self.listener.accept()
            except (OSError, EOFError):
                return
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection):
        try:
            request = connection.recv()
            if not isinstance(request, dict):
                raise ValueError("无效管理请求")
            result = dispatch(self.app, self.token, request.get("method"),
                              request.get("path"), request.get("headers"), request.get("body", ""))
            connection.send(result)
        except Exception as exc:
            try:
                connection.send({"status": 500, "headers": {"content-type": "application/json"},
                                 "body": base64.b64encode(json.dumps({"ok": False, "msg": str(exc)}).encode()).decode("ascii")})
            except (OSError, EOFError):
                pass
        finally:
            connection.close()

    def close(self):
        if self.listener is not None:
            self.listener.close()


def request_manager(method, path, body=None):
    """Call the desktop manager from the separate QQ bot process."""
    content = json.dumps(body).encode("utf-8") if body is not None else b""
    timeout = BACKFILL_RESPONSE_TIMEOUT if path.startswith("/api/backfill/") else DEFAULT_RESPONSE_TIMEOUT
    with Client(PIPE_ADDRESS, family="AF_PIPE", authkey=_token()) as connection:
        connection.send({"method": method, "path": path,
                         "headers": {"content-type": "application/json"},
                         "body": base64.b64encode(content).decode("ascii")})
        if not connection.poll(timeout):
            raise TimeoutError("桌面管理操作响应超时")
        result = connection.recv()
    try:
        data = json.loads(base64.b64decode(result["body"]))
    except (KeyError, ValueError, TypeError):
        data = {"ok": False, "msg": "桌面管理程序返回无效响应"}
    if not isinstance(data, dict):
        data = {"ok": False, "msg": "桌面管理程序返回无效响应"}
    if result.get("status", 500) >= 400:
        data.setdefault("ok", False)
        data.setdefault("msg", "管理操作失败")
    return data
