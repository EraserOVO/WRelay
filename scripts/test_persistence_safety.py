"""验证运行路径、配置缓存和跨进程 JSON 更新安全性。

所有测试数据均放在临时目录，不读取或修改 data/ 下的真实运行数据。
用法：.venv/Scripts/python.exe scripts/test_persistence_safety.py
"""
import asyncio
import builtins
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins import config as config_mod
from plugins import history as history_mod
from plugins import json_io as json_io_mod
from plugins import stats as stats_mod
from plugins.json_io import (
    ConcurrentWriteError,
    atomic_write_json,
    file_revision,
    load_json,
    update_json,
)
from plugins.paths import DATA_DIR, PROJECT_ROOT


def check_runtime_paths():
    """从项目外的工作目录导入路径模块，仍应解析到项目 data 目录。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_cwd_test_") as tmp:
        code = (
            "import sys; "
            "sys.path.insert(0, %r); "
            "from plugins.paths import PROJECT_ROOT, DATA_DIR; "
            "assert str(PROJECT_ROOT) == %r; "
            "assert str(DATA_DIR) == %r"
        ) % (str(ROOT), str(PROJECT_ROOT), str(DATA_DIR))
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=tmp,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    assert result.returncode == 0, result.stderr
    print("PASS - 运行时路径不依赖当前工作目录")


def check_config_cache_freshness():
    """即使外部改写保持相同 mtime/size，内容变化也必须刷新缓存。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_settings_test_") as tmp:
        path = Path(tmp) / "settings.json"
        first = {
            "qq_group_openids": [{"openid": "old", "enabled": True}],
            "discord_channels": [],
        }
        second = {
            "qq_group_openids": [{"openid": "new", "enabled": True}],
            "discord_channels": [],
        }
        path.write_text(json.dumps(first, separators=(",", ":")), encoding="utf-8")

        old_path = config_mod.SETTINGS_FILE
        old_cache = (
            config_mod._settings_cache,
            config_mod._settings_cache_mtime,
            config_mod._settings_cache_size,
            config_mod._settings_cache_revision,
        )
        try:
            config_mod.SETTINGS_FILE = str(path)
            config_mod._settings_cache = None
            config_mod._settings_cache_mtime = None
            config_mod._settings_cache_size = None
            config_mod._settings_cache_revision = None

            assert config_mod.get_active_groups() == ["old"]
            mtime = path.stat().st_mtime_ns
            path.write_text(json.dumps(second, separators=(",", ":")), encoding="utf-8")
            os.utime(path, ns=(mtime, mtime))
            assert config_mod.get_active_groups() == ["new"]
        finally:
            config_mod.SETTINGS_FILE = old_path
            (
                config_mod._settings_cache,
                config_mod._settings_cache_mtime,
                config_mod._settings_cache_size,
                config_mod._settings_cache_revision,
            ) = old_cache
    print("PASS - 配置缓存按内容版本刷新")


