"""验证 Discord 代理端口自检与网关启动重试的接线。"""
from pathlib import Path

from plugins.proxy_guard import align_discord_proxy, loopback_http_proxy

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot.py"
RECONNECT_PATH = ROOT / "plugins" / "discord_reconnect.py"


def test_loopback_detection():
    assert loopback_http_proxy("http://127.0.0.1:64509") == ("127.0.0.1", 64509)
    assert loopback_http_proxy(" http://localhost:7897 ") == ("localhost", 7897)
    assert loopback_http_proxy("http://[::1]:7897") == ("::1", 7897)
    assert loopback_http_proxy("socks5://127.0.0.1:7897") is None
    assert loopback_http_proxy("http://proxy.example.com:8080") is None
    assert loopback_http_proxy("") is None
    assert loopback_http_proxy("not a url") is None


def test_dead_port_follows_system_proxy():
    environ = {"DISCORD_PROXY": "http://127.0.0.1:64509"}
    probed = []

    def probe(host, port):
        probed.append((host, port))
        return port == 64430

    result = align_discord_proxy(
        environ=environ,
        probe=probe,
        system_proxy=lambda: "http://127.0.0.1:64430",
    )
    assert result == ("127.0.0.1:64509", "127.0.0.1:64430")
    assert environ["DISCORD_PROXY"] == "http://127.0.0.1:64430"
    assert probed == [("127.0.0.1", 64509), ("127.0.0.1", 64430)]


def test_live_port_is_kept():
    environ = {"DISCORD_PROXY": "http://127.0.0.1:64430"}
    called = []

    def system_proxy():
        called.append(True)
        return "http://127.0.0.1:64509"

    assert align_discord_proxy(environ=environ, probe=lambda h, p: True, system_proxy=system_proxy) is None
    assert environ["DISCORD_PROXY"] == "http://127.0.0.1:64430"
    assert called == []


def test_remote_proxy_is_untouched():
    environ = {"DISCORD_PROXY": "http://10.0.0.5:8080"}
    probed = []
    result = align_discord_proxy(
        environ=environ,
        probe=lambda h, p: probed.append((h, p)) or False,
        system_proxy=lambda: "http://127.0.0.1:64430",
    )
    assert result is None
    assert environ["DISCORD_PROXY"] == "http://10.0.0.5:8080"
    assert probed == []


def test_unusable_system_proxy_keeps_value():
    environ = {"DISCORD_PROXY": "http://127.0.0.1:64509"}
    assert align_discord_proxy(
        environ=environ,
        probe=lambda h, p: False,
        system_proxy=lambda: None,
    ) is None
    assert environ["DISCORD_PROXY"] == "http://127.0.0.1:64509"
    assert align_discord_proxy(
        environ=environ,
        probe=lambda h, p: False,
        system_proxy=lambda: "http://127.0.0.1:64430",
    ) is None
    assert environ["DISCORD_PROXY"] == "http://127.0.0.1:64509"


def test_startup_wiring():
    bot = BOT_PATH.read_text(encoding="utf-8")
    assert "align_discord_proxy()" in bot
    assert bot.index("align_discord_proxy()") < bot.index("\nnonebot.init()")
    assert "已跟随系统代理改为" in bot
    assert '"plugins.discord_reconnect"' in bot

    plugin = RECONNECT_PATH.read_text(encoding="utf-8")
    assert "RETRY_DELAYS = (15, 30, 60, 120, 300, 300, 300, 300)" in plugin
    assert "@driver.on_startup" in plugin
    assert "adapter.run_bot(bot_info)" in plugin
    # 冷启动时代理可能几分钟后才可用：重试前要重新对齐端口并同步给适配器
    assert "_refresh_proxy(adapter)" in plugin
    assert "align_discord_proxy()" in plugin
    assert "adapter.discord_config.discord_proxy" in plugin
    # 补发/媒体读取驱动配置，切换代理时必须一并更新，否则网关恢复但 REST 仍走旧端口
    assert "driver.config.discord_proxy = proxy" in plugin


def test_cached_clients_follow_proxy_switch():
    fetch = (ROOT / "plugins" / "fetch.py").read_text(encoding="utf-8")
    assert "_discord_client_proxy" in fetch
    assert "proxy != _discord_client_proxy" in fetch

    media = (ROOT / "plugins" / "media.py").read_text(encoding="utf-8")
    assert "_discord_media_proxy" in media
    assert "proxy != _discord_media_proxy" in media


def main():
    test_loopback_detection()
    test_dead_port_follows_system_proxy()
    test_live_port_is_kept()
    test_remote_proxy_is_untouched()
    test_unusable_system_proxy_keeps_value()
    test_startup_wiring()
    test_cached_clients_follow_proxy_switch()
    print("PASS - Discord 代理端口自检与网关重试接线")


if __name__ == "__main__":
    main()
