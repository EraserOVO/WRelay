"""向导隔离回归：临时配置、模拟连接，不读取真实凭据或启动机器人。"""

import ast
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from panel.onboarding import apply_draft, clean_draft, feed_url
from plugins.startup_config import needs_discord, qq_ready, discord_ready
from plugins.settings_model import SettingsModel


def draft(purpose="x"):
    return clean_draft({"purpose": purpose, "step": "4", "bot_id": "1001", "groups": ["group1"],
                        "users": ["user1"], "source_value": "example" if purpose == "x" else "123",
                        "source_kind": "up", "rsshub": "http://127.0.0.1:1200"})


def fixture():
    result = SettingsModel.default().to_dict()
    result["qq_group_openids"] = [{"openid": "group1", "bot_id": "1001", "enabled": False},
                                    {"openid": "other", "bot_id": "2002", "enabled": False}]
    result["qq_user_openids"] = [{"openid": "user1", "enabled": True}]
    result["unrelated"] = {"keep": 42}
    return result


def test_pure():
    original = fixture()
    for purpose in ("discord", "x", "bilibili", "qq"):
        d = draft(purpose)
        first = apply_draft(original, d, "a", ["1001"])
        assert first["qq_group_openids"][0]["enabled"] is True
        assert first["qq_group_openids"][1]["enabled"] is False
        assert first["unrelated"] == original["unrelated"]
        assert apply_draft(first, d, "a", ["1001"]) == first
        assert apply_draft(first, d, "b", ["1001"]) == first
        if purpose == "qq":
            assert first["qq_forwarding_groups"] == original["qq_forwarding_groups"]
            assert first["discord_channels"] == []
    assert original == fixture(), "输入不能被部分修改"
    for changes in ({"groups": []}, {"groups": ["discovered_only"]}, {"groups": ["other"]}, {"bot_id": "wrong"}):
        try:
            apply_draft(original, {**draft(), **changes}, "a", ["1001"])
            raise AssertionError("无效选择不应被授权")
        except ValueError:
            pass
    no_users = fixture()
    no_users["qq_user_openids"][0]["enabled"] = False
    try:
        apply_draft(no_users, draft("qq"), "a", ["1001"])
        raise AssertionError("未授权用户不应完成 QQ 向导")
    except ValueError:
        pass
    assert feed_url(draft(), original).endswith("/twitter/user/example")
    assert feed_url(draft("bilibili"), original).endswith("/bilibili/user/video/123")
    for kind in ("tag", "keyword"):
        assert feed_url({**draft("bilibili"), "source_kind": kind}, original).endswith("/bilibili/vsearch/123/pubdate/0")
    assert "secret" not in clean_draft({**draft(), "secret": "do-not-store"})
    reuse = fixture()
    reuse["x_rss_sources"] = [{"source": "Example", "name": "existing", "enabled": False, "filter_keywords": ["keep"]}]
    reused = apply_draft(reuse, draft(), "a", ["1001"])
    assert len(reused["x_rss_sources"]) == 1 and reused["x_rss_sources"][0]["filter_keywords"] == ["keep"]
    legacy = fixture()
    legacy["qq_group_openids"][0]["bot_id"] = ""
    assert apply_draft(legacy, draft(), "a", ["1001"])["qq_group_openids"][0]["enabled"]
    legacy["bilibili_sources"] = [{"kind": "up", "uid": "123", "enabled": False}]
    reused = apply_draft(legacy, draft("bilibili"), "a", ["1001"])
    assert len(reused["bilibili_sources"]) == 1
    print("PASS - 四种用途、重复保存、既有配置保护、显式权限与 RSS 路由")