def check_concurrent_config_cache_publication():
    """较旧的并行读取不能用新版本指纹发布旧 settings 内容。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_settings_race_test_") as tmp:
        path = Path(tmp) / "settings.json"
        path.write_text("{}", encoding="utf-8")
        old_path = config_mod.SETTINGS_FILE
        old_cache = (
            config_mod._settings_cache,
            config_mod._settings_cache_mtime,
            config_mod._settings_cache_size,
            config_mod._settings_cache_revision,
        )
        old_settings = config_mod._default_settings()
        old_settings["qq_group_openids"] = [{
            "openid": "stale-group",
            "enabled": True,
            "remark": "",
            "bot_id": "",
        }]
        new_settings = config_mod._default_settings()
        new_settings["qq_group_openids"] = [{
            "openid": "fresh-group",
            "enabled": True,
            "remark": "",
            "bot_id": "",
        }]
        stale_validating = threading.Event()
        fresh_normalizing = threading.Event()
        stale_finished = threading.Event()
        failures = []
        original_validate = config_mod.validate_settings
        original_ensure_groups = config_mod._ensure_forwarding_groups

        def revision(_path):
            if threading.current_thread().name == "stale-settings-reader":
                return "revision-stale"
            return "revision-fresh"

        def load_settings(_path, default=None):
            if threading.current_thread().name == "stale-settings-reader":
                return old_settings
            return new_settings

        def validate(data):
            if threading.current_thread().name == "stale-settings-reader":
                stale_validating.set()
                if not fresh_normalizing.wait(2):
                    raise AssertionError("较新配置读取未进入归一化阶段")
            return original_validate(data)

        def ensure_groups(data):
            if threading.current_thread().name == "fresh-settings-reader":
                fresh_normalizing.set()
                if not stale_finished.wait(2):
                    raise AssertionError("较旧配置读取未完成")
            return original_ensure_groups(data)

        def run_load():
            try:
                config_mod._load_settings()
            except Exception as exc:
                failures.append(exc)

        def run_stale_load():
            try:
                config_mod._load_settings()
            except Exception as exc:
                failures.append(exc)
            finally:
                stale_finished.set()

        try:
            config_mod.SETTINGS_FILE = str(path)
            config_mod._settings_cache = None
            config_mod._settings_cache_mtime = None
            config_mod._settings_cache_size = None
            config_mod._settings_cache_revision = None
            with (
                mock.patch.object(config_mod, "file_revision", side_effect=revision),
                mock.patch.object(config_mod, "load_json", side_effect=load_settings),
                mock.patch.object(config_mod, "validate_settings", side_effect=validate),
                mock.patch.object(config_mod, "_ensure_forwarding_groups", side_effect=ensure_groups),
            ):
                stale = threading.Thread(
                    target=run_stale_load,
                    name="stale-settings-reader",
                )
                fresh = threading.Thread(
                    target=run_load,
                    name="fresh-settings-reader",
                )
                stale.start()
                assert stale_validating.wait(2)
                fresh.start()
                stale.join(3)
                fresh.join(3)
                assert not stale.is_alive() and not fresh.is_alive()
                assert failures == []
                assert config_mod.get_active_groups() == ["fresh-group"]
        finally:
            config_mod.SETTINGS_FILE = old_path
            (
                config_mod._settings_cache,
                config_mod._settings_cache_mtime,
                config_mod._settings_cache_size,
                config_mod._settings_cache_revision,
            ) = old_cache
    print("PASS - 并发设置读取不会把旧内容绑定到新缓存版本")


def check_concurrent_settings_bootstrap_preserves_writer_config():
    """首次默认初始化与并发配置保存竞争时，不覆盖已创建的配置。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_settings_bootstrap_test_") as tmp:
        path = Path(tmp) / "settings.json"
        old_path = config_mod.SETTINGS_FILE
        external_settings = {
            "qq_group_openids": [{"openid": "external-group", "enabled": True}],
        }

        def create_external_config_before_default_write(
            target, data, indent=4, expected_revision=None
        ):
            if expected_revision == "missing":
                atomic_write_json(target, external_settings, indent=2)
            return atomic_write_json(
                target,
                data,
                indent=indent,
                expected_revision=expected_revision,
            )

        try:
            config_mod.SETTINGS_FILE = str(path)
            with mock.patch.object(
                config_mod,
                "atomic_write_json",
                side_effect=create_external_config_before_default_write,
            ):
                config_mod._ensure_settings_file()
            assert load_json(path) == external_settings
        finally:
            config_mod.SETTINGS_FILE = old_path
    print("PASS - 首次默认初始化不覆盖竞争写入的设置")


