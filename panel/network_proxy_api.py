"""Local management routes for validating and testing outbound proxies."""

import time

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from plugins.network_proxy import (
    read_network_proxies,
    save_network_proxies,
    validate_proxy_url,
)
from plugins.proxy_guard import (
    loopback_http_proxy,
    port_is_open,
    system_loopback_http_proxy,
)


TEST_TARGETS = {
    "discord": ("https://discord.com/api/v10/gateway", "Discord API"),
    "x": (
        "https://syndication.twitter.com/srv/timeline-profile/screen-name/snekflat",
        "X 时间线",
    ),
}


def register_routes(app, panel):
    @app.get("/api/network-proxy")
    def get_network_proxy_config():
        return {"ok": True, "proxies": read_network_proxies()}

    @app.get("/api/network-proxy/detect-system")
    def detect_system_proxy():
        proxy = system_loopback_http_proxy()
        if not proxy:
            return {
                "ok": False,
                "code": "system_proxy_not_configured",
                "msg": "未检测到 Windows 系统代理。请先在 Clash Verge 首页开启「系统代理」；如果使用 TUN 模式，可留空代理设置。",
            }
        target = loopback_http_proxy(proxy)
        if target is None or not port_is_open(*target):
            return {
                "ok": False,
                "code": "system_proxy_not_listening",
                "msg": "检测到 Windows 系统代理设置，但对应端口没有监听。请启动 Clash Verge 内核并检查端口设置。",
            }
        return {
            "ok": True,
            "proxy": proxy,
            "msg": "已检测到正在监听的 Windows 系统代理。",
        }

    @app.post("/api/network-proxy/save")
    async def save_network_proxy_config(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("请求内容必须是对象")
            changed = save_network_proxies(body.get("proxies"))
        except (ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "msg": str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({"ok": False, "msg": "代理配置无法保存"}, status_code=500)
        return {"ok": True, "changed": changed, "restart_required": changed}

    @app.post("/api/network-proxy/test")
    async def test_network_proxy(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("请求内容必须是对象")
            service = str(body.get("service") or "").strip()
            if service not in TEST_TARGETS:
                raise ValueError("当前只支持检测 Discord 和 X 代理")
            proxy = validate_proxy_url(body.get("proxy", ""), field_name=service)
            if not proxy:
                raise ValueError("请先填写该链路的代理地址")
        except (ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "msg": str(exc)}, status_code=400)

        target, label = TEST_TARGETS[service]
        started = time.perf_counter()
        headers = {"User-Agent": "WRelay proxy connectivity check"}
        if service == "x":
            headers["Accept"] = "text/html,application/xhtml+xml"
        try:
            async with httpx.AsyncClient(
                proxy=proxy,
                timeout=httpx.Timeout(12.0),
                follow_redirects=True,
                trust_env=False,
            ) as client:
                async with client.stream("GET", target, headers=headers) as response:
                    status = response.status_code
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            reachable = status < 500
            return {
                "ok": reachable,
                "status_code": status,
                "elapsed_ms": elapsed_ms,
                "msg": (
                    "%s 已收到 HTTP %s 响应（%s ms）" % (label, status, elapsed_ms)
                    if reachable
                    else "%s 返回 HTTP %s" % (label, status)
                ),
            }
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if loopback_http_proxy(proxy):
                message = (
                    "%s 无法连接到本机代理端口；请确认 Clash Verge 内核已启动，"
                    "且填写的是混合或 HTTP(S) 端口。"
                ) % label
            else:
                message = "%s 无法连接到代理服务器，请检查地址、端口和网络。" % label
            return {"ok": False, "msg": message}
        except Exception as exc:
            # Never echo the proxy URL: it may contain authentication details.
            return {
                "ok": False,
                "msg": "%s 连接失败：%s" % (label, type(exc).__name__),
            }
