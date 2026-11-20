"""Verify live YouTube poll progress and account isolation with controlled sources."""

import asyncio
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import nonebot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="wrelay-youtube-env-") as empty_dir:
    original_dir = os.getcwd()
    try:
        os.chdir(empty_dir)
        nonebot.init(driver="~fastapi+~httpx", _env_file=None)
    finally:
        os.chdir(original_dir)

from plugins import config, youtube
from plugins.profile_runtime import RSSProfiles


SLOW = {"id": "channel:@slow", "name": "慢频道", "enabled": True}
FAST = {"id": "channel:@fast", "name": "快频道", "enabled": True}
SOURCES = [SLOW, FAST]
ROUTES = {source["id"]: ["group"] for source in SOURCES}


class YoutubePollProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_source_is_visible_through_status_before_slow_source(self):
        resume_slow = asyncio.Event()
        finished_fast = asyncio.Event()
        old_health = [{"id": SLOW["id"], "name": SLOW["name"], "consecutive_failures": 2}]
        initial = {
            "poll_progress": None,
            "source_health": old_health,
            "last_poll_ts": 100,
            "last_result": "fetch_failed",
            "last_error": "上轮错误",
            "last_new_count": 4,
        }

        async def poll(source):
            if source is SLOW:
                await resume_slow.wait()
                return {"result": "fetch_failed", "error": "HTTP 429", "provider": "data_api"}
            finished_fast.set()
            return {"result": "ok", "provider": "rss_fallback", "new": 2, "sent": 1}

        with (
            mock.patch.multiple(
                youtube,
                _runtime_status=deepcopy(initial),
                _posts_cache={},
                _loop_started=True,
                _load_polling_snapshot=mock.AsyncMock(return_value=(SOURCES, ROUTES)),
                _poll_source=poll,
                _youtube_data_api_status=lambda: {},
            ),
            mock.patch.object(config, "_load_settings", side_effect=lambda: {}),
            mock.patch("plugins.sender.get_qq_bots", return_value={"111": object(), "222": object()}),
            mock.patch("plugins.personal_qq.connected_bots", return_value={}),
        ):
            runtime = RSSProfiles(vars(youtube), "youtube")
            with (
                mock.patch.object(runtime, "owners", return_value=["111", "222"]),
                mock.patch.object(youtube, "_profile_runtime", runtime),
            ):
                task = asyncio.create_task(runtime.call("111", youtube._poll_all_profile))
                try:
                    await asyncio.wait_for(finished_fast.wait(), timeout=2)
                    partial = await youtube.api_youtube_status("111")
                    self.assertTrue(partial["ok"])
                    self.assertEqual(partial["poll_progress"]["total"], 2)
                    self.assertEqual([(item["id"], item["result"]) for item in
                                      partial["poll_progress"]["completed"]], [(FAST["id"], "ok")])
                    self.assertEqual(partial["source_health"], old_health)
                    self.assertEqual(partial["last_poll_ts"], 100)
                    self.assertEqual(partial["last_error"], "上轮错误")
                    self.assertFalse(task.done())

                    self.assertIsNone((await youtube.api_youtube_status("222"))["poll_progress"])
                    combined = (await youtube.api_youtube_status())["poll_progress"]
                    self.assertEqual(combined["total"], 2)
                    self.assertEqual(combined["completed"][0]["id"], "111:" + FAST["id"])
                finally:
                    resume_slow.set()
                    await task

                final = await youtube.api_youtube_status("111")
                self.assertIsNone(final["poll_progress"])
                self.assertEqual(len(partial["poll_progress"]["completed"]), 1)
                self.assertEqual([item["id"] for item in final["source_health"]], [SLOW["id"], FAST["id"]])
                self.assertEqual(final["source_health"][0]["consecutive_failures"], 3)
                self.assertEqual(final["last_error"], "HTTP 429")
                self.assertEqual(final["last_new_count"], 2)
                self.assertEqual(final["last_sent_count"], 1)


if __name__ == "__main__":
    unittest.main()
