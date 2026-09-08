"""验证运行路径、配置缓存和跨进程 JSON 更新安全性。

所有测试数据均放在临时目录，不读取或修改 data/ 下的真实运行数据。
用法：.venv/Scripts/python.exe scripts/test_persistence_safety.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins import config as config_mod
from plugins.json_io import (
    ConcurrentWriteError,
    atomic_write_json,
    file_revision,
    load_json,
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


def main():
    check_runtime_paths()
    check_config_cache_freshness()
    check_json_concurrency()


if __name__ == "__main__":
    main()
