"""验证安全备份与恢复的默认范围、回退备份和 ZIP 白名单。API 检查使用 HTTPX ASGI transport。"""
import asyncio
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
import httpx


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wrelay_backup_panel", ROOT / "panel" / "管理面板.pyw")
panel = importlib.util.module_from_spec(spec)
saved_stdout = sys.stdout
spec.loader.exec_module(panel)
sys.stdout = saved_stdout


def archive(files, runtime=False, version=1, manifest_runtime=None):
    out = io.BytesIO()
    names = list(files)
    include_runtime_state = runtime if manifest_runtime is None else manifest_runtime
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("manifest.json", json.dumps({
            "format": "wrelay-backup",
            "version": version,
            "files": names,
            "include_runtime_state": include_runtime_state,
        }))
        for name, value in files.items():
            z.writestr(name, json.dumps(value))
    return out.getvalue()


async def api_request(method, path, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=panel.app),
        base_url="http://127.0.0.1",
    ) as client:
        return await client.request(method, path, **kwargs)


async def main():
    with tempfile.TemporaryDirectory(prefix="wrelay_backup_test_") as tmp:
        root = Path(tmp)
        old = (
            panel.SETTINGS_FILE,
            panel.DISCORD_LAST_FILE,
            panel.RETRY_PENDING_FILE,
            panel.DELIVERY_DEDUP_DB_FILE,
            panel.RESTORE_BACKUP_DIR,
        )
        try:
            panel.SETTINGS_FILE = str(root / "settings.json")
            panel.DISCORD_LAST_FILE = str(root / "discord_last.json")
            panel.RETRY_PENDING_FILE = str(root / "retry_pending.json")
            panel.DELIVERY_DEDUP_DB_FILE = str(root / "delivery_dedup.sqlite3")
            panel.RESTORE_BACKUP_DIR = str(root / "restore_backups")
            settings = panel.get_default_settings()
            Path(panel.SETTINGS_FILE).write_text(json.dumps(settings), encoding="utf-8")
            Path(panel.DISCORD_LAST_FILE).write_text(json.dumps({"cursor": "old"}), encoding="utf-8")
            Path(panel.RETRY_PENDING_FILE).write_text(json.dumps({"pending": ["old"]}), encoding="utf-8")
            connection = sqlite3.connect(panel.DELIVERY_DEDUP_DB_FILE)
            try:
                connection.execute(
                    "CREATE TABLE delivered_items (identity_key TEXT NOT NULL, "
                    "group_id TEXT NOT NULL, delivered_at REAL NOT NULL, "
                    "PRIMARY KEY(identity_key, group_id))"
                )
                connection.execute(
                    "INSERT INTO delivered_items VALUES (?, ?, ?)",
                    ("x:123", "G1", 123.0),
                )
                connection.commit()
            finally:
                connection.close()

            with zipfile.ZipFile(io.BytesIO(panel.create_backup_archive()), "r") as z:
                assert set(z.namelist()) == {"manifest.json", "settings.json"}
            print("PASS - 默认导出仅含 settings.json")

            assert (await api_request("GET", "/api/backup/export")).status_code == 401
            exported = await api_request(
                "GET",
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

            restored = await api_request(
                "POST",
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

            runtime_archive = panel.create_backup_archive(True)
            with zipfile.ZipFile(io.BytesIO(runtime_archive), "r") as z:
                assert "delivery_dedup.sqlite3" in z.namelist()
            connection = sqlite3.connect(panel.DELIVERY_DEDUP_DB_FILE)
            try:
                connection.execute("DELETE FROM delivered_items")
            finally:
                connection.close()
            panel.restore_backup_archive(runtime_archive, True, True)
            connection = sqlite3.connect(panel.DELIVERY_DEDUP_DB_FILE)
            try:
                row = connection.execute(
                    "SELECT identity_key, group_id FROM delivered_items"
                ).fetchone()
            finally:
                connection.close()
            assert row == ("x:123", "G1")
            print("PASS - 运行状态备份用一致 SQLite 快照保存并恢复跨来源去重记录")

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

            before_runtime_restore = {
                panel.SETTINGS_FILE: Path(panel.SETTINGS_FILE).read_bytes(),
                panel.DISCORD_LAST_FILE: Path(panel.DISCORD_LAST_FILE).read_bytes(),
                panel.RETRY_PENDING_FILE: Path(panel.RETRY_PENDING_FILE).read_bytes(),
                panel.DELIVERY_DEDUP_DB_FILE: Path(panel.DELIVERY_DEDUP_DB_FILE).read_bytes(),
            }
            blocked_settings = panel.get_default_settings()
            blocked_settings["backfill_limit"] = 13
            with mock.patch.object(
                panel,
                "bot_status",
                return_value={"running": True, "pid": 123, "managed": True},
            ):
                blocked = await api_request(
                    "POST",
                    "/api/backup/restore?include_runtime_state=true&confirm_runtime_state=true",
                    content=archive({
                        "settings.json": blocked_settings,
                        "discord_last.json": {"cursor": "blocked"},
                        "retry_pending.json": {"pending": ["blocked"]},
                    }, True),
                    headers={
                        "Content-Type": "application/zip",
                        "X-WRelay-Token": panel.PANEL_TOKEN,
                    },
                )
            assert blocked.status_code == 409, blocked.text
            assert all(
                Path(path).read_bytes() == content
                for path, content in before_runtime_restore.items()
            )
            print("PASS - 机器人运行中拒绝恢复游标和重试队列")

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
            for bad_manifest in (
                archive({"settings.json": incoming}, version=True),
                archive({"settings.json": incoming}, manifest_runtime="false"),
            ):
                try:
                    panel.restore_backup_archive(bad_manifest)
                except ValueError:
                    pass
                else:
                    raise AssertionError("非严格类型的清单字段未被拒绝")
            print("PASS - 拒绝路径穿越、非白名单文件及错误清单类型")
        finally:
            (
                panel.SETTINGS_FILE,
                panel.DISCORD_LAST_FILE,
                panel.RETRY_PENDING_FILE,
                panel.DELIVERY_DEDUP_DB_FILE,
                panel.RESTORE_BACKUP_DIR,
            ) = old


if __name__ == "__main__":
    asyncio.run(main())