def check_malformed_settings_lists_fall_back():
    """坏列表类型应进入统一安全降级，而不在迁移遍历阶段抛异常。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_bad_settings_list_test_") as tmp:
        path = Path(tmp) / "settings.json"
        old_path = config_mod.SETTINGS_FILE
        old_cache = (
            config_mod._settings_cache,
            config_mod._settings_cache_mtime,
            config_mod._settings_cache_size,
            config_mod._settings_cache_revision,
        )
        try:
            config_mod.SETTINGS_FILE = str(path)
            for key in (
                "qq_group_openids",
                "discord_channels",
                "x_rss_sources",
                "bilibili_sources",
            ):
                settings = config_mod._default_settings()
                settings[key] = None
                original = json.dumps(settings, ensure_ascii=False).encode("utf-8")
                path.write_bytes(original)
                config_mod._settings_cache = None
                config_mod._settings_cache_mtime = None
                config_mod._settings_cache_size = None
                config_mod._settings_cache_revision = None

                assert config_mod.get_active_groups() == list(config_mod.QQ_GROUP_OPENIDS)
                assert path.read_bytes() == original
        finally:
            config_mod.SETTINGS_FILE = old_path
            (
                config_mod._settings_cache,
                config_mod._settings_cache_mtime,
                config_mod._settings_cache_size,
                config_mod._settings_cache_revision,
            ) = old_cache
    print("PASS - settings 列表类型错误走内存安全降级并保留原文件")


def check_non_finite_legacy_schedule_values():
    """旧版间隔迁移遇到 JSON Infinity 时应使用保守间隔而非崩溃。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_legacy_schedule_test_") as tmp:
        path = Path(tmp) / "settings.json"
        old_path = config_mod.SETTINGS_FILE
        old_cache = (
            config_mod._settings_cache,
            config_mod._settings_cache_mtime,
            config_mod._settings_cache_size,
            config_mod._settings_cache_revision,
        )
        try:
            config_mod.SETTINGS_FILE = str(path)
            cases = (
                ({"enabled": True, "content": "legacy daily", "every_days": float("inf")}, 1, 0),
                ({"enabled": True, "content": "legacy hourly", "interval": float("inf")}, 0, 1),
            )
            for legacy_config, expected_days, expected_hours in cases:
                settings = config_mod._default_settings()
                settings["scheduled_message"] = legacy_config
                original = json.dumps(
                    settings,
                    ensure_ascii=False,
                    allow_nan=True,
                ).encode("utf-8")
                path.write_bytes(original)
                config_mod._settings_cache = None
                config_mod._settings_cache_mtime = None
                config_mod._settings_cache_size = None
                config_mod._settings_cache_revision = None

                schedule = config_mod.get_scheduled_message_config()
                assert len(schedule["messages"]) == 1
                assert schedule["messages"][0]["interval_days"] == expected_days
                assert schedule["messages"][0]["interval_hours"] == expected_hours
                assert path.read_bytes() == original
        finally:
            config_mod.SETTINGS_FILE = old_path
            (
                config_mod._settings_cache,
                config_mod._settings_cache_mtime,
                config_mod._settings_cache_size,
                config_mod._settings_cache_revision,
            ) = old_cache
    print("PASS - 非有限旧版定时间隔按安全默认值迁移并保留原文件")


def check_json_concurrency():
    """多进程读改写不可丢更新，过期版本不可覆盖新版本。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_json_test_") as tmp:
        path = Path(tmp) / "counter.json"
        child = """\
import sys
sys.path.insert(0, %r)
from plugins.json_io import update_json
path = sys.argv[1]
def mutate(data):
    data['count'] = int(data.get('count', 0)) + 1
    return True
for _ in range(20):
    update_json(path, mutate, default={}, indent=2)