def test_startup():
    # 仅抽取首次配置函数，避免执行 bot.py 的初始化、真实文件读取与网络连接。
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "_first_run_config_check")
    from types import SimpleNamespace
    namespace = {"os": os, "sys": sys, "ENV_PROD": __file__, "needs_discord": needs_discord,
                 "data_path": lambda name: name, "load_json": lambda *args, **kwargs: {},
                 "_QQ_PLACEHOLDER_APPID": "你的AppID", "_QQ_PLACEHOLDER_APPSECRET": "你的AppSecret",
                 "_DC_PLACEHOLDER_TOKEN": "在这里粘贴你的DiscordBotToken"}
    qq = {"id": "1001", "token": "1001", "secret": "test-secret"}
    configs = SimpleNamespace(qq_bots=[qq], discord_bots=[{"token": "在这里粘贴你的DiscordBotToken"}])
    namespace["_build_adapter_config"] = lambda model: configs
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / "bot.py"), "exec"), namespace)
    with patch.dict(os.environ, {}, clear=False), patch("sys.stderr", io.StringIO()):
        namespace["_first_run_config_check"]()
        assert os.environ["DISCORD_BOTS"] == "[]"
        namespace["load_json"] = lambda *args, **kwargs: {"discord_channels": [{"enabled": True}]}
        try:
            namespace["_first_run_config_check"]()
            raise AssertionError("使用 Discord 必须检查 Token")
        except SystemExit as exc:
            assert exc.code == 2
        configs.discord_bots = [{"token": "real-test-token"}]
        namespace["_first_run_config_check"]()
        configs.qq_bots = []
        try:
            namespace["_first_run_config_check"]()
            raise AssertionError("所有用途都需要 QQ")
        except SystemExit as exc:
            assert exc.code == 2
    assert qq_ready(qq) and not discord_ready({"token": "在这里粘贴你的DiscordBotToken"})
    print("PASS - 非 Discord 独立启动、Discord 按需必填、QQ 必填")


def test_watchdog():
    from types import SimpleNamespace
    import threading
    tree = ast.parse((ROOT / "panel" / "管理面板.pyw").read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "_watchdog_loop")
    class StopLoop(Exception):
        pass
    for configured in (False, True):
        sleeps, restarts = [], []
        def sleep(_):
            if sleeps:
                raise StopLoop
            sleeps.append(True)
        ns = {"time": SimpleNamespace(sleep=sleep, time=lambda: 1000, monotonic=lambda: 1000), "_BOT_CONTROL_LOCK": threading.RLock(),
              "_want_running": True, "WATCHDOG_INTERVAL": 1, "_discord_unavailable_since": 1,
              "_last_discord_restart_attempt": 0, "_discord_restart_count": 0,
              "DISCORD_UNAVAILABLE_GRACE": 5, "DISCORD_RESTART_BASE_GAP": 5, "DISCORD_RESTART_MAX_GAP": 50,
              "bot_status": lambda: {"running": True}, "_bot_health_cached": lambda: {"discord": False},
              "_read_discord_bots": lambda: ([{"token": "valid-test-token" if configured else "在这里粘贴你的DiscordBotToken"}],),
              "discord_ready": discord_ready, "_log": lambda *args: None,
              "restart_bot": lambda: (restarts.append(True) or {"ok": True})}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "watchdog", "exec"), ns)
        try:
            ns["_watchdog_loop"]()
        except StopLoop:
            pass
        assert bool(restarts) == configured
    print("PASS - 无 Discord 配置时不误重启，已有 Discord 的断线恢复保留")


