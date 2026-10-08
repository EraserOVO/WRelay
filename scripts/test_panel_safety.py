"""验证桌面管理接口的认证、防坏配置和 PID 边界。

不启动桌面进程、不修改真实 settings；用法：
.venv/Scripts/python.exe scripts/test_panel_safety.py
"""
import asyncio
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch

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
        "server": ("127.0.0.1", 0),
        "client": ("127.0.0.1", 50000),
    })


async def pass_through(_request):
    return Response("ok", status_code=200)


def check_guard(path, headers):
    return asyncio.run(panel.origin_guard(
        make_request(path, headers), pass_through
    ))


def check_watchdog_uses_monotonic_time():
    class StopWatchdog(Exception):
        pass

    sleep_calls = 0

    def stop_after_one_iteration(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise StopWatchdog

    previous = (
        panel._want_running,
        panel._last_auto_start,
        panel._discord_unavailable_since,
    )
    panel._want_running = True
    panel._last_auto_start = 100.0
    panel._discord_unavailable_since = None
    try:
        with (
            patch.object(panel.time, "sleep", side_effect=stop_after_one_iteration),
            patch.object(panel.time, "monotonic", return_value=120.0),
            patch.object(panel.time, "time", side_effect=AssertionError("wall clock used")),
            patch.object(panel, "bot_status", return_value={"running": False}),
            patch.object(panel, "start_bot") as start_bot,
        ):
            try:
                panel._watchdog_loop()
            except StopWatchdog:
                pass
        assert not start_bot.called
    finally:
        (
            panel._want_running,
            panel._last_auto_start,
            panel._discord_unavailable_since,
        ) = previous


def check_inaccessible_bot_pid_fails_closed():
    from panel.process_manager import BotProcessManager

    class FakeFunction:
        def __init__(self, result):
            self.result = result

        def __call__(self, *_args):
            return self.result

    class InaccessibleKernel:
        OpenProcess = FakeFunction(0)
        GetLastError = FakeFunction(5)

    class MissingKernel:
        OpenProcess = FakeFunction(0)
        GetLastError = FakeFunction(87)

    manager = BotProcessManager(
        project_root=str(ROOT),
        python_path=sys.executable,
        bot_entry=str(ROOT / "bot.py"),
        pid_file=str(ROOT / "data" / "unused-test-bot.pid"),
        process_state={"proc": None},
        logger=lambda _message: None,
        localize_error=lambda message, _fallback: message,
    )
    with patch("panel.process_manager._kernel32_api", return_value=InaccessibleKernel()):
        assert manager.process_identity_status(12345) is None
    with patch("panel.process_manager._kernel32_api", return_value=MissingKernel()):
        assert manager.process_identity_status(12345) is False

    cleared = []
    killed = []
    manager.read_pid_record = lambda: {"pid": 12345, "creation_time": None}
    manager.clear_pid = lambda: cleared.append(True)
    manager.kill_tree = lambda pid: killed.append(pid) or True
    with patch("panel.process_manager._kernel32_api", return_value=InaccessibleKernel()):
        status = manager.bot_status()
        assert status["running"] is True and status["verified"] is False
        assert not cleared

        with patch("panel.process_manager.subprocess.Popen") as popen:
            started = manager.start_bot()
        assert started["ok"] is False
        assert "相同 Windows 权限" in started["msg"]
        popen.assert_not_called()

        stopped = manager.stop_bot()
        assert stopped["ok"] is False and stopped["stopped"] is False
        assert not killed and not cleared
        assert "相同 Windows 权限" in stopped["msg"]

        previous_want_running = panel._want_running
        panel._want_running = True
        try:
            with patch.object(panel, "_process_manager", return_value=manager):
                wrapper_result = panel.stop_bot()
            assert wrapper_result["ok"] is False
            assert panel._want_running is True
        finally:
            panel._want_running = previous_want_running
    print("PASS - 无权检查的 PID 不清理、不重启也不终止")


def check_bot_pid_creation_time_prevents_reuse():
    from panel.process_manager import BotProcessManager

    with tempfile.TemporaryDirectory(prefix="wrelay_pid_identity_test_") as tmp:
        pid_file = Path(tmp) / "bot.pid"
        manager = BotProcessManager(
            project_root=str(ROOT),
            python_path=sys.executable,
            bot_entry=str(ROOT / "bot.py"),
            pid_file=str(pid_file),
            process_state={"proc": None},
            logger=lambda _message: None,
            localize_error=lambda message, _fallback: message,
        )
        manager._query_process_identity = lambda _pid: (True, 200)
        manager.write_pid(12345)
        assert manager.read_pid_record() == {
            "pid": 12345,
            "creation_time": 200,
            "error": None,
        }

        pid_file.write_text(
            json.dumps({"pid": 12345, "creation_time": 100}),
            encoding="ascii",
        )
        killed = []
        manager.kill_tree = lambda pid: killed.append(pid) or True
        status = manager.bot_status()
        assert status["running"] is False
        assert not pid_file.exists()
        assert not killed

        pid_file.write_text("12345", encoding="ascii")
        status = manager.bot_status()
        assert status["running"] is True
        assert manager.read_pid_record() == {
            "pid": 12345,
            "creation_time": 200,
            "error": None,
        }

    if os.name == "nt":
        live_manager = BotProcessManager(
            project_root=str(ROOT),
            python_path=sys.executable,
            bot_entry=str(ROOT / "bot.py"),
            pid_file=str(Path(tmp) / "live.pid"),
            process_state={"proc": None},
            logger=lambda _message: None,
            localize_error=lambda message, _fallback: message,
        )
        identity = live_manager._query_process_identity(os.getpid())
        assert isinstance(identity, tuple) and identity[0] is True
        assert identity[1] > 0
    print("PASS - PID 文件保存创建时间并兼容旧格式，复用 PID 不会命中")


def check_invalid_bot_pid_file_fails_closed():
    from panel.process_manager import BotProcessManager

    with tempfile.TemporaryDirectory(prefix="wrelay_pid_file_error_test_") as tmp:
        pid_file = Path(tmp) / "bot.pid"
        manager = BotProcessManager(
            project_root=str(ROOT),
            python_path=sys.executable,
            bot_entry=str(ROOT / "bot.py"),
            pid_file=str(pid_file),
            process_state={"proc": None},
            logger=lambda _message: None,
            localize_error=lambda message, _fallback: message,
        )
        cleared = []
        killed = []
        manager.clear_pid = lambda: cleared.append(True)
        manager.kill_tree = lambda pid: killed.append(pid) or True

        for content in ("", "not-a-pid"):
            pid_file.write_text(content, encoding="ascii")
            status = manager.bot_status()
            assert status["running"] is True
            assert status["verified"] is False
            assert status["status_error"] == "format"
            with patch("panel.process_manager.subprocess.Popen") as popen:
                started = manager.start_bot()
            assert started["ok"] is False
            popen.assert_not_called()
            stopped = manager.stop_bot()
            assert stopped["ok"] is False
            assert pid_file.exists()

        with patch("builtins.open", side_effect=PermissionError("denied")):
            status = manager.bot_status()
            started = manager.start_bot()
            stopped = manager.stop_bot()
        assert status["status_error"] == "read"
        assert started["ok"] is False
        assert stopped["ok"] is False
        assert not cleared and not killed

        pid_file.unlink()
        status = manager.bot_status()
        assert status["running"] is False
    print("PASS - PID 文件缺失才视为未启动，损坏或不可读时保留现场并失败关闭")


def check_stop_waits_for_pid_exit():
    from panel.process_manager import BotProcessManager

    def make_manager(statuses):
        manager = BotProcessManager(
            project_root=str(ROOT),
            python_path=sys.executable,
            bot_entry=str(ROOT / "bot.py"),
            pid_file=str(ROOT / "data" / "unused-test-bot.pid"),
            process_state={"proc": None},
            logger=lambda _message: None,
            localize_error=lambda message, _fallback: message,
        )
        manager.read_pid_record = lambda: {
            "pid": 12345,
            "creation_time": 200,
            "error": None,
        }
        manager.process_identity_status = lambda *_args: next(statuses)
        killed = []
        cleared = []
        manager.kill_tree = lambda pid: killed.append(pid) or True
        manager.clear_pid = lambda: cleared.append(True)
        return manager, killed, cleared

    manager, killed, cleared = make_manager(iter([True, True, None, False]))
    stopped = manager.stop_bot()
    assert stopped == {"ok": True, "stopped": True}
    assert killed == [12345]
    assert cleared

    manager, killed, cleared = make_manager(iter([True, True, True]))
    with (
        patch("panel.process_manager.PROCESS_STOP_VERIFY_TIMEOUT", 0.1),
        patch("panel.process_manager.time.monotonic", side_effect=[0, 0, 0.11]),
        patch("panel.process_manager.time.sleep", return_value=None),
    ):
        stopped = manager.stop_bot()
    assert stopped["ok"] is False and stopped["stopped"] is False
    assert killed == [12345]
    assert not cleared
    print("PASS - 停止后轮询确认退出，延迟退出成功、持续运行保持失败")


def check_settings_save_does_not_block_event_loop():
    save_started = threading.Event()
    release_save = threading.Event()

    class JsonRequest:
        async def json(self):
            return {"_revision": "test-revision", "qq_group_openids": []}

    def slow_save_settings(_data, expected_revision=None):
        save_started.set()
        if not release_save.wait(2):
            raise AssertionError("测试未及时释放模拟 settings 保存")

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "_settings_revision", return_value="test-revision"),
                patch.object(panel, "_validate_settings", return_value=None),
                patch.object(panel, "_settings_payload", return_value={"ok": True}),
                patch.object(panel, "save_settings", side_effect=slow_save_settings),
            ):
                task = asyncio.create_task(panel.api_settings_save(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(save_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_save.set()
                assert await task == {"ok": True, "settings": {"ok": True}}
        finally:
            release_save.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    print("PASS - 面板 settings 保存在线程池完成，慢写期间事件循环仍响应")


def check_backfill_toggle_does_not_block_event_loop():
    load_started = threading.Event()
    release_load = threading.Event()
    saved = []

    class JsonRequest:
        async def json(self):
            return {"backfill_enabled": True}

    def slow_load_settings_snapshot():
        load_started.set()
        if not release_load.wait(2):
            raise AssertionError("测试未及时释放模拟 settings 读取")
        return {"backfill_enabled": False}, "test-revision"

    async def exercise_slow_read():
        task = None
        try:
            with (
                patch.object(panel, "_load_settings_snapshot", side_effect=slow_load_settings_snapshot),
                patch.object(panel, "_validate_settings", return_value=None),
                patch.object(panel, "save_settings", side_effect=lambda data, expected_revision=None: saved.append((data, expected_revision))),
            ):
                task = asyncio.create_task(panel.api_settings_backfill_toggle(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(load_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_load.set()
                assert await task == {
                    "ok": True,
                    "changed": True,
                    "backfill_enabled": True,
                }
        finally:
            release_load.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise_slow_read())
    assert saved == [({"backfill_enabled": True}, "test-revision")]

    async def exercise_unchanged_value():
        with (
            patch.object(panel, "_load_settings_snapshot", return_value=({"backfill_enabled": True}, "test-revision")),
            patch.object(panel, "_validate_settings") as validate,
            patch.object(panel, "save_settings") as save,
        ):
            assert await panel.api_settings_backfill_toggle(JsonRequest()) == {
                "ok": True,
                "changed": False,
                "backfill_enabled": True,
            }
            validate.assert_not_called()
            save.assert_not_called()

    asyncio.run(exercise_unchanged_value())
    print("PASS - 补发开关事务在线程池运行，慢读期间事件循环仍响应且无变化不写盘")


def check_scheduled_message_update_does_not_block_event_loop():
    load_started = threading.Event()
    release_load = threading.Event()
    saved = []

    class JsonRequest:
        async def json(self):
            return {"enabled": True}

    current_settings = {
        "unrelated_setting": "keep",
        "scheduled_message": {
            "enabled": False,
            "content": "keep existing message",
            "messages": [{"id": "existing", "content": "keep"}],
        },
    }

    def slow_load_settings_snapshot():
        load_started.set()
        if not release_load.wait(2):
            raise AssertionError("测试未及时释放模拟 settings 读取")
        return current_settings, "test-revision"

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "_load_settings_snapshot", side_effect=slow_load_settings_snapshot),
                patch.object(panel, "_validate_settings", return_value=None),
                patch.object(panel, "save_settings", side_effect=lambda data, expected_revision=None: saved.append((data, expected_revision))),
            ):
                task = asyncio.create_task(
                    panel.api_settings_scheduled_message(JsonRequest())
                )
                started = await asyncio.wait_for(
                    asyncio.to_thread(load_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_load.set()
                assert await task == {
                    "ok": True,
                    "scheduled_message": {
                        "enabled": True,
                        "content": "keep existing message",
                        "messages": [{"id": "existing", "content": "keep"}],
                    },
                }
        finally:
            release_load.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert len(saved) == 1
    saved_settings, revision = saved[0]
    assert revision == "test-revision"
    assert saved_settings["unrelated_setting"] == "keep"
    assert saved_settings["scheduled_message"]["enabled"] is True
    assert saved_settings["scheduled_message"]["content"] == "keep existing message"
    assert saved_settings["scheduled_message"]["messages"] == [
        {"id": "existing", "content": "keep"}
    ]
    print("PASS - 定时消息字段事务在线程池运行，慢读期间事件循环仍响应且保留其他设置")


def check_discovered_settings_sync_does_not_block_event_loop():
    load_started = threading.Event()
    release_load = threading.Event()
    saved = []

    class JsonRequest:
        async def body(self):
            return json.dumps({
                "kind": "groups",
                "openids": ["chosen", "not-authorized"],
            }).encode("utf-8")

    current_settings = {
        "unrelated_setting": "keep",
        "qq_group_openids": [],
        "qq_user_openids": [],
    }

    def slow_load_settings_snapshot():
        load_started.set()
        if not release_load.wait(2):
            raise AssertionError("测试未及时释放模拟 settings 读取")
        return current_settings, "test-revision"

    def save_snapshot(data, expected_revision=None):
        saved.append((data, expected_revision))

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "_load_settings_snapshot", side_effect=slow_load_settings_snapshot),
                patch.object(panel, "_read_discovered", return_value=["chosen"]),
                patch.object(panel, "_read_discovered_group_bot_ids", return_value={"chosen": "bot-1"}),
                patch.object(panel, "_validate_settings", return_value=None),
                patch.object(panel, "save_settings", side_effect=save_snapshot),
                patch.object(panel, "load_settings", return_value={"reloaded": True}),
            ):
                task = asyncio.create_task(panel.api_settings_sync(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(load_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_load.set()
                assert await task == {
                    "ok": True,
                    "added": 1,
                    "settings": {"reloaded": True},
                }
        finally:
            release_load.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert len(saved) == 1
    saved_settings, revision = saved[0]
    assert revision == "test-revision"
    assert saved_settings["unrelated_setting"] == "keep"
    assert saved_settings["qq_group_openids"] == [{
        "openid": "chosen",
        "enabled": False,
        "remark": "自动发现，点击启用",
        "bot_id": "bot-1",
    }]
    assert saved_settings["qq_user_openids"] == []
    print("PASS - 自动发现同步事务在线程池运行，慢读期间事件循环仍响应且只同步选中群")


def check_registration_review_does_not_block_event_loop():
    load_started = threading.Event()
    release_load = threading.Event()
    saved_settings = []
    saved_registrations = []

    class JsonRequest:
        async def json(self):
            return {"kind": "users", "openid": "user-1", "action": "approve"}

    current_settings = {
        "unrelated_setting": "keep",
        "qq_user_openids": [],
    }
    registrations = {
        "users": {
            "user-1": {"nickname": "User One", "qq_id": "12345", "time": 1}
        }
    }

    def slow_load_settings_snapshot():
        load_started.set()
        if not release_load.wait(2):
            raise AssertionError("测试未及时释放模拟 settings 读取")
        return current_settings, "settings-revision"

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "file_revision", return_value="registrations-revision"),
                patch.object(panel, "_read_registrations", return_value=registrations),
                patch.object(panel, "_ensure_discovered"),
                patch.object(panel, "_load_settings_snapshot", side_effect=slow_load_settings_snapshot),
                patch.object(panel, "_read_discovered", return_value=["user-1"]),
                patch.object(panel, "save_settings", side_effect=lambda data, expected_revision=None: saved_settings.append((data, expected_revision))),
                patch.object(panel, "_write_registrations", side_effect=lambda data, expected_revision=None: saved_registrations.append((data, expected_revision))),
                patch.object(panel, "load_settings", return_value={"reloaded": True}),
                patch.object(panel, "_log"),
            ):
                task = asyncio.create_task(panel.api_registrations_review(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(load_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_load.set()
                assert await task == {
                    "ok": True,
                    "added": 1,
                    "settings": {"reloaded": True},
                }
        finally:
            release_load.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert len(saved_settings) == 1
    data, revision = saved_settings[0]
    assert revision == "settings-revision"
    assert data["unrelated_setting"] == "keep"
    assert data["qq_user_openids"] == [{
        "openid": "user-1",
        "enabled": False,
        "remark": "自动发现，点击启用",
        "name": "User One",
        "qq_id": "12345",
    }]
    assert saved_registrations == [({"users": {}}, "registrations-revision")]
    print("PASS - 注册审核事务在线程池运行，慢读期间事件循环仍响应且审核用户仍禁用")


def check_corrupt_registrations_are_reported_and_preserved():
    class JsonRequest:
        async def json(self):
            return {"kind": "users", "openid": "user-1", "action": "approve"}

    old_path = panel.REGISTRATIONS_FILE
    with tempfile.TemporaryDirectory(prefix="wrelay_bad_registrations_test_") as tmp:
        path = Path(tmp) / "qq_registrations.json"
        panel.REGISTRATIONS_FILE = str(path)
        try:
            for contents in ("{invalid", "[]", '{"users": []}'):
                path.write_text(contents, encoding="utf-8")
                listing = panel.api_registrations()
                assert listing.status_code == 500
                assert json.loads(listing.body)["ok"] is False

                review = asyncio.run(panel.api_registrations_review(JsonRequest()))
                assert review.status_code == 500
                assert json.loads(review.body)["ok"] is False
                assert path.read_text(encoding="utf-8") == contents
        finally:
            panel.REGISTRATIONS_FILE = old_path
    print("PASS - 注册申请文件损坏时列表/审核返回错误且保留原文件")


def check_registration_list_uses_one_snapshot():
    snapshot = {
        "users": {"user-1": {"qq_id": "12345", "time": 1}},
        "groups": {"group-1": {"qq_id": "54321", "time": 2}},
    }
    with patch.object(panel, "_read_registrations", return_value=snapshot) as read:
        response = panel.api_registrations()
    assert response == {
        "ok": True,
        "users": [{"openid": "user-1", "qq_id": "12345", "time": 1}],
        "groups": [{"openid": "group-1", "qq_id": "54321", "time": 2}],
    }
    read.assert_called_once_with()
    print("PASS - 注册申请列表的用户与群共用单次文件快照")


def check_corrupt_identity_store_is_reported_and_preserved():
    old_path = panel.IDENTITIES_FILE
    with tempfile.TemporaryDirectory(prefix="wrelay_bad_identities_test_") as tmp:
        path = Path(tmp) / "qq_identities.json"
        panel.IDENTITIES_FILE = str(path)
        try:
            for contents in ("{invalid", "[]", '{"users": []}'):
                path.write_text(contents, encoding="utf-8")
                response = panel.api_identities()
                assert response.status_code == 500
                assert json.loads(response.body)["ok"] is False
                assert path.read_text(encoding="utf-8") == contents
        finally:
            panel.IDENTITIES_FILE = old_path
    print("PASS - 身份资料文件损坏时接口返回错误且保留原文件")


def check_identity_list_uses_one_snapshot():
    snapshot = {
        "users": {"user-1": {"nickname": "Alice", "admin_remark": "user"}},
        "groups": {"group-1": {"group_name": "Group", "admin_remark": "group"}},
    }
    with patch.object(panel, "_read_identities", return_value=snapshot) as read:
        response = panel.api_identities()
    assert response == {
        "ok": True,
        "users": [{"openid": "user-1", "name": "Alice", "admin_remark": "user"}],
        "groups": [{"openid": "group-1", "name": "Group", "admin_remark": "group"}],
    }
    read.assert_called_once_with()
    print("PASS - 身份资料列表的用户与群共用单次文件快照")


def check_corrupt_discovered_store_is_reported_and_preserved():
    class BodyRequest:
        def __init__(self, body):
            self.raw_body = json.dumps(body).encode("utf-8")

        async def body(self):
            return self.raw_body

    class ReviewRequest:
        async def json(self):
            return {"kind": "groups", "openid": "group-1", "action": "approve"}

    old_data_dir = panel.DATA_DIR
    with tempfile.TemporaryDirectory(prefix="wrelay_bad_discovered_test_") as tmp:
        panel.DATA_DIR = tmp
        data_dir = Path(tmp)
        group_path = data_dir / "qq_group_openids.json"
        user_path = data_dir / "qq_user_openids.json"
        valid_groups = '{"group_openids": [], "group_bot_ids": {}}'
        invalid_cases = (
            (group_path, "groups", "{invalid"),
            (group_path, "groups", "[]"),
            (group_path, "groups", '{"group_openids": {}}'),
            (group_path, "groups", '{"group_openids": ["group-1"], "group_bot_ids": []}'),
            (group_path, "groups", '{"group_openids": ["group-1"], "group_bot_ids": {"group-1": 123}}'),
            (user_path, "users", "{invalid"),
            (user_path, "users", "[]"),
            (user_path, "users", '{"user_openids": [123]}'),
        )
        try:
            with (
                patch.object(panel, "load_settings", return_value={"qq_group_openids": [], "qq_user_openids": []}),
                patch.object(panel, "_load_settings_snapshot", return_value=({"qq_group_openids": [], "qq_user_openids": []}, "settings-revision")),
                patch.object(panel, "save_settings") as save_settings,
                patch.object(panel, "_log"),
            ):
                for path, kind, contents in invalid_cases:
                    group_path.write_text(valid_groups, encoding="utf-8")
                    if path == group_path:
                        path.write_text(contents, encoding="utf-8")
                    else:
                        path.write_text(contents, encoding="utf-8")

                    preview = panel.api_settings_sync_preview()
                    assert preview.status_code == 500
                    assert json.loads(preview.body)["ok"] is False

                    sync = asyncio.run(panel.api_settings_sync(BodyRequest({"kind": kind})))
                    assert sync.status_code == 500
                    assert json.loads(sync.body)["ok"] is False
                    assert path.read_text(encoding="utf-8") == contents

                # A group approval must preserve both the registration and settings
                # when the discovered Bot ownership map is malformed.
                group_path.write_text(
                    '{"group_openids": ["group-1"], "group_bot_ids": []}',
                    encoding="utf-8",
                )
                registrations = {"groups": {"group-1": {"group_name": "Group", "bot_id": "bot-1"}}}
                with (
                    patch.object(panel, "file_revision", return_value="registrations-revision"),
                    patch.object(panel, "_read_registrations", return_value=registrations),
                    patch.object(panel, "_write_registrations") as write_registrations,
                    patch.object(panel, "_load_settings_snapshot") as load_snapshot,
                ):
                    review = asyncio.run(panel.api_registrations_review(ReviewRequest()))
                    assert review.status_code == 500
                    assert json.loads(review.body)["ok"] is False
                    assert group_path.read_text(encoding="utf-8") == '{"group_openids": ["group-1"], "group_bot_ids": []}'
                    write_registrations.assert_not_called()
                    load_snapshot.assert_not_called()
                save_settings.assert_not_called()
        finally:
            panel.DATA_DIR = old_data_dir
    print("PASS - 自动发现文件损坏时预览/同步/群审核明确失败且保留原记录")


def check_ai_test_record_does_not_block_event_loop():
    load_started = threading.Event()
    release_load = threading.Event()
    saved = []

    class JsonRequest:
        async def json(self):
            return {
                "profile_id": "profile-1",
                "record": {
                    "ok": True,
                    "model": " tested-model ",
                    "message": " test passed ",
                    "at": 123,
                    "diagnostic": {
                        "category": "success",
                        "detail": "valid response",
                        "secret": "must-not-persist",
                    },
                    "api_key": "must-not-persist",
                },
            }

    current_settings = {
        "unrelated_setting": "keep",
        "ai_profiles": [{"id": "profile-1", "model": "configured-model"}],
    }

    def slow_load_settings_snapshot():
        load_started.set()
        if not release_load.wait(2):
            raise AssertionError("测试未及时释放模拟 settings 读取")
        return current_settings, "test-revision"

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "_load_settings_snapshot", side_effect=slow_load_settings_snapshot),
                patch.object(panel, "_validate_settings", return_value=None),
                patch.object(panel, "save_settings", side_effect=lambda data, expected_revision=None: saved.append((data, expected_revision))),
            ):
                task = asyncio.create_task(panel.api_translation_ai_test_record(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(load_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_load.set()
                assert await task == {"ok": True}
        finally:
            release_load.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert len(saved) == 1
    data, revision = saved[0]
    assert revision == "test-revision"
    assert data["unrelated_setting"] == "keep"
    profile = data["ai_profiles"][0]
    assert profile["last_test"] == {
        "ok": True,
        "model": "tested-model",
        "message": "test passed",
        "at": 123,
        "diagnostic": {"category": "success", "detail": "valid response"},
    }
    assert profile["last_tested_model"] == "tested-model"
    assert "must-not-persist" not in json.dumps(data, ensure_ascii=False)
    print("PASS - AI 检测记录在线程池写入，慢读期间事件循环仍响应且不持久化密钥字段")


def check_translation_env_save_does_not_block_event_loop():
    write_started = threading.Event()
    release_write = threading.Event()
    writes = []

    class JsonRequest:
        async def json(self):
            return {"secret_id": "secret-id-value", "secret_key": "secret-key-value"}

    def slow_write_translation_env(values):
        writes.append(values)
        write_started.set()
        if not release_write.wait(2):
            raise AssertionError("测试未及时释放模拟翻译密钥写入")

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "_write_translation_env", side_effect=slow_write_translation_env),
                patch.object(panel, "_read_translation_env_state", return_value={"configured": True}),
                patch.object(panel, "_log"),
            ):
                task = asyncio.create_task(panel.api_translation_env_save(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(write_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_write.set()
                result = await task
                assert result == {"ok": True, "configured": {"configured": True}}
                assert "secret-id-value" not in json.dumps(result)
                assert "secret-key-value" not in json.dumps(result)
        finally:
            release_write.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert writes == [{
        "WRELAY_TENCENT_SECRET_ID": "secret-id-value",
        "WRELAY_TENCENT_SECRET_KEY": "secret-key-value",
    }]
    print("PASS - 腾讯翻译密钥写入在线程池执行，慢写期间事件循环仍响应且响应不含密钥")


def check_ai_env_save_does_not_block_event_loop():
    write_started = threading.Event()
    release_write = threading.Event()
    writes = []

    class JsonRequest:
        async def json(self):
            return {"env_name": "WRELAY_TEST_AI_KEY", "api_key": "private-ai-key"}

    def slow_write_ai_env(env_name, api_key):
        writes.append((env_name, api_key))
        write_started.set()
        if not release_write.wait(2):
            raise AssertionError("测试未及时释放模拟 AI 密钥写入")

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "_valid_env_name", return_value=True),
                patch.object(panel, "_write_ai_env", side_effect=slow_write_ai_env),
                patch.object(panel, "_read_env_secret", return_value="private-ai-key"),
                patch.object(panel, "_log"),
            ):
                task = asyncio.create_task(panel.api_translation_ai_env_save(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(write_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_write.set()
                result = await task
                assert result == {"ok": True, "configured": True}
                assert "private-ai-key" not in json.dumps(result)
        finally:
            release_write.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert writes == [("WRELAY_TEST_AI_KEY", "private-ai-key")]
    print("PASS - AI 密钥写入在线程池执行，慢写期间事件循环仍响应且响应不含密钥")


def check_qq_bot_config_save_does_not_block_event_loop():
    read_started = threading.Event()
    release_read = threading.Event()
    writes = []

    class JsonRequest:
        async def json(self):
            return {
                "bots": [
                    {"appid": "A", "secret": ""},
                    {"appid": "B", "secret": "new-secret"},
                ]
            }

    existing_bots = [
        {"id": "A", "token": "A", "secret": "old-secret", "sandbox": False}
    ]

    def slow_read_qq_bots():
        read_started.set()
        if not release_read.wait(2):
            raise AssertionError("测试未及时释放模拟 QQ Bot 配置读取")
        return existing_bots, 0, ["QQ_BOTS=[]"], 0

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "_read_qq_bots", side_effect=slow_read_qq_bots),
                patch.object(panel, "_bound_qq_bot_ids", return_value=set()),
                patch.object(panel, "_write_qq_bots", side_effect=lambda bots, lines, line_idx, end_idx: writes.append((bots, lines, line_idx, end_idx))),
                patch.object(panel, "_log"),
            ):
                task = asyncio.create_task(panel.api_bot_qq_config_save(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(read_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_read.set()
                result = await task
                assert result == {"ok": True, "appid": "A", "bot_ids": ["A", "B"]}
                assert "old-secret" not in json.dumps(result)
                assert "new-secret" not in json.dumps(result)
        finally:
            release_read.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert len(writes) == 1
    saved_bots, lines, line_idx, end_idx = writes[0]
    assert [bot["id"] for bot in saved_bots] == ["A", "B"]
    assert saved_bots[0]["secret"] == "old-secret"
    assert saved_bots[0]["sandbox"] is False
    assert saved_bots[1]["secret"] == "new-secret"
    assert (lines, line_idx, end_idx) == (["QQ_BOTS=[]"], 0, 0)
    print("PASS - QQ Bot 配置事务在线程池运行，慢读期间事件循环仍响应且 Secret 不回传")


def check_discord_bot_config_save_does_not_block_event_loop():
    read_started = threading.Event()
    release_read = threading.Event()
    writes = []

    class JsonRequest:
        async def json(self):
            return {"token": "new-discord-token"}

    current_bots = [
        {"id": "discord-1", "token": "old-discord-token", "intents": ["guilds"]}
    ]

    def slow_read_discord_bots():
        read_started.set()
        if not release_read.wait(2):
            raise AssertionError("测试未及时释放模拟 Discord Bot 配置读取")
        return current_bots, 0, ["DISCORD_BOTS=[]"], 0

    def save_bots(bots, lines, line_idx, end_idx):
        writes.append((bots, lines, line_idx, end_idx))

    async def exercise_slow_read():
        task = None
        try:
            with (
                patch.object(panel, "_read_discord_bots", side_effect=slow_read_discord_bots),
                patch.object(panel, "_write_discord_bots", side_effect=save_bots),
                patch.object(panel, "_log"),
            ):
                task = asyncio.create_task(panel.api_bot_discord_config_save(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(read_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_read.set()
                result = await task
                assert result == {"ok": True}
                assert "new-discord-token" not in json.dumps(result)
                assert "old-discord-token" not in json.dumps(result)
        finally:
            release_read.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise_slow_read())
    assert len(writes) == 1
    saved_bots, lines, line_idx, end_idx = writes[0]
    assert saved_bots == [{
        "id": "discord-1",
        "token": "new-discord-token",
        "intents": ["guilds"],
    }]
    assert (lines, line_idx, end_idx) == (["DISCORD_BOTS=[]"], 0, 0)

    class EmptyTokenRequest:
        async def json(self):
            return {"token": ""}

    async def exercise_empty_token_preserves_existing_value():
        with (
            patch.object(panel, "_read_discord_bots", return_value=(saved_bots, 0, lines, end_idx)),
            patch.object(panel, "_write_discord_bots", side_effect=save_bots),
            patch.object(panel, "_log"),
        ):
            assert await panel.api_bot_discord_config_save(EmptyTokenRequest()) == {"ok": True}

    asyncio.run(exercise_empty_token_preserves_existing_value())
    assert writes[1][0][0]["token"] == "new-discord-token"
    print("PASS - Discord Bot 配置事务在线程池运行，慢读期间事件循环仍响应且 Token 不回传")


def check_loglevel_save_does_not_block_event_loop():
    write_started = threading.Event()
    release_write = threading.Event()
    writes = []

    class JsonRequest:
        async def json(self):
            return {"level": "debug"}

    def slow_atomic_write_json(path, data, indent=None):
        writes.append((path, data, indent))
        write_started.set()
        if not release_write.wait(2):
            raise AssertionError("测试未及时释放模拟日志级别写入")

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "atomic_write_json", side_effect=slow_atomic_write_json),
                patch.object(panel, "_log"),
            ):
                task = asyncio.create_task(panel.api_bot_loglevel_set(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(write_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_write.set()
                assert await task == {"ok": True, "level": "DEBUG"}
        finally:
            release_write.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert writes == [(panel.RUNTIME_LEVEL_FILE, {"level": "DEBUG"}, 2)]
    print("PASS - 日志级别文件在线程池写入，慢写期间事件循环仍响应且级别规范化")


def check_backup_restore_does_not_block_event_loop():
    restore_started = threading.Event()
    release_restore = threading.Event()
    restores = []

    class UploadRequest:
        headers = {"content-length": "12"}

        async def stream(self):
            yield b"backup-bytes"

    def slow_restore(payload, include_runtime_state, confirm_runtime_state):
        restores.append((payload, include_runtime_state, confirm_runtime_state))
        restore_started.set()
        if not release_restore.wait(2):
            raise AssertionError("测试未及时释放模拟备份恢复")
        return {"restored": ["settings.json"], "rollback": "rollback.zip"}

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "restore_backup_archive", side_effect=slow_restore),
                patch.object(panel, "_log"),
            ):
                task = asyncio.create_task(panel.api_backup_restore(UploadRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(restore_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_restore.set()
                assert await task == {
                    "ok": True,
                    "restored": ["settings.json"],
                    "msg": "恢复完成，已自动创建恢复前备份",
                }
        finally:
            release_restore.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert restores == [(b"backup-bytes", False, False)]
    print("PASS - 备份归档恢复在线程池执行，慢恢复期间事件循环仍响应且请求参数保留")


def check_autostart_set_does_not_block_event_loop():
    set_started = threading.Event()
    release_set = threading.Event()
    requested = []

    class JsonRequest:
        async def json(self):
            return {"enabled": True}

    def slow_set_autostart(enabled):
        requested.append(enabled)
        set_started.set()
        if not release_set.wait(2):
            raise AssertionError("测试未及时释放模拟开机自启更新")
        return True

    async def exercise():
        task = None
        try:
            with (
                patch.object(panel, "set_autostart", side_effect=slow_set_autostart),
                patch.object(panel, "get_autostart", return_value=True),
            ):
                task = asyncio.create_task(panel.api_autostart_set(JsonRequest()))
                started = await asyncio.wait_for(
                    asyncio.to_thread(set_started.wait, 1),
                    timeout=1,
                )
                assert started
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_set.set()
                assert await task == {"ok": True, "enabled": True}
        finally:
            release_set.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert requested == [True]
    class ValueRequest:
        def __init__(self, value):
            self.value = value

        async def json(self):
            return self.value

    with patch.object(panel, "_set_autostart_state") as set_state:
        for value in (
            {"enabled": "false"},
            {"enabled": 1},
            {"enabled": None},
            {},
        ):
            response = asyncio.run(panel.api_autostart_set(ValueRequest(value)))
            assert response.status_code == 400
            assert json.loads(response.body) == {
                "ok": False,
                "msg": "enabled 必须是布尔值",
            }
        set_state.assert_not_called()
    print("PASS - 开机自启文件操作在线程池执行，慢操作期间响应且拒绝非布尔值")


def check_discovered_sync_rejects_invalid_payloads():
    class BodyRequest:
        def __init__(self, raw_body):
            self.raw_body = raw_body

        async def body(self):
            return self.raw_body

    class JsonBodyRequest:
        def __init__(self, body):
            self.body_value = body

        async def body(self):
            return json.dumps(self.body_value).encode("utf-8")

    with patch.object(
        panel,
        "_sync_discovered_settings",
        return_value={"ok": True, "added": 0, "settings": {}},
    ) as sync_settings:
        for raw_body in (b"not-json", b"[]", b"null", b" \r\n\t "):
            response = asyncio.run(panel.api_settings_sync(BodyRequest(raw_body)))
            assert response.status_code == 400
            assert json.loads(response.body) == {"ok": False, "msg": "数据格式错误"}
        sync_settings.assert_not_called()

        # The documented optional empty body still uses the existing all-kind default.
        assert asyncio.run(panel.api_settings_sync(BodyRequest(b""))) == {
            "ok": True,
            "added": 0,
            "settings": {},
        }
        sync_settings.assert_called_once_with({})

    with patch.object(panel, "_load_settings_snapshot") as load_settings:
        invalid_bodies = (
            ({"kind": "unknown", "openids": ["group-1"]}, "无效的同步类型"),
            ({"kind": "groups", "openids": "group-1"}, "openids 必须是字符串列表"),
            ({"kind": "users", "openids": [123]}, "openids 必须是字符串列表"),
        )
        for body, message in invalid_bodies:
            response = asyncio.run(panel.api_settings_sync(JsonBodyRequest(body)))
            assert response.status_code == 400
            assert json.loads(response.body) == {"ok": False, "msg": message}
        load_settings.assert_not_called()
    print("PASS - 自动发现同步区分真空请求并验证 JSON、kind 和 openids")


def check_panel_write_routes_reject_non_object_json():
    class JsonRequest:
        def __init__(self, value):
            self.value = value

        async def json(self):
            return self.value

    routes = (
        panel.api_registrations_review,
        panel.api_autostart_set,
        panel.api_bot_loglevel_set,
    )
    with (
        patch.object(panel, "_review_registration") as review,
        patch.object(panel, "_set_autostart_state") as set_autostart,
        patch.object(panel, "_set_bot_loglevel") as set_loglevel,
    ):
        for route in routes:
            for value in ([], None):
                response = asyncio.run(route(JsonRequest(value)))
                assert response.status_code == 400
                assert json.loads(response.body) == {
                    "ok": False,
                    "msg": "数据格式错误",
                }
        review.assert_not_called()
        set_autostart.assert_not_called()
        set_loglevel.assert_not_called()
    print("PASS - 注册审核、自启和日志级别接口拒绝非对象 JSON 且不执行副作用")


def main():
    assert check_guard("/api/auth/bootstrap", {}).status_code == 200
    assert check_guard("/api/status", {}).status_code == 401
    assert check_guard(
        "/api/status", {"X-WRelay-Token": panel.PANEL_TOKEN}
    ).status_code == 200
    for path in (
        "/api/backup/export",
        "/api/backup/restore",
        "/api/translation/ai-test",
        "/api/translation/ai-test-record",
        "/api/translation/ai-env",
    ):
        assert check_guard(path, {}).status_code == 401
        assert check_guard(
            path, {"X-WRelay-Token": panel.PANEL_TOKEN}
        ).status_code == 200
    check_watchdog_uses_monotonic_time()
    check_inaccessible_bot_pid_fails_closed()
    check_bot_pid_creation_time_prevents_reuse()
    check_invalid_bot_pid_file_fails_closed()
    check_stop_waits_for_pid_exit()
    check_settings_save_does_not_block_event_loop()
    check_backfill_toggle_does_not_block_event_loop()
    check_scheduled_message_update_does_not_block_event_loop()
    check_discovered_settings_sync_does_not_block_event_loop()
    check_registration_review_does_not_block_event_loop()
    check_corrupt_registrations_are_reported_and_preserved()
    check_registration_list_uses_one_snapshot()
    check_corrupt_identity_store_is_reported_and_preserved()
    check_identity_list_uses_one_snapshot()
    check_corrupt_discovered_store_is_reported_and_preserved()
    check_ai_test_record_does_not_block_event_loop()
    check_translation_env_save_does_not_block_event_loop()
    check_ai_env_save_does_not_block_event_loop()
    check_qq_bot_config_save_does_not_block_event_loop()
    check_discord_bot_config_save_does_not_block_event_loop()
    check_loglevel_save_does_not_block_event_loop()
    check_backup_restore_does_not_block_event_loop()
    check_autostart_set_does_not_block_event_loop()
    check_discovered_sync_rejects_invalid_payloads()
    check_panel_write_routes_reject_non_object_json()
    print("PASS - 桌面管理 API 令牌边界")

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
    ai_limit_settings = dict(valid_settings)
    ai_limit_settings["ai"] = dict(valid_settings["ai"])
    ai_limit_settings["ai"]["timeout_seconds"] = 300
    ai_limit_settings["ai"]["max_output_tokens"] = 1_000_000
    assert panel._validate_settings(ai_limit_settings) is None
    ai_limit_settings["ai"]["timeout_seconds"] = 301
    assert "timeout_seconds" in panel._validate_settings(ai_limit_settings)
    ai_limit_settings["ai"]["timeout_seconds"] = 25
    ai_limit_settings["ai"]["max_output_tokens"] = 1_000_001
    assert "max_output_tokens" in panel._validate_settings(ai_limit_settings)
    ai_limit_settings["ai"]["max_output_tokens"] = 800
    ai_limit_settings["ai_profiles"] = [{
        "id": "profile-1",
        "name": "AI",
        "base_url": "https://example.test/v1",
        "model": "test-model",
        "api_key_env": "WRELAY_AI_API_KEY",
        "timeout_seconds": 301,
        "max_output_tokens": 800,
    }]
    ai_limit_settings["ai_active_profile"] = "profile-1"
    assert "timeout_seconds" in panel._validate_settings(ai_limit_settings)
    ai_limit_settings["ai_profiles"][0]["timeout_seconds"] = 25
    ai_limit_settings["ai_profiles"][0]["max_output_tokens"] = 1_000_001
    assert "max_output_tokens" in panel._validate_settings(ai_limit_settings)
    print("PASS - 配置结构校验拒绝非法载荷")

    existing_bots = [{"id": "A", "token": "A", "secret": "old-secret", "sandbox": False}]
    updated_bots = panel._prepare_qq_bots([
        {"appid": "A", "secret": ""},
        {"appid": "B", "secret": "new-secret"},
    ], existing_bots)
    assert [bot["id"] for bot in updated_bots] == ["A", "B"]
    assert updated_bots[0]["secret"] == "old-secret"
    assert updated_bots[0]["sandbox"] is False
    assert updated_bots[1]["secret"] == "new-secret"
    try:
        panel._prepare_qq_bots([{"appid": "B", "secret": ""}], existing_bots)
        raise AssertionError("新增 Bot 缺少 Secret 应被拒绝")
    except ValueError:
        pass
    assert panel._public_qq_bots(updated_bots) == [{"appid": "A"}, {"appid": "B"}]
    print("PASS - QQ Bot 多凭据保存保留旧 Secret 且不向前端泄露")

    with tempfile.TemporaryDirectory(prefix="wrelay_group_bot_test_") as tmp:
        old_data_dir = panel.DATA_DIR
        try:
            panel.DATA_DIR = tmp
            (Path(tmp) / "qq_group_openids.json").write_text(
                json.dumps({
                    "group_openids": ["GROUP_A", "GROUP_B"],
                    "group_bot_ids": {"GROUP_A": "BOT_A", "GROUP_B": "BOT_B"},
                }),
                encoding="utf-8",
            )
            settings = {"qq_group_openids": []}
            assert panel.sync_discovered_groups(settings) == 2
            assert settings["qq_group_openids"] == [
                {"openid": "GROUP_A", "enabled": False, "remark": "自动发现，点击启用", "bot_id": "BOT_A"},
                {"openid": "GROUP_B", "enabled": False, "remark": "自动发现，点击启用", "bot_id": "BOT_B"},
            ]
        finally:
            panel.DATA_DIR = old_data_dir
    print("PASS - 自动发现群保留 QQ Bot 归属")

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

    class FakeResponse:
        def __init__(self, body=b'{"choices":[{"message":{"content":"OK"}}]}'):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.body

    with patch.object(panel, "_read_env_secret", return_value="test-key"), patch(
        "urllib.request.urlopen", return_value=FakeResponse()
    ):
        assert panel._probe_ai_api({
            "base_url": "https://example.test/v1",
            "model": "test-model",
            "api_key_env": "WRELAY_TEST_KEY",
            "timeout_seconds": 5,
        }) == "test-model"
    local_probe_calls = []
    def fake_local_urlopen(request, timeout):
        local_probe_calls.append((request, timeout))
        return FakeResponse(b'{"message":{"content":"</think>\\nOK"}}')
    with patch.object(panel, "_read_env_secret", return_value="ollama"), patch(
        "urllib.request.urlopen", side_effect=fake_local_urlopen
    ):
        assert panel._probe_ai_api({
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "qwen3-4b-local:latest",
            "api_key_env": "WRELAY_TEST_KEY",
            "timeout_seconds": 5,
        }) == "qwen3-4b-local:latest"
    local_request, local_timeout = local_probe_calls[0]
    local_payload = json.loads(local_request.data.decode("utf-8"))
    assert local_request.full_url == "http://127.0.0.1:11434/api/chat"
    assert local_payload["think"] is False
    assert local_payload["options"]["num_predict"] == 8
    assert local_timeout == 90
    # 面板 API 需要把检测成功的模型名称返回给二级配置窗口，供其显示。
    class JsonRequest:
        def __init__(self, payload):
            self.payload = payload

        async def json(self):
            return self.payload

    with patch.object(panel, "_probe_ai_api", return_value="verified-model"):
        response = asyncio.run(panel.api_translation_ai_test(JsonRequest({
            "base_url": "https://example.test/v1",
            "model": "verified-model",
        })))
        assert response["model"] == "verified-model"
        assert response["diagnostic"]["category"] == "success"
        assert response["diagnostic"]["detail"] == "接口返回了有效内容"
        assert "endpoint" not in response["diagnostic"]
        assert "http_status" not in response["diagnostic"]

    with patch.object(
        panel,
        "_probe_ai_api",
        side_effect=panel.AiProbeError(
            "AI API 返回 HTTP 503：服务不可用",
            category="http",
            detail="服务不可用",
            http_status=503,
        ),
    ):
        response = asyncio.run(panel.api_translation_ai_test(JsonRequest({
            "base_url": "https://example.test/v1",
            "model": "failed-model",
        })))
        payload = json.loads(response.body)
        assert payload["diagnostic"]["category"] == "http"
        assert payload["diagnostic"]["detail"] == "服务不可用"
        assert "endpoint" not in payload["diagnostic"]
        assert "http_status" not in payload["diagnostic"]

    record_settings = panel.get_default_settings()
    record_settings["ai_profiles"] = [{
        "id": "profile-1",
        "name": "测试配置",
        "base_url": "https://example.test/v1",
        "model": "test-model",
        "api_key_env": "WRELAY_TEST_KEY",
        "timeout_seconds": 5,
        "max_output_tokens": 8,
    }]
    with patch.object(panel, "_load_settings_snapshot", return_value=(record_settings, "revision")), \
         patch.object(panel, "_settings_revision", return_value="revision"), \
         patch.object(panel, "save_settings") as save_settings:
        response = asyncio.run(panel.api_translation_ai_test_record(JsonRequest({
            "profile_id": "profile-1",
            "record": {
                "ok": False,
                "model": "test-model",
                "message": "检测失败",
                "at": 123,
                "diagnostic": {
                    "category": "empty_content",
                    "endpoint": "https://example.test/v1/chat/completions?token=hidden",
                    "http_status": 503,
                    "detail": "choices[0].message.content 为空",
                },
            },
        })))
        assert response["ok"] is True
        assert record_settings["ai_profiles"][0]["last_test"]["ok"] is False
        assert record_settings["ai_profiles"][0]["last_test"]["diagnostic"]["category"] == "empty_content"
        assert "endpoint" not in record_settings["ai_profiles"][0]["last_test"]["diagnostic"]
        assert "http_status" not in record_settings["ai_profiles"][0]["last_test"]["diagnostic"]
        save_settings.assert_called_once()
    with patch.object(panel, "_read_env_secret", return_value=""), patch(
        "urllib.request.urlopen", return_value=FakeResponse()
    ):
        assert panel._probe_ai_api({
            "base_url": "https://example.test/v1",
            "model": "test-model",
            "api_key_env": "WRELAY_TEST_KEY",
            "api_key": "typed-key",
            "timeout_seconds": 5,
        }) == "test-model"
    http_error = HTTPError(
        "https://example.test/v1/chat/completions",
        400,
        "Bad Request",
        {},
        io.BytesIO(b'{"error":{"message":"model is not supported"}}'),
    )
    with patch.object(panel, "_read_env_secret", return_value="test-key"), patch(
        "urllib.request.urlopen", side_effect=[
            http_error,
            FakeResponse(b'{"data":[{"id":"DeepSeek-V4.1-Flash"}]}'),
        ]
    ):
        try:
            panel._probe_ai_api({
                "base_url": "https://example.test/v1",
                "model": "test-model",
                "api_key_env": "WRELAY_TEST_KEY",
                "timeout_seconds": 5,
            })
        except RuntimeError as exc:
            assert str(exc) == "AI API 返回 HTTP 400：model is not supported；当前可用模型：DeepSeek-V4.1-Flash"
            assert exc.category == "http"
            assert exc.http_status == 400
            assert exc.detail == "model is not supported"
        else:
            raise AssertionError("HTTP 错误应包含服务端返回的安全说明")
    try:
        panel._probe_ai_api({"base_url": "", "model": "", "api_key_env": ""})
    except ValueError:
        pass
    else:
        raise AssertionError("缺失 AI API 配置应被拒绝")
    print("PASS - AI API 检测使用最小请求且不暴露密钥")


if __name__ == "__main__":
    main()