""" % str(ROOT)
        processes = [
            subprocess.Popen([sys.executable, "-c", child, str(path)])
            for _ in range(8)
        ]
        assert all(process.wait() == 0 for process in processes)
        assert load_json(path, default={}).get("count") == 160

        revision = file_revision(path)
        atomic_write_json(path, {"count": 161}, expected_revision=revision)
        try:
            atomic_write_json(path, {"count": 162}, expected_revision=revision)
        except ConcurrentWriteError:
            pass
        else:
            raise AssertionError("过期版本未被拒绝")
    print("PASS - 多进程 JSON 更新与版本冲突保护")


def check_file_revision_does_not_conflate_io_error_with_missing():
    """An unreadable existing file must not pass an expected-missing check."""
    with tempfile.TemporaryDirectory(prefix="wrelay_revision_io_test_") as tmp:
        path = Path(tmp) / "state.json"
        original = b'{"stable": true}'
        path.write_bytes(original)
        real_open = builtins.open

        def deny_revision_read(file, mode="r", *args, **kwargs):
            if os.fspath(file) == str(path) and mode == "rb":
                raise PermissionError("simulated unreadable existing file")
            return real_open(file, mode, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=deny_revision_read):
            try:
                file_revision(path)
            except PermissionError as exc:
                assert "simulated unreadable" in str(exc)
            else:
                raise AssertionError("read error was reported as a missing file")

            try:
                atomic_write_json(path, {"replacement": True}, expected_revision="missing")
            except PermissionError as exc:
                assert "simulated unreadable" in str(exc)
            else:
                raise AssertionError("unreadable existing file passed missing revision check")

        assert path.read_bytes() == original
        assert not list(Path(tmp).glob("state.json.*.tmp"))

        missing = Path(tmp) / "new.json"
        assert file_revision(missing) == "missing"
        atomic_write_json(missing, {"created": True}, expected_revision="missing")
        assert json.loads(missing.read_text(encoding="utf-8")) == {"created": True}
    print("PASS - 文件版本读取错误不再伪装为缺失，首次创建仍可使用 missing 版本")


def check_corrupt_json_is_preserved():
    """读改写遇到损坏文件时必须中止，不能用默认值覆盖原文。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_corrupt_json_test_") as tmp:
        path = Path(tmp) / "records.json"
        original = b'{"records": [broken'
        path.write_bytes(original)
        called = []

        def mutate(data):
            called.append(True)
            data["new"] = True

        try:
            update_json(path, mutate, default={}, indent=2)
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError("损坏 JSON 未中止读改写")
        assert not called
        assert path.read_bytes() == original
    print("PASS - 损坏 JSON 不会被默认结构覆盖")


def check_wrong_type_json_is_preserved():
    """语法有效但顶层类型错误时也应中止读改写。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_wrong_type_json_test_") as tmp:
        path = Path(tmp) / "records.json"
        original = b'["unexpected-list"]'
        path.write_bytes(original)
        called = []

        def mutate(data):
            called.append(True)
            data["new"] = True

        try:
            update_json(path, mutate, default={}, indent=2)
        except ValueError as exc:
            assert "expected dict, got list" in str(exc), exc
        else:
            raise AssertionError("wrong JSON top-level type must stop the update")
        assert not called
        assert path.read_bytes() == original
    print("PASS - 顶层类型错误的 JSON 不会被默认结构覆盖")


def check_failed_atomic_write_cleans_temp():
    """序列化和替换失败后应清理临时文件并保留旧目标。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_atomic_failure_test_") as tmp:
        path = Path(tmp) / "state.json"
        original = b'{"stable": true}'
        path.write_bytes(original)

        try:
            atomic_write_json(path, {"not_serializable": object()})
        except TypeError:
            pass
        else:
            raise AssertionError("不可序列化数据应使 JSON 写入失败")
        assert path.read_bytes() == original
        assert not list(Path(tmp).glob("state.json.*.tmp"))

        with mock.patch.object(json_io_mod.os, "replace", side_effect=OSError("simulated replace failure")):
            try:
                atomic_write_json(path, {"replacement": True})
            except OSError as exc:
                assert "simulated replace failure" in str(exc)
            else:
                raise AssertionError("替换错误应继续抛出")
        assert path.read_bytes() == original
        assert not list(Path(tmp).glob("state.json.*.tmp"))
    print("PASS - 原子写入失败时保留目标并清理临时文件")