async def test_api():
    import httpx
    import plugins.paths as paths
    with tempfile.TemporaryDirectory(prefix="wrelay_onboarding_") as tmp:
        original_stdout, original_stderr = sys.stdout, sys.stderr
        with patch.object(paths, "DATA_DIR", Path(tmp)):
            spec = importlib.util.spec_from_file_location("onboarding_test_panel", ROOT / "panel" / "管理面板.pyw")
            panel = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(panel)
            sys.stdout, sys.stderr = original_stdout, original_stderr
            panel._panel_log._file.close()
            panel._log = lambda *args: None
            panel.ENV_PROD = os.path.join(tmp, "test.env")
            panel.api_message_trace = lambda *args: {"ok": True, "entries": []}
            panel.api_channels_audit = lambda: {"ok": True, "audit": {"readable": [{"id": "123"}]}}
            panel.bot_status = lambda: {"running": False}
            panel.start_bot = lambda: {"ok": True}
            panel.PROJECT_ROOT = tmp
            Path(tmp, ".env.example").write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel.app), base_url="http://127.0.0.1:18090", headers={"X-WRelay-Token": panel.PANEL_TOKEN}) as client:
                async def post(path, body):
                    return await client.post("/api/onboarding/" + path, json=body)

                async def post_raw(path, raw_body):
                    return await client.post(
                        "/api/onboarding/" + path,
                        content=raw_body,
                        headers={"Content-Type": "application/json"},
                    )

                async def post_with_slow_write(path, body):
                    write_started = threading.Event()
                    release_write = threading.Event()
                    original_write = panel.atomic_write_json

                    def blocking_write(*args, **kwargs):
                        write_started.set()
                        if not release_write.wait(2):
                            raise AssertionError("测试未及时释放模拟向导状态写入")
                        return original_write(*args, **kwargs)

                    task = None
                    try:
                        with patch.object(panel, "atomic_write_json", side_effect=blocking_write):
                            task = asyncio.create_task(post(path, body))
                            started = await asyncio.wait_for(
                                asyncio.to_thread(write_started.wait, 1),
                                timeout=1,
                            )
                            assert started
                            loop_tick = asyncio.Event()
                            asyncio.get_running_loop().call_soon(loop_tick.set)
                            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                            release_write.set()
                            return await task
                    finally:
                        release_write.set()
                        if task is not None and not task.done():
                            await asyncio.gather(task, return_exceptions=True)

                async def post_with_slow_call(path, body, target):
                    read_started = threading.Event()
                    release_read = threading.Event()
                    original_read = getattr(panel, target)

                    def blocking_read(*args, **kwargs):
                        read_started.set()
                        if not release_read.wait(2):
                            raise AssertionError("测试未及时释放模拟同步读取")
                        return original_read(*args, **kwargs)

                    task = None
                    try:
                        with patch.object(panel, target, side_effect=blocking_read):
                            task = asyncio.create_task(post(path, body))
                            started = await asyncio.wait_for(
                                asyncio.to_thread(read_started.wait, 1),
                                timeout=1,
                            )
                            assert started
                            loop_tick = asyncio.Event()
                            asyncio.get_running_loop().call_soon(loop_tick.set)
                            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                            release_read.set()
                            return await task
                    finally:
                        release_read.set()
                        if task is not None and not task.done():
                            await asyncio.gather(task, return_exceptions=True)

                first = (await client.get("/api/onboarding")).json()
                assert first["show_welcome"] is True
                denied = await client.get("/api/onboarding", headers={"X-WRelay-Token": "bad"})
                assert denied.status_code == 401
                env_path = Path(panel.ENV_PROD)
                assert not env_path.exists()
                onboarding_state_path = Path(panel.DATA_DIR, "onboarding.json")
                for endpoint in ("progress", "credentials", "authorize", "probe", "apply", "receipt"):
                    for raw_body in (b"[]", b"null", b"{"):
                        invalid = await post_raw(endpoint, raw_body)
                        assert invalid.status_code == 400, (endpoint, raw_body, invalid.text)
                        assert invalid.json() == {"ok": False, "msg": "向导请求格式不正确"}
                assert not onboarding_state_path.exists()
                assert not env_path.exists()
                current = draft()
                r = await post_with_slow_call(
                    "credentials",
                    {"draft": current, "secret": "test-secret"},
                    "_read_qq_bots",
                )
                assert r.status_code == 200, r.text
                assert "test-secret" not in r.text
                assert r.json()["credentials"]["restart_needed"] is True
                assert (await client.get("/api/onboarding")).json()["show_welcome"] is False
                r = await post_with_slow_write(
                    "progress",
                    {"draft": {**current, "secret": "never-persist"}},
                )
                assert r.status_code == 200, r.text
                flow = r.json()["state"]["flow_id"]
                assert r.json()["state"]["restart_needed"] is True
                assert (await post("start", {})).json()["ok"] is True
                assert (await client.get("/api/onboarding")).json()["credentials"]["restart_needed"] is False
                assert "never-persist" not in Path(tmp, "onboarding.json").read_text(encoding="utf-8")
                assert (await post("progress", {"flow_id": "stale", "draft": current})).status_code == 409
                settings = fixture()
                settings["qq_user_openids"][0]["enabled"] = False
                panel.atomic_write_json(panel.SETTINGS_FILE, settings)

                r = await post_with_slow_call(
                    "authorize",
                    {"confirm": True, "openid": "user1"},
                    "_load_settings_snapshot",
                )
                assert r.status_code == 200, r.text
                assert panel.load_settings()["qq_user_openids"][0]["enabled"] is True

                r = await post("apply", {"draft": current, "flow_id": flow, "revision": "stale"})
                assert r.status_code == 409
                r = await post_with_slow_call(
                    "apply",
                    {"draft": current, "flow_id": flow, "revision": panel._settings_revision()},
                    "_load_settings_snapshot",
                )
                assert r.status_code == 200, r.text
                saved_settings = panel.load_settings()
                r = await post("apply", {"draft": current, "flow_id": flow, "revision": panel._settings_revision()})
                assert r.status_code == 200, r.text
                assert panel.load_settings() == saved_settings
                r = await post_with_slow_write("receipt", {"flow_id": flow})
                assert r.json()["state"]["receipt_confirmed"] is True
                r = await post("progress", {"flow_id": flow, "draft": {**current, "step": "6"}})
                assert r.json()["state"]["receipt_confirmed"] is True
                # 增加其他账号不会删除旧账号；两个凭据校验通过才写入。
                r = await post("credentials", {"draft": {**current, "bot_id": "2002"}, "secret": "second-secret"})
                assert len(r.json()["credentials"]["bots"]) == 2
                old_env = Path(panel.ENV_PROD).read_bytes()
                r = await post("credentials", {"draft": draft("discord"), "secret": "new-secret", "token": "在这里粘贴你的DiscordBotToken"})
                assert r.status_code == 400 and Path(panel.ENV_PROD).read_bytes() == old_env
                r = await post("credentials", {"draft": draft("discord"), "token": "new-token"})
                assert r.status_code == 200 and "new-token" not in r.text
                # RSS HTML 错页不得被当成有效来源。
                original_client = httpx.AsyncClient
                mock_transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>bad</html>"))
                with patch("panel.onboarding_api.httpx.AsyncClient", side_effect=lambda **kw: original_client(transport=mock_transport, **kw)):
                    r = await post("probe", current)
                    assert r.status_code == 400
                mock_transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<rss><channel/></rss>"))
                with patch("panel.onboarding_api.httpx.AsyncClient", side_effect=lambda **kw: original_client(transport=mock_transport, **kw)):
                    r = await post_with_slow_call("probe", current, "load_settings")
                    assert r.status_code == 200, r.text

                # Damaged wizard state must remain visible and must not be
                # replaced by a fresh draft or partially commit credentials.
                env_before_corruption = env_path.read_bytes()
                corrupt_states = (
                    b"{invalid",
                    b"[]",
                    b'{"draft": []}',
                )
                with (
                    patch.object(panel, "start_bot") as start_bot,
                    patch.object(panel, "_load_settings_snapshot") as load_settings_snapshot,
                ):
                    for corrupt in corrupt_states:
                        onboarding_state_path.write_bytes(corrupt)
                        response = await client.get("/api/onboarding")
                        assert response.status_code == 500, response.text
                        response = await post("progress", {"draft": current})
                        assert response.status_code == 500, response.text
                        assert onboarding_state_path.read_bytes() == corrupt

                        response = await post("credentials", {"draft": current, "secret": "must-not-save"})
                        assert response.status_code == 500, response.text
                        assert onboarding_state_path.read_bytes() == corrupt
                        assert env_path.read_bytes() == env_before_corruption

                        response = await post("start", {})
                        assert response.status_code == 500, response.text
                        assert onboarding_state_path.read_bytes() == corrupt
                        response = await post("receipt", {"flow_id": flow})
                        assert response.status_code == 500, response.text
                        response = await post("apply", {"draft": current, "flow_id": flow, "revision": panel._settings_revision()})
                        assert response.status_code == 500, response.text
                        assert onboarding_state_path.read_bytes() == corrupt
                    start_bot.assert_not_called()
                    load_settings_snapshot.assert_not_called()
            panel._panel_log._file.close()
    print("PASS - API 认证、进度恢复、密钥隔离、原子保存、版本冲突与来源检测")


if __name__ == "__main__":
    test_pure()
    test_startup()
    test_watchdog()
    asyncio.run(test_api())
