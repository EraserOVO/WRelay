"""Focused checks for durable Discord per-target obligations."""
import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

from plugins import discord_gaps


class DiscordGapsTest(unittest.TestCase):
    def test_deferred_failure_does_not_block_newer_or_other_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            discord_gaps.register("a", "c", "10", ["g", "h"], {"message_id": "10"}, path)
            discord_gaps.register("a", "c", "11", ["g"], {"message_id": "11"}, path)
            discord_gaps.defer("a", "c", "10", ["g"], path, now=1000)
            rows = discord_gaps.pending_channel("a", "c", ["g", "h"], 10, path, now=1001)
            self.assertEqual([(r[0], r[1]) for r in rows], [("10", "h"), ("11", "g")])
            rows = discord_gaps.pending_channel("a", "c", ["g", "h"], 10, path, now=1060)
            self.assertEqual([(r[0], r[1]) for r in rows], [("10", "g"), ("10", "h"), ("11", "g")])
            discord_gaps.defer("a", "c", "10", ["g"], path, now=1060)
            self.assertEqual(discord_gaps.pending_channel("a", "c", ["g"], 10, path, now=1179)[0][0], "11")
            self.assertEqual(discord_gaps.pending_channel("a", "c", ["g"], 10, path, now=1180)[0][0], "10")

    def test_existing_journal_is_migrated_without_losing_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE discord_gaps (owner TEXT, channel TEXT, message_id TEXT, target TEXT, message TEXT, PRIMARY KEY(owner, channel, message_id, target)) WITHOUT ROWID")
                db.execute("INSERT INTO discord_gaps VALUES ('a','c','10','g','{}')")
                db.commit()
            self.assertEqual(len(discord_gaps.pending_channel("a", "c", ["g"], 10, path, now=1)), 1)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT attempts, next_attempt FROM discord_gaps").fetchone(), (0, 0))

    def test_older_gap_survives_newer_success_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            discord_gaps.register("bot-a", "channel", "9", ["one", "two"], {"text": "old"}, path)
            discord_gaps.register("bot-a", "channel", "10", ["one"], {"text": "new"}, path)
            discord_gaps.resolve("bot-a", "channel", "10", ["one"], path)
            discord_gaps.resolve("bot-a", "channel", "9", ["two"], path)
            self.assertEqual(discord_gaps.pending("bot-a", path=path), [
                ("channel", "9", "one", {"text": "old"}),
            ])

    def test_account_isolation_duplicate_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            discord_gaps.register("a", "c", "10", ["g", "g"], {"v": 10}, path)
            discord_gaps.register("a", "c", "2", ["g"], {"v": 2}, path)
            discord_gaps.register("a", "c", "2", ["g"], {"v": 999}, path)
            discord_gaps.register("b", "c", "2", ["g"], {"v": 3}, path)
            self.assertEqual([x[1] for x in discord_gaps.pending("a", path=path)], ["2", "10"])
            self.assertEqual(discord_gaps.pending("a", path=path)[0][3], {"v": 2})
            discord_gaps.resolve("a", "c", "2", ["g"], path)
            self.assertEqual(len(discord_gaps.pending("b", path=path)), 1)

    def test_bad_database_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            path.write_bytes(b"broken database")
            with self.assertRaises(Exception):
                discord_gaps.pending("a", path=path)
            self.assertEqual(path.read_bytes(), b"broken database")

    def test_scan_cursor_and_gaps_advance_together(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            discord_gaps.initialize_cursor("a", "c", "g", "10", path)
            discord_gaps.initialize_cursor("a", "c", "g", "20", path)
            self.assertEqual(discord_gaps.get_scan_cursor("a", "c", "g", path), "10")
            batch = [{"message_id": "11"}, {"message_id": "12"}]
            discord_gaps.stage_scan("a", "c", "g", "10", batch, path)
            self.assertEqual(discord_gaps.get_scan_cursor("a", "c", "g", path), "12")
            self.assertEqual([row[1] for row in discord_gaps.pending("a", path=path)], ["11", "12"])
            with self.assertRaises(discord_gaps.CursorChanged):
                discord_gaps.stage_scan("a", "c", "g", "10", [{"message_id": "13"}], path)
            with self.assertRaises(ValueError):
                discord_gaps.stage_scan("a", "c", "g", "12", [{"message_id": "14"}, {"message_id": "13"}], path)
            self.assertEqual(discord_gaps.get_scan_cursor("a", "c", "g", path), "12")
            self.assertEqual([row[1] for row in discord_gaps.pending("a", path=path)], ["11", "12"])

    def test_scan_does_not_requeue_a_realtime_success_or_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            discord_gaps.initialize_cursors("a", "c", {"g": "10", "h": "10"}, path)
            discord_gaps.resolve("a", "c", "12", ["g"], path)
            discord_gaps.stage_scan("a", "c", "g", "10", [
                {"message_id": "11"}, {"message_id": "12"},
            ], path)
            discord_gaps.stage_scan("a", "c", "h", "10", [
                {"message_id": "11"}, {"message_id": "12"},
            ], path)
            self.assertEqual([(row[1], row[2]) for row in discord_gaps.pending("a", path=path)],
                             [("11", "g"), ("11", "h"), ("12", "h")])

    def test_scan_rolls_back_if_journal_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            discord_gaps.initialize_cursor("a", "c", "g", "10", path)
            with closing(sqlite3.connect(str(path))) as db:
                db.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON discord_gaps
                    WHEN NEW.message_id = '12' BEGIN SELECT RAISE(ABORT, 'write failed'); END""")
                db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                discord_gaps.stage_scan("a", "c", "g", "10", [
                    {"message_id": "11"}, {"message_id": "12"},
                ], path)
            self.assertEqual(discord_gaps.get_scan_cursor("a", "c", "g", path), "10")
            self.assertEqual(discord_gaps.pending("a", path=path), [])

    def test_clear_through_preserves_newer_and_paused_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            discord_gaps.initialize_cursor("a", "c", "g", "30", path)
            discord_gaps.register("a", "c", "10", ["g", "paused"], {}, path)
            discord_gaps.register("a", "c", "21", ["g"], {}, path)
            discord_gaps.register("b", "c", "10", ["g"], {}, path)
            discord_gaps.clear_through("a", [("c", ["g"], "20")], path)
            self.assertEqual([(row[1], row[2]) for row in discord_gaps.pending("a", path=path)],
                             [("10", "paused"), ("21", "g")])
            self.assertEqual(len(discord_gaps.pending("b", path=path)), 1)
            self.assertEqual(discord_gaps.get_scan_cursor("a", "c", "g", path), "30")

    def test_descending_scan_resumes_and_commits_only_after_reaching_base(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            discord_gaps.initialize_cursor("a", "c", "g", "10", path)
            base, before = discord_gaps.start_scan("a", "c", "g", path)
            self.assertEqual((base, before), ("10", None))
            self.assertFalse(discord_gaps.append_scan_page("a", "c", "g", base, before,
                [{"message_id": "15"}, {"message_id": "14"}], 2, path))
            self.assertEqual(discord_gaps.get_scan_cursor("a", "c", "g", path), "10")
            self.assertEqual(discord_gaps.pending("a", path=path), [])
            discord_gaps.resolve("a", "c", "15", ["g"], path)
            base, before = discord_gaps.start_scan("a", "c", "g", path)
            self.assertEqual(before, "14")
            with self.assertRaises(ValueError):
                discord_gaps.append_scan_page("a", "c", "g", base, before,
                    [{"message_id": "14"}], 2, path)
            self.assertFalse(discord_gaps.append_scan_page("a", "c", "g", base, before,
                [{"message_id": "13"}, {"message_id": "12"}], 2, path))
            self.assertTrue(discord_gaps.append_scan_page("a", "c", "g", base, "12",
                [{"message_id": "11"}, {"message_id": "10"}], 2, path))
            self.assertEqual(discord_gaps.get_scan_cursor("a", "c", "g", path), "15")
            self.assertEqual([row[1] for row in discord_gaps.pending("a", path=path)], ["11", "12", "13", "14"])

    def test_clear_invalidates_in_progress_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gaps.sqlite3"
            discord_gaps.initialize_cursor("a", "c", "g", "10", path)
            base, before = discord_gaps.start_scan("a", "c", "g", path)
            discord_gaps.clear_through("a", [("c", ["g"], "20")], path)
            with self.assertRaises(discord_gaps.CursorChanged):
                discord_gaps.append_scan_page("a", "c", "g", base, before, [{"message_id": "11"}], 2, path)
            self.assertEqual(discord_gaps.pending("a", path=path), [])


if __name__ == "__main__":
    unittest.main()
