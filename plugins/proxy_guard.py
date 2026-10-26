"""Discord 专用代理端口自检。

Clash Verge 重启后混合端口可能变化，而 .env.prod 的 DISCORD_PROXY 不会自动
跟随；端口失效时 Discord 适配器连网关都拿不到，且只尝试一次。这里只在
DISCORD_PROXY 指向本机且端口不可用时，改用 Windows 系统代理的当前端口。
"""

import os
import socket
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PROXY_SCHEMES = frozenset({"http", "https"})


def loopback_http_proxy(proxy):
    """返回本机 HTTP 代理的 (host, port)；非本机或非 HTTP 代理返回 None。"""
    try:
        parts = urlsplit(str(proxy or "").strip())
        if parts.scheme not in PROXY_SCHEMES or not parts.hostname:
            return None
        if parts.hostname not in LOOPBACK_HOSTS:
            return None
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return None
    return parts.hostname, port


def port_is_open(host, port, timeout=0.4):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def system_loopback_http_proxy():
    """Windows 系统代理仍是本机 HTTP 代理时返回其 URL（含当前端口）。"""
    try:
        import urllib.request

        proxies = urllib.request.getproxies_registry()
    except Exception:
        return None
    for scheme in ("https", "http"):
        candidate = proxies.get(scheme)
        if candidate and loopback_http_proxy(candidate):
            return candidate
    return None


def align_discord_proxy(environ=None, probe=None, system_proxy=None):
    """DISCORD_PROXY 的本机端口失效时，跟随系统代理当前端口。

    返回 (旧地址, 新地址) 便于记录日志；未调整时返回 None。
    远程代理无法探测，保持原样；没有可用的系统代理时也不改动。
    """
    environ = os.environ if environ is None else environ
    probe = port_is_open if probe is None else probe
    configured = str(environ.get("DISCORD_PROXY") or "")
    target = loopback_http_proxy(configured)
    if target is None or probe(*target):
        return None
    candidate = system_proxy() if system_proxy else system_loopback_http_proxy()
    live = loopback_http_proxy(candidate)
    if live is None or not probe(*live):
        return None
    environ["DISCORD_PROXY"] = str(candidate).strip()
    return f"{target[0]}:{target[1]}", f"{live[0]}:{live[1]}"
