"""Global, per-service outbound proxy settings for WRelay."""

import ipaddress
import threading
from urllib.parse import urlsplit

from plugins.json_io import atomic_write_json, load_json
from plugins.paths import data_path


NETWORK_PROXY_FILE = data_path("network_proxies.json")
PROXY_KEYS = (
    "discord",
    "x",
    "rsshub",
    "bilibili",
    "youtube",
    "steam",
    "translation",
)
LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain"})
LOCAL_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".lan",
    ".home.arpa",
    ".internal",
    ".intranet",
)
_LOCK = threading.RLock()


def _defaults():
    return {key: "" for key in PROXY_KEYS}


def validate_proxy_url(value, *, field_name="代理"):
    """Normalize a blank or HTTP(S) forward-proxy URL without logging it."""
    if not isinstance(value, str):
        raise ValueError(field_name + "必须是文本")
    value = value.strip()
    if not value:
        return ""
    if len(value) > 512:
        raise ValueError(field_name + "地址过长")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        _port = parts.port
    except ValueError as exc:
        raise ValueError(field_name + "地址格式无效") from exc
    if parts.scheme.lower() not in ("http", "https") or not hostname:
        raise ValueError(field_name + "仅支持 http:// 或 https:// 代理地址")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError(field_name + "地址不能包含路径、查询参数或片段")
    return value.rstrip("/")


def read_network_proxies():
    with _LOCK:
        raw = load_json(NETWORK_PROXY_FILE, default={})
        if not isinstance(raw, dict):
            raw = {}
        result = _defaults()
        for key in PROXY_KEYS:
            value = raw.get(key, "")
            if isinstance(value, str):
                try:
                    result[key] = validate_proxy_url(value, field_name=key)
                except ValueError:
                    result[key] = ""
        return result


def get_proxy(service):
    if service not in PROXY_KEYS:
        return None
    return read_network_proxies().get(service) or None


def save_network_proxies(values):
    if not isinstance(values, dict):
        raise ValueError("代理配置必须是对象")
    unknown = set(values) - set(PROXY_KEYS)
    if unknown:
        raise ValueError("代理配置包含未知链路")
    with _LOCK:
        result = read_network_proxies()
        for key, value in values.items():
            result[key] = validate_proxy_url(value, field_name=key)
        changed = result != read_network_proxies()
        if changed:
            atomic_write_json(NETWORK_PROXY_FILE, result, indent=2)
        return changed


def is_local_url(url):
    """Keep loopback, private-IP and local service-name traffic off proxies."""
    try:
        host = (urlsplit(str(url or "")).hostname or "").strip("[]").lower()
        if (
            not host
            or host in LOCAL_HOSTS
            or host.endswith(LOCAL_HOST_SUFFIXES)
        ):
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return "." not in host
        return address.is_private or address.is_loopback
    except ValueError:
        return False


def proxy_for_url(service, url):
    """Resolve a service proxy while always bypassing local RSSHub endpoints."""
    if is_local_url(url):
        return None
    return get_proxy(service)
