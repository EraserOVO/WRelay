"""Check that Discord delivery outcomes resolve only the confirmed targets."""
import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import nonebot

nonebot.init(_env_file=("__wrelay_test_missing_env__",))

from plugins import backfill, backfill_api, bot_profiles, discord_gaps, manage, retry


class DeliveryJournalTest(unittest.IsolatedAsyncioTestCase):
    async def test_registration_skips_durable_successes_after_cursor_crash(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")):
            with bot_profiles.scope("account"):
                self.assertEqual(
                    await discord_gaps.register_async("123", "101", ["done", "pending"], {"content": "old"}),
                    {"done", "pending"},
                )
                # Simulate the durable delivery receipt being written just before
                # the legacy last-message cursor update was interrupted.
                await discord_gaps.resolve_async("123", "101", ["done"])
                self.assertEqual(
                    await discord_gaps.register_async("123", "101", ["done", "pending"], {"content": "old"}),
                    {"pending"},
                )
                self.assertEqual([(row[1], row[2]) for row in discord_gaps.pending("account")],
                                 [("101", "pending")])

    async def test_confirmed_receipt_wins_retry_race_without_resending(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")), \
                mock.patch.object(retry, "update_last_messages", new_callable=mock.AsyncMock) as update, \
                mock.patch.object(retry, "_purge_delivered_async", new_callable=mock.AsyncMock), \
                mock.patch.object(retry, "send_relay_message", new_callable=mock.AsyncMock) as send:
            with bot_profiles.scope("account"):
                await discord_gaps.register_async("123", "101", ["g"], {"content": "old"})
                await discord_gaps.resolve_async("123", "101", ["g"])
                result = await retry.send_and_record("123", "101", [], [], ["g"])
                self.assertTrue(result["g"])
                send.assert_not_awaited()
                update.assert_awaited_once()

    async def test_scan_failure_does_not_block_saved_gap_delivery(self):
        snapshot = {"enabled": True, "limit": 10, "channels": {"123": "channel"},
                    "groups_by_channel": {"123": ["g"]}}
        with mock.patch.object(backfill, "_backfill_running", False), \
                mock.patch.object(backfill, "_backfill_rerun_requested", False), \
                mock.patch.object(backfill, "get_backfill_runtime_snapshot", return_value=snapshot), \
                mock.patch.object(backfill, "_scan_gaps_channel", new_callable=mock.AsyncMock,
                                  side_effect=RuntimeError("offline")), \
                mock.patch.object(backfill, "_replay_gaps_channel", new_callable=mock.AsyncMock) as replay, \
                mock.patch.object(backfill.logger, "exception"):
            with bot_profiles.scope("account"):
                await backfill._backfill_missed_profile()
                replay.assert_awaited_once_with("123", "channel", ["g"], 10)
                self.assertTrue(backfill.get_backfill_status().get("error"))

    async def test_scanning_delivery_failure_and_recovery_together(self):
        snapshot = {"enabled": True, "limit": 10, "channels": {"123": "channel"},
                    "groups_by_channel": {"123": ["g1", "g2"]}}
        sent = []
        failed_once = set()

        async def fake_send(text_parts, media_items, groups, channel_id, message_id):
            result = {}
            for group in groups:
                sent.append((message_id, group))
                key = (message_id, group)
                if key == ("101", "g1") and key not in failed_once:
                    failed_once.add(key)
                    result[group] = False
                else:
                    result[group] = True
            return result

        async def page(channel, before):
            return [{"message_id": str(i), "content": "body", "author_username": "u"}
                    for i in (103, 102, 101, 100)] if before is None else []

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")), \
                mock.patch.object(backfill, "BACKFILL_SEND_INTERVAL", 0), \
                mock.patch.object(backfill, "_backfill_running", False), \
                mock.patch.object(backfill, "_backfill_rerun_requested", False), \
                mock.patch.object(backfill, "get_backfill_runtime_snapshot", return_value=snapshot), \
                mock.patch.object(backfill, "load_last_messages_async", new_callable=mock.AsyncMock,
                                  return_value={"123": {"g1": "100", "g2": "100"}}), \
                mock.patch.object(backfill, "fetch_channel_scan_page", side_effect=page), \
                mock.patch.object(backfill, "load_channel_filter", new_callable=mock.AsyncMock, return_value={}), \
                mock.patch.object(backfill, "check_message_filter", return_value=True), \
                mock.patch.object(backfill, "prepare_discord_message", new_callable=mock.AsyncMock, return_value=(["text"], [])), \
                mock.patch.object(backfill, "record", new_callable=mock.AsyncMock), \
                mock.patch.object(backfill, "schedule_retry_async", new_callable=mock.AsyncMock), \
                mock.patch.object(retry, "update_last_messages", new_callable=mock.AsyncMock), \
                mock.patch.object(retry, "_purge_delivered_async", new_callable=mock.AsyncMock), \
                mock.patch.object(retry, "send_relay_message", side_effect=fake_send):
            with bot_profiles.scope("account"):
                await discord_gaps.resolve_async("123", "103", ["g1"])
                await backfill._backfill_missed_profile()
                self.assertNotIn(("103", "g1"), sent)
                self.assertIn(("102", "g1"), sent)
                self.assertIn(("101", "g2"), sent)
                self.assertEqual([(row[1], row[2]) for row in discord_gaps.pending("account")], [("101", "g1")])
                db = sqlite3.connect(discord_gaps.GAP_FILE)
                db.execute("UPDATE discord_gaps SET next_attempt=0")
                db.commit()
                db.close()
                await backfill._backfill_missed_profile()
                self.assertEqual(discord_gaps.pending("account"), [])
                self.assertEqual(sent.count(("101", "g1")), 2)
                self.assertEqual(sent.count(("101", "g2")), 1)

    async def test_failed_target_is_deferred_but_busy_target_is_not(self):
        snapshot = {"enabled": True, "groups_by_channel": {"123": ["failed", "busy"]}}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")), \
                mock.patch.object(backfill, "BACKFILL_SEND_INTERVAL", 0), \
                mock.patch.object(backfill, "get_backfill_runtime_snapshot", return_value=snapshot), \
                mock.patch.object(backfill, "send_and_record", new_callable=mock.AsyncMock,
                                  return_value=retry.SendResult({"failed": False, "busy": False}, busy_groups={"busy"})), \
                mock.patch.object(backfill, "record", new_callable=mock.AsyncMock), \
                mock.patch.object(backfill, "schedule_retry_async", new_callable=mock.AsyncMock):
            with bot_profiles.scope("account"):
                await discord_gaps.register_async("123", "101", ["failed", "busy"], {"content": "old"})
                await backfill._deliver_backfill_message("123", "101", {}, ["failed", "busy"], (["text"], []), only_pending=True)
                rows = discord_gaps.pending_channel("account", "123", ["failed", "busy"], 10)
                self.assertEqual([row[1] for row in rows], ["busy"])

    async def test_manual_run_is_not_suppressed_while_scanning_or_unknown(self):
        for pending in ({"ok": True, "total": 0, "scanning": True},
                        {"ok": True, "total": 0, "unknown": True}):
            with mock.patch.object(manage, "_panel_get", new_callable=mock.AsyncMock, return_value=pending), \
                    mock.patch.object(manage, "_panel_post", new_callable=mock.AsyncMock, return_value={"ok": True}) as post:
                self.assertEqual(await manage._cmd_backfill(["执行"]), "补发已触发")
                post.assert_awaited_once()
        self.assertEqual(manage._backfill_pending_text({"ok": True, "total": 0, "unknown": True}), "未知")

    async def test_status_counts_recorded_messages_and_reports_unknown(self):
        snapshot = {"channels": {"123": "channel"}, "groups_by_channel": {"123": ["a", "b"]}}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")), \
                mock.patch.object(backfill_api, "get_backfill_routing_snapshot", return_value=snapshot), \
                mock.patch.object(backfill_api, "load_last_messages_async", new_callable=mock.AsyncMock,
                                  return_value={"123": {"a": "100", "b": "100"}}), \
                mock.patch.object(backfill_api, "fetch_channel_latest", new_callable=mock.AsyncMock, return_value="105") as latest:
            with bot_profiles.scope("account"):
                await discord_gaps.register_async("123", "101", ["a", "b"], {"content": "old"})
                discord_gaps.initialize_cursors("account", "123", {"a": "100", "b": "100"})
                status = await backfill_api.api_backfill_pending()
                self.assertEqual(status["total"], 1)
                self.assertTrue(status["scanning"])
                self.assertFalse(status["unknown"])
                latest.return_value = None
                status = await backfill_api.api_backfill_pending()
                self.assertTrue(status["unknown"])
                self.assertEqual(status["total"], 1)

    async def test_large_history_scans_across_rounds_without_requeueing_success(self):
        pages = []

        async def fetch_page(channel, before):
            pages.append(before)
            top = int(before) - 1 if before else 1150
            return [{"message_id": str(value), "content": "body"}
                    for value in range(top, max(99, top - 100), -1)]

        snapshot = {"enabled": True, "groups_by_channel": {"123": ["g"]}}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")), \
                mock.patch.object(backfill, "load_last_messages_async", new_callable=mock.AsyncMock, return_value={"123": {"g": "100"}}), \
                mock.patch.object(backfill, "get_backfill_runtime_snapshot", return_value=snapshot), \
                mock.patch.object(backfill, "fetch_channel_scan_page", side_effect=fetch_page), \
                mock.patch.object(backfill.asyncio, "sleep", new_callable=mock.AsyncMock):
            with bot_profiles.scope("account"):
                await discord_gaps.resolve_async("123", "1149", ["g"])
                await backfill._scan_gaps_channel("123", ["g"])
                self.assertEqual(len(pages), 10)
                self.assertEqual(discord_gaps.get_scan_cursor("account", "123", "g"), "100")
                self.assertEqual(discord_gaps.pending("account"), [])
                await backfill._scan_gaps_channel("123", ["g"])
                self.assertEqual(discord_gaps.get_scan_cursor("account", "123", "g"), "1150")
                rows = discord_gaps.pending("account", limit=2000)
                self.assertEqual(len(rows), 1049)
                self.assertNotIn("1149", {row[1] for row in rows})

    async def test_clear_removes_only_selected_retry_targets_and_blocks_stale_send(self):
        older = retry._RetryEntry({"content": "old"}, "test", {"g", "paused"}, 0)
        newer = retry._RetryEntry({"content": "new"}, "test", {"g"}, 0)
        with mock.patch.object(retry, "_pending", {"123": {"101": older, "201": newer}}), \
                mock.patch.object(retry, "_save_pending_async", new_callable=mock.AsyncMock, return_value=True), \
                mock.patch.object(retry, "send_relay_message", new_callable=mock.AsyncMock) as send:
            await retry.clear_retry_through([("123", ["g"], "150")])
            self.assertEqual(older.groups, {"paused"})
            self.assertEqual(newer.groups, {"g"})
            result = await retry.send_and_record("123", "101", [], [], ["g"], retry_entry=older)
            self.assertTrue(result["g"])
            send.assert_not_awaited()

    async def test_scheduler_survives_failed_round(self):
        reached = asyncio.Event()
        calls = 0

        async def run_round():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient")
            reached.set()

        with mock.patch.object(backfill, "BACKFILL_CHECK_INTERVAL", 0.01), \
                mock.patch.object(backfill, "_backfill_stopping", False), \
                mock.patch.object(backfill, "_backfill_missed", side_effect=run_round), \
                mock.patch.object(backfill.logger, "exception"):
            task = asyncio.create_task(backfill._backfill_scheduler())
            try:
                await asyncio.wait_for(reached.wait(), 2)
                self.assertGreaterEqual(calls, 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_backfill_sends_are_spaced_and_recheck_route(self):
        clock = [0.0]
        started = []
        requested_delays = []

        async def fake_sleep(delay):
            requested_delays.append(delay)
            clock[0] += delay

        async def fake_send(*args, **kwargs):
            started.append(clock[0])
            return {"g": True}

        snapshot = {"enabled": True, "groups_by_channel": {"123": ["g"]}}
        with mock.patch.object(backfill, "BACKFILL_SEND_INTERVAL", 0.03), \
                mock.patch.object(backfill, "_backfill_last_send", None), \
                mock.patch.object(backfill, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                mock.patch.object(backfill.asyncio, "sleep", new=fake_sleep), \
                mock.patch.object(backfill, "get_backfill_runtime_snapshot", return_value=snapshot), \
                mock.patch.object(backfill, "send_and_record", side_effect=fake_send), \
                mock.patch.object(backfill, "record", new_callable=mock.AsyncMock):
            await backfill._deliver_backfill_message("123", "101", {}, ["g"], (["text"], []))
            await backfill._deliver_backfill_message("123", "102", {}, ["g"], (["text"], []))
            self.assertEqual(requested_delays, [0.03])
            self.assertAlmostEqual(started[1] - started[0], 0.03)

    async def test_recovery_ignores_high_cursor_and_preserves_paused_group(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")), \
                mock.patch.object(backfill, "get_backfill_runtime_snapshot", return_value={
                    "enabled": True, "groups_by_channel": {"123": ["active"]},
                }), \
                mock.patch.object(backfill, "load_channel_filter", new_callable=mock.AsyncMock, return_value={}), \
                mock.patch.object(backfill, "check_message_filter", return_value=True), \
                mock.patch.object(backfill, "prepare_discord_message", new_callable=mock.AsyncMock, return_value=(["text"], [])), \
                mock.patch.object(backfill, "record", new_callable=mock.AsyncMock), \
                mock.patch.object(retry, "update_last_messages", new_callable=mock.AsyncMock), \
                mock.patch.object(retry, "_purge_delivered_async", new_callable=mock.AsyncMock), \
                mock.patch.object(retry, "send_relay_message", new_callable=mock.AsyncMock, return_value={"active": True}) as send:
            with bot_profiles.scope("account"):
                await discord_gaps.register_async("123", "101", ["active", "paused"], {"content": "old"})
                count = await backfill._replay_gaps_channel("123", "channel", ["active"], 5)
                self.assertEqual(count, 1)
                self.assertEqual(send.await_count, 1)
                self.assertEqual([(row[1], row[2]) for row in discord_gaps.pending("account")], [("101", "paused")])

    async def test_stale_recovery_snapshot_does_not_resend(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")), \
                mock.patch.object(retry, "send_relay_message", new_callable=mock.AsyncMock) as send:
            with bot_profiles.scope("account"):
                result = await retry.send_and_record("123", "101", [], [], ["g"], only_pending=True)
                self.assertTrue(result["g"])
                send.assert_not_awaited()

    async def test_disabling_group_during_render_preserves_obligation(self):
        enabled = {"enabled": True, "groups_by_channel": {"123": ["g"]}}
        disabled = {"enabled": True, "groups_by_channel": {"123": []}}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")), \
                mock.patch.object(backfill, "get_backfill_runtime_snapshot", side_effect=[enabled, disabled]), \
                mock.patch.object(backfill, "load_channel_filter", new_callable=mock.AsyncMock, return_value={}), \
                mock.patch.object(backfill, "check_message_filter", return_value=True), \
                mock.patch.object(backfill, "prepare_discord_message", new_callable=mock.AsyncMock, return_value=(["text"], [])), \
                mock.patch.object(backfill, "send_and_record", new_callable=mock.AsyncMock) as send:
            with bot_profiles.scope("account"):
                await discord_gaps.register_async("123", "101", ["g"], {"content": "old"})
                await backfill._replay_gaps_channel("123", "channel", ["g"], 5)
                send.assert_not_awaited()
                self.assertEqual(len(discord_gaps.pending("account")), 1)

    async def test_event_timestamp_is_serializable(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")):
            await discord_gaps.register_async("123", "101", ["g"], {
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            })
            message = discord_gaps.pending(discord_gaps.LEGACY_OWNER)[0][3]
            self.assertEqual(message["message_id"], "101")
            self.assertEqual(message["created_at"], "2026-01-01T00:00:00+00:00")

    async def test_partial_failure_survives_newer_success_and_retry(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(discord_gaps, "GAP_FILE", str(Path(directory) / "gaps.sqlite3")), \
                mock.patch.object(retry, "update_last_messages", new_callable=mock.AsyncMock), \
                mock.patch.object(retry, "_purge_delivered_async", new_callable=mock.AsyncMock), \
                mock.patch.object(retry, "send_relay_message", new_callable=mock.AsyncMock) as send:
            with bot_profiles.scope("account"):
                await discord_gaps.register_async("123", "101", ["g1", "g2"], {"content": "old"})
                send.return_value = {"g1": True, "g2": False}
                await retry.send_and_record("123", "101", [], [], ["g1", "g2"])
                await discord_gaps.register_async("123", "102", ["g2"], {"content": "new"})
                send.return_value = {"g2": True}
                await retry.send_and_record("123", "102", [], [], ["g2"])
                self.assertEqual([(row[1], row[2]) for row in discord_gaps.pending("account")], [("101", "g2")])
                await retry.send_and_record("123", "101", [], [], ["g2"])
                self.assertEqual(discord_gaps.pending("account"), [])


if __name__ == "__main__":
    unittest.main()