def check_corrupt_discord_cursor_is_preserved():
    """损坏或错误类型的 Discord 游标不得退化为空对象并覆盖原文。"""
    with tempfile.TemporaryDirectory(prefix="wrelay_cursor_corruption_test_") as tmp:
        path = Path(tmp) / "discord_last.json"
        original_path = history_mod.LAST_MESSAGE_FILE
        original_cache = (
            history_mod._last,
            history_mod._last_mtime,
            history_mod._last_size,
        )

        def clear_cache():
            history_mod._last = None
            history_mod._last_mtime = None
            history_mod._last_size = None

        async def exercise_invalid_files():
            invalid_files = (
                b'{"broken": [',
                b'[]',
                json.dumps({"C1": {"G1": []}}).encode("utf-8"),
                json.dumps({"C1": "not-a-cursor"}).encode("utf-8"),
                json.dumps({"C1": {"G1": "9" * 40}}).encode("utf-8"),
                json.dumps({"C1": {"G1": True}}).encode("utf-8"),
            )
            for original in invalid_files:
                path.write_bytes(original)
                clear_cache()
                try:
                    history_mod.load_last_messages()
                except RuntimeError as exc:
                    assert "Discord 去重游标文件" in str(exc), exc
                else:
                    raise AssertionError("invalid cursor data must stop loading")

                try:
                    await history_mod.update_last_messages(
                        lambda data: data.update({"C1": {"G1": "101"}}) or True
                    )
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("invalid cursor data must stop updates")
                assert path.read_bytes() == original

            for compatible in (
                {"C1": "101"},
                {"C1": {"G1": "102", "*": "101"}},
            ):
                path.write_text(json.dumps(compatible), encoding="utf-8")
                clear_cache()
                assert history_mod.load_last_messages() == compatible

        try:
            history_mod.LAST_MESSAGE_FILE = str(path)
            asyncio.run(exercise_invalid_files())
        finally:
            history_mod.LAST_MESSAGE_FILE = original_path
            (
                history_mod._last,
                history_mod._last_mtime,
                history_mod._last_size,
            ) = original_cache
    print("PASS - 损坏 Discord 去重游标会中止读改写并保留原文件")


def check_cancelled_cursor_write_keeps_serialization():
    """Cancellation must not release the cursor lock while its disk write runs."""
    with tempfile.TemporaryDirectory(prefix="wrelay_cursor_cancel_test_") as tmp:
        path = Path(tmp) / "discord_last.json"
        write_started = threading.Event()
        release_write = threading.Event()

        def read_snapshot():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}

        def slow_save_snapshot(data):
            write_started.set()
            if not release_write.wait(2):
                raise AssertionError("测试未释放模拟游标写入")
            atomic_write_json(path, data, indent=4)

        async def exercise_cancellation():
            with (
                mock.patch.object(history_mod, "load_last_messages", side_effect=read_snapshot),
                mock.patch.object(history_mod, "save_last_messages", side_effect=slow_save_snapshot),
            ):
                first = asyncio.create_task(
                    history_mod.update_last_messages(
                        lambda data: data.update({"C1": {"G1": "100"}}) or True
                    )
                )
                try:
                    started = await asyncio.wait_for(
                        asyncio.to_thread(write_started.wait, 1), timeout=1
                    )
                    assert started
                    first.cancel()
                    await asyncio.sleep(0)
                    assert not first.done(), "取消提前释放了游标写锁"

                    second = asyncio.create_task(
                        history_mod.update_last_messages(
                            lambda data: data.setdefault("C1", {}).update(
                                {"G2": "200"}
                            ) or True
                        )
                    )
                    await asyncio.sleep(0)
                    assert not second.done(), "后续游标更新越过了仍在执行的写入"

                    release_write.set()
                    results = await asyncio.gather(
                        first,
                        second,
                        return_exceptions=True,
                    )
                    assert isinstance(results[0], asyncio.CancelledError), results
                    assert results[1] is True, results
                    assert json.loads(path.read_text(encoding="utf-8")) == {
                        "C1": {"G1": "100", "G2": "200"}
                    }
                finally:
                    release_write.set()
                    if not first.done():
                        first.cancel()
                        await asyncio.gather(first, return_exceptions=True)

        asyncio.run(exercise_cancellation())
    print("PASS - 游标写入取消后仍持锁至落盘完成，后续更新无丢失")


