"""验证安全备份与恢复的默认范围、回退备份和 ZIP 白名单。"""
import importlib.util
import io
import json
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wrelay_backup_panel", ROOT / "panel" / "管理面板.pyw")
panel = importlib.util.module_from_spec(spec)
saved_stdout = sys.stdout
spec.loader.exec_module(panel)
sys.stdout = saved_stdout


def archive(files, runtime=False):
    out = io.BytesIO()
    names = list(files)
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": "wrelay-backup", "version": 1, "files": names, "include_runtime_state": runtime}))
        for name, value in files.items():
            z.writestr(name, json.dumps(value))
    return out.getvalue()


def main():
    with tempfile.TemporaryDirectory(prefix="wrelay_backup_test_") as tmp:
        root = Path(tmp)
        old = (panel.SETTINGS_FILE, panel.DISCORD_LAST_FILE, panel.RETRY_PENDING_FILE, panel.RESTORE_BACKUP_DIR)
        try:
            panel.SETTINGS_FILE = str(root / "settings.json")
            panel.DISCORD_LAST_FILE = str(root / "discord_last.json")
            panel.RETRY_PENDING_FILE = str(root / "retry_pending.json")
            panel.RESTORE_BACKUP_DIR = str(root / "restore_backups")
            settings = panel.get_default_settings()
            Path(panel.SETTINGS_FILE).write_text(json.dumps(settings), encoding="utf-8")
            Path(panel.DISCORD_LAST_FILE).write_text(json.dumps({"cursor": "old"}), encoding="utf-8")
            Path(panel.RETRY_PENDING_FILE).write_text(json.dumps({"pending": ["old"]}), encoding="utf-8")

            with zipfile.ZipFile(io.BytesIO(panel.create_backup_archive()), "r") as z:
                assert set(z.namelist()) == {"manifest.json", "settings.json"}
            print("PASS - 默认导出仅含 settings.json")

            client = TestClient(panel.app)
            assert client.get("/api/backup/export").status_code == 401
            exported = client.get(
                "/api/backup/export",
                headers={"X-WRelay-Token": panel.PANEL_TOKEN},
            )
            assert exported.status_code == 200
            assert exported.headers["content-type"].startswith("application/zip")
            with zipfile.ZipFile(io.BytesIO(exported.content), "r") as z:
                assert set(z.namelist()) == {"manifest.json", "settings.json"}
            print("PASS - 备份导出接口受令牌保护并返回 ZIP")

            incoming = panel.get_default_settings()
            incoming["backfill_limit"] = 12
            panel.restore_backup_archive(archive({"settings.json": incoming}))
            assert json.loads(Path(panel.DISCORD_LAST_FILE).read_text(encoding="utf-8"))["cursor"] == "old"
            assert json.loads(Path(panel.RETRY_PENDING_FILE).read_text(encoding="utf-8"))["pending"] == ["old"]
            print("PASS - 默认恢复不触碰运行状态")

            restored = client.post(
                "/api/backup/restore",
                content=archive({"settings.json": incoming}),
                headers={
                    "Content-Type": "application/zip",
                    "X-WRelay-Token": panel.PANEL_TOKEN,
                },
            )
            assert restored.status_code == 200 and restored.json()["ok"] is True
            assert json.loads(Path(panel.DISCORD_LAST_FILE).read_text(encoding="utf-8"))["cursor"] == "old"
            print("PASS - 默认恢复接口不触碰运行状态")

            panel.restore_backup_archive(archive({
                "settings.json": incoming,
                "discord_last.json": {"cursor": "new"},
                "retry_pending.json": {"pending": ["new"]},
            }, True), True, True)
            backups = list((root / "restore_backups").glob("*.zip"))
            assert backups
            assert any(
                "discord_last.json" in zipfile.ZipFile(path).namelist()
                for path in backups
            )
            print("PASS - 高级恢复前创建回退备份")

            acquired = []
            original_lock = panel.json_file_lock
            original_rollback = panel._save_restore_rollback

            @contextmanager
            def recording_lock(path):
                acquired.append(path)
                yield

            def check_rollback(names):
                assert set(acquired) == {panel._backup_targets()[name] for name in names}
                return original_rollback(names)

            try:
                panel.json_file_lock = recording_lock
                panel.create_backup_archive(True)
                assert acquired == sorted(panel._backup_targets().values())
                acquired.clear()
                panel._save_restore_rollback = check_rollback
                panel.restore_backup_archive(archive({
                    "settings.json": incoming,
                    "discord_last.json": {"cursor": "locked"},
                }, True), True, True)
            finally:
                panel.json_file_lock = original_lock
                panel._save_restore_rollback = original_rollback
            print("PASS - 多文件导出和恢复在同一锁范围内执行")

            rollback_dir = root / "restore_backups"
            preserved = rollback_dir / "manual-backup.zip"
            preserved.write_bytes(b"manual")
            for index in range(12):
                (rollback_dir / ("restore-before-old-%02d.zip" % index)).write_bytes(b"old")
            panel.restore_backup_archive(archive({"settings.json": incoming}))
            automatic = list(rollback_dir.glob("restore-before-*.zip"))
            assert len(automatic) <= panel.RESTORE_BACKUP_RETENTION
            assert preserved.exists()
            print("PASS - 仅保留最近的自动回退备份")

            bad = archive({"settings.json": incoming, "../panel_auth.json": {}})
            try:
                panel.restore_backup_archive(bad)
            except ValueError:
                pass
            else:
                raise AssertionError("路径穿越未被拒绝")
            bad = archive({"settings.json": incoming, "panel_auth.json": {}})
            try:
                panel.restore_backup_archive(bad)
            except ValueError:
                pass
            else:
                raise AssertionError("非白名单文件未被拒绝")
            bad = archive({
                "settings.json": incoming,
                "discord_last.json": {"cursor": "new"},
            })
            try:
                panel.restore_backup_archive(bad, True, True)
            except ValueError:
                pass
            else:
                raise AssertionError("运行状态元数据不一致未被拒绝")
            print("PASS - 拒绝路径穿越和不允许文件名")
        finally:
            panel.SETTINGS_FILE, panel.DISCORD_LAST_FILE, panel.RETRY_PENDING_FILE, panel.RESTORE_BACKUP_DIR = old


if __name__ == "__main__":
    main()
