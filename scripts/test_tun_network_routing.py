"""验证虚拟网卡模式会清除 WRelay 强制使用的 HTTP 代理环境变量。"""
import ast
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot.py"
MEDIA_PATH = ROOT / "plugins" / "media.py"
PROXY_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


def load_routing_function():
    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"), filename=str(BOT_PATH))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_use_tun_network_routing"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {"os": os}
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["_use_tun_network_routing"]


def load_media_proxy_function():
    tree = ast.parse(MEDIA_PATH.read_text(encoding="utf-8"), filename=str(MEDIA_PATH))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_get_proxy"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {}
    exec(compile(module, str(MEDIA_PATH), "exec"), namespace)
    return namespace["_get_proxy"]


def main():
    original = {key: os.environ.get(key) for key in PROXY_KEYS}
    try:
        for key in PROXY_KEYS:
            os.environ[key] = "http://127.0.0.1:7897"
        load_routing_function()()
        assert all(key not in os.environ for key in PROXY_KEYS)
        assert load_media_proxy_function()() is None
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("PASS - 虚拟网卡模式不强制本地 HTTP 代理")


if __name__ == "__main__":
    main()