def check_invalid_stats_counters_recover():
    """单个坏统计值归零后，统计写入应能继续。"""
    assert stats_mod._safe_counter(True) == 0
    assert stats_mod._safe_counter(False) == 0
    assert stats_mod._safe_counter(-3) == 0
    with tempfile.TemporaryDirectory(prefix="wrelay_stats_safety_test_") as tmp:
        path = Path(tmp) / "stats.json"
        path.write_text(
            json.dumps({
                "today": time.strftime("%Y-%m-%d"),
                "total_forwarded": True,
                "total_failed": "invalid",
                "today_forwarded": [],
                "today_failed": "2",
            }),
            encoding="utf-8",
        )
        original_path = stats_mod.STATS_FILE
        original_cache = stats_mod._stats
        try:
            stats_mod.STATS_FILE = str(path)
            stats_mod._stats = None
            asyncio.run(stats_mod.record(True))
            saved = json.loads(path.read_text(encoding="utf-8"))
            assert saved["total_forwarded"] == 1
            assert saved["total_failed"] == 0
            assert saved["today_forwarded"] == 1
            assert saved["today_failed"] == 2
        finally:
            stats_mod.STATS_FILE = original_path
            stats_mod._stats = original_cache
    print("PASS - 异常及布尔统计计数归零后继续累计")


def check_corrupt_stats_file_is_preserved_and_nonfatal():
    """Bad stats JSON stays intact and cannot break the message caller."""
    with tempfile.TemporaryDirectory(prefix="wrelay_corrupt_stats_test_") as tmp:
        path = Path(tmp) / "stats.json"
        original_path = stats_mod.STATS_FILE
        original_cache = stats_mod._stats
        original_error = stats_mod._stats_write_error
        try:
            stats_mod.STATS_FILE = str(path)
            for corrupt in (b"{invalid", b"[]"):
                path.write_bytes(corrupt)
                stats_mod._stats = None
                stats_mod._stats_write_error = ""
                asyncio.run(stats_mod.record(True))
                assert path.read_bytes() == corrupt
                assert stats_mod.get_stats()["total_forwarded"] == 1
                asyncio.run(stats_mod.record(False))
                assert path.read_bytes() == corrupt
                counters = stats_mod.get_stats()
                assert counters["total_forwarded"] == 1
                assert counters["total_failed"] == 1
        finally:
            stats_mod.STATS_FILE = original_path
            stats_mod._stats = original_cache
            stats_mod._stats_write_error = original_error
    print("PASS - 损坏统计文件保留原文，计数落盘失败不影响调用方")


def check_stats_first_load_does_not_block_event_loop():
    """The first asynchronous statistics read must stay off the event loop."""
    with tempfile.TemporaryDirectory(prefix="wrelay_stats_read_test_") as tmp:
        path = Path(tmp) / "stats.json"
        path.write_text(
            json.dumps({
                "today": time.strftime("%Y-%m-%d"),
                "total_forwarded": 4,
                "total_failed": 0,
                "today_forwarded": 2,
                "today_failed": 0,
            }),
            encoding="utf-8",
        )
        original_path = stats_mod.STATS_FILE
        original_cache = stats_mod._stats
        read_started = threading.Event()
        release_read = threading.Event()

        def slow_load(_path, default=None):
            read_started.set()
            if not release_read.wait(2):
                raise AssertionError("测试未释放模拟统计读取")
            return json.loads(path.read_text(encoding="utf-8"))

        async def exercise():
            with mock.patch.object(stats_mod, "load_json", side_effect=slow_load):
                task = asyncio.create_task(stats_mod.record(True))
                try:
                    started = await asyncio.wait_for(
                        asyncio.to_thread(read_started.wait, 1),
                        timeout=1,
                    )
                    assert started
                    loop_tick = asyncio.Event()
                    asyncio.get_running_loop().call_soon(loop_tick.set)
                    await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                    release_read.set()
                    await task
                finally:
                    release_read.set()
                    if not task.done():
                        await asyncio.gather(task, return_exceptions=True)

        try:
            stats_mod.STATS_FILE = str(path)
            stats_mod._stats = None
            asyncio.run(exercise())
            saved = json.loads(path.read_text(encoding="utf-8"))
            assert saved["total_forwarded"] == 5
            assert saved["today_forwarded"] == 3
        finally:
            stats_mod.STATS_FILE = original_path
            stats_mod._stats = original_cache
    print("PASS - 统计首次读取在线程池执行，事件循环保持响应")


