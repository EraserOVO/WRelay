"""Verify Discord scan journal backup, validation and legacy restore."""
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from panel.backup_restore import BackupRestoreService, BACKUP_MAX_BYTES
from panel.maintenance import MaintenanceService
from plugins import discord_gaps
from plugins.json_io import json_file_lock, _atomic_write_json_unlocked


class DiscordGapBackupTest(unittest.TestCase):
    def test_large_journal_backup_and_compact_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.json").write_text('{"good": true}', encoding="utf-8")
            journal = root / "discord_gaps.sqlite3"
            db = discord_gaps._connect(journal)
            with db:
                db.executemany("INSERT INTO discord_gaps (owner,channel,message_id,target,message) VALUES (?, ?, ?, ?, ?)",
                    (("a", "123", str(i), "g", json.dumps({"message_id": str(i), "content": "x" * 1100}))
                     for i in range(1, 6001)))
            db.close()
            service = BackupRestoreService(str(root / "settings.json"), str(root / "discord_last.json"),
                str(root / "retry_pending.json"), str(root / "delivery_dedup.sqlite3"), str(root / "restore_backups"),
                json_file_lock, _atomic_write_json_unlocked, lambda: {"good": True},
                lambda data: None if data.get("good") else "invalid", lambda text: None)
            archive = service.create_backup_archive(True)
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                self.assertGreater(z.getinfo("discord_gaps.sqlite3").file_size, 5 * 1024 * 1024)
            self.assertIn("discord_gaps.sqlite3", service.read_restore_archive(archive))
            db = sqlite3.connect(journal)
            db.execute("DELETE FROM discord_gaps")
            db.commit()
            db.close()
            original_size = journal.stat().st_size
            snapshot = service._sqlite_snapshot_bytes(journal)
            self.assertLess(len(snapshot), original_size // 2)
            self.assertEqual(journal.stat().st_size, original_size)

    def test_uncompressed_archive_limit_still_applies(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("settings.json", b" " * (BACKUP_MAX_BYTES + 1))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = BackupRestoreService(str(root / "settings.json"), str(root / "discord_last.json"),
                str(root / "retry_pending.json"), str(root / "delivery_dedup.sqlite3"), str(root / "restore_backups"),
                json_file_lock, _atomic_write_json_unlocked, lambda: {}, lambda data: None, lambda text: None)
            with self.assertRaisesRegex(ValueError, "32 MB"):
                service.read_restore_archive(payload.getvalue())

    def test_backup_restore_and_legacy_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.json").write_text('{"good": true}', encoding="utf-8")
            (root / "discord_last.json").write_text('{"123": {"g": "100"}}', encoding="utf-8")
            journal = root / "discord_gaps.sqlite3"
            discord_gaps.initialize_cursor("a", "123", "g", "100", journal)
            discord_gaps.register("a", "123", "101", ["g"], {"message_id": "101"}, journal)
            service = BackupRestoreService(str(root / "settings.json"), str(root / "discord_last.json"),
                str(root / "retry_pending.json"), str(root / "delivery_dedup.sqlite3"), str(root / "restore_backups"),
                json_file_lock, _atomic_write_json_unlocked, lambda: {"good": True},
                lambda data: None if data.get("good") else "invalid", lambda text: None)
            archive = service.create_backup_archive(True)
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                self.assertIn("discord_gaps.sqlite3", z.namelist())
            discord_gaps.resolve("a", "123", "101", ["g"], journal)
            service.restore_backup_archive(archive, True, True)
            self.assertEqual(len(discord_gaps.pending("a", path=journal)), 1)
            old_archive = io.BytesIO()
            with zipfile.ZipFile(old_archive, "w") as z:
                z.writestr("manifest.json", json.dumps({"format": "wrelay-backup", "version": 1,
                    "include_runtime_state": True, "files": ["settings.json", "discord_last.json"]}))
                z.writestr("settings.json", '{"good": true}')
                z.writestr("discord_last.json", '{"123": {"g": "90"}}')
            result = service.restore_backup_archive(old_archive.getvalue(), True, True)
            self.assertEqual(discord_gaps.pending("a", path=journal), [])
            with zipfile.ZipFile(result["rollback"]) as rollback:
                self.assertIn("discord_gaps.sqlite3", rollback.namelist())

    def test_rejects_corrupt_and_incomplete_journal(self):
        with self.assertRaises(ValueError):
            BackupRestoreService._validate_sqlite_payload(b"broken", "discord_gaps.sqlite3")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.sqlite3"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE discord_gaps (owner TEXT)")
            db.close()
            with self.assertRaises(ValueError):
                BackupRestoreService._validate_sqlite_payload(path.read_bytes(), "discord_gaps.sqlite3")

    def test_maintenance_detects_corrupt_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.json").write_text('{"good": true}', encoding="utf-8")
            (root / "discord_gaps.sqlite3").write_bytes(b"broken")
            service = BackupRestoreService(str(root / "settings.json"), str(root / "discord_last.json"),
                str(root / "retry_pending.json"), str(root / "delivery_dedup.sqlite3"), str(root / "restore_backups"),
                json_file_lock, _atomic_write_json_unlocked, lambda: {"good": True},
                lambda data: None if data.get("good") else "invalid", lambda text: None)
            maintenance = MaintenanceService(lambda: service, root, lambda *args: {}, lambda *args: {}, lambda text: None)
            maintenance._last_database_check["discord_gaps.sqlite3"] = -1000
            alerts = {}
            maintenance._check_files(alerts)
            self.assertIn("file:discord_gaps.sqlite3", alerts)


if __name__ == "__main__":
    unittest.main()