def check_cancelled_stats_write_keeps_counter_order():
    """A later counter snapshot must not be overtaken by a canceled write."""
    with tempfile.TemporaryDirectory(prefix="wrelay_stats_cancel_test_") as tmp:
        path = Path(tmp) / "stats.json"
        original_path = stats_mod.STATS_FILE
        original_cache = stats_mod._stats
        write_started = threading.Event()
        release_write = threading.Event()
        written_snapshots = []

        def slow_write_snapshot(_path, snapshot):
            written_snapshots.append(dict(snapshot))
            write_started.set()
            if not release_write.wait(2):
                raise AssertionError("测试未释放模拟统计落盘")
            json_io_mod.update_json(
                _path,
                lambda data: data.update(snapshot) or True,
                default={},
                indent=2,
            )

        async def exercise_cancellation():
            first = second = None
            try:
                with mock.patch.object(
                    stats_mod,
                    "_write_stats_snapshot",
                    side_effect=slow_write_snapshot,
                ):
                    first = asyncio.create_task(stats_mod.record(True))
                    started = await asyncio.wait_for(
                        asyncio.to_thread(write_started.wait, 1), timeout=1
                    )
                    assert started
                    first.cancel()
                    await asyncio.sleep(0)
                    assert not first.done(), "统计写入取消提前释放了计数锁"

                    second = asyncio.create_task(stats_mod.record(True))
                    await asyncio.sleep(0)
                    assert not second.done(), "后续统计快照越过了仍在落盘的写入"

                    release_write.set()
                    results = await asyncio.gather(
                        first,
                        second,
                        return_exceptions=True,
                    )
                    assert isinstance(results[0], asyncio.CancelledError), results
                    assert results[1] is None, results
                    assert [item["total_forwarded"] for item in written_snapshots] == [1, 2]
            finally:
                release_write.set()
                for task in (first, second):
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

        try:
            stats_mod.STATS_FILE = str(path)
            stats_mod._stats = None
            asyncio.run(exercise_cancellation())
            saved = json.loads(path.read_text(encoding="utf-8"))
            assert saved["total_forwarded"] == 2
            assert saved["today_forwarded"] == 2
        finally:
            stats_mod.STATS_FILE = original_path
            stats_mod._stats = original_cache
    print("PASS - 统计写入取消后持锁至落盘，成功计数快照顺序不回退")


def check_thread_io_failure_preserves_cancellation():
    """A failed worker must not replace cancellation already requested by its caller."""
    started = threading.Event()
    release = threading.Event()

    def failing_io():
        started.set()
        if not release.wait(2):
            raise AssertionError("测试未释放模拟线程池 I/O")
        raise OSError("simulated disk failure")

    def failing_io_without_cancel():
        raise OSError("uncancelled failure")

    async def exercise():
        task = asyncio.create_task(json_io_mod.run_in_thread_to_completion(failing_io))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=1)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "取消不应在底层 I/O 完成前结束"
            release.set()
            result = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError), result

            try:
                await json_io_mod.run_in_thread_to_completion(failing_io_without_cancel)
            except OSError as exc:
                assert str(exc) == "uncancelled failure"
            else:
                raise AssertionError("未取消的线程池 I/O 异常应继续传播")
        finally:
            release.set()
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    print("PASS - 线程池操作失败不覆盖已请求的取消，未取消时仍传播 I/O 异常")


def main():
    check_runtime_paths()
    check_config_cache_freshness()
    check_concurrent_config_cache_publication()
    check_concurrent_settings_bootstrap_preserves_writer_config()
    check_malformed_settings_lists_fall_back()
    check_non_finite_legacy_schedule_values()
    check_json_concurrency()
    check_file_revision_does_not_conflate_io_error_with_missing()
    check_corrupt_json_is_preserved()
    check_wrong_type_json_is_preserved()
    check_failed_atomic_write_cleans_temp()
    check_corrupt_discord_cursor_is_preserved()
    check_cancelled_cursor_write_keeps_serialization()
    check_invalid_stats_counters_recover()
    check_corrupt_stats_file_is_preserved_and_nonfatal()
    check_stats_first_load_does_not_block_event_loop()
    check_cancelled_stats_write_keeps_counter_order()
    check_thread_io_failure_preserves_cancellation()


if __name__ == "__main__":
    main()
