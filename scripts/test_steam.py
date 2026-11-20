"""Targeted tests for Steam source parsing, changes and durable delivery."""
import asyncio
import copy
import os
import json
import importlib.util
from io import BytesIO
from importlib.machinery import SourceFileLoader
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nonebot
_cwd = os.getcwd()
with tempfile.TemporaryDirectory(prefix="wrelay-steam-env-") as _empty_env_dir:
    try:
        os.chdir(_empty_env_dir)
        nonebot.init(driver="~fastapi+~httpx", _env_file=None)
    finally:
        os.chdir(_cwd)

import httpx
from plugins import bot_profiles, config, delivery_dedup, history, media, message_card, paths, personal_qq, retry, sender, steam, steam_data, steam_state
from panel.profile_api import portable, prepare
from panel.backup_restore import BackupRestoreService
from plugins.json_io import json_file_lock, _atomic_write_json_unlocked
from plugins.settings_model import SettingsModel, validate_settings
from plugins.state_validation import validate_runtime


GAME = {"id": "s1", "kind": "game", "value": "620", "name": "Portal 2", "enabled": True,
        "events": ["announcements", "news", "discount"], "news_feeds": []}
CREATOR = {"id": "s2", "kind": "creator", "value": "https://store.steampowered.com/developer/Valve",
           "name": "Valve", "enabled": True, "events": ["discount", "new_listing", "early_access", "release", "full_release"], "news_feeds": []}


def game(appid="620", price=1000, percent=0, phase="upcoming"):
    return {"appid": appid, "name": "Example", "type": "game", "free": False,
            "price": {"initial": 1000, "final": price, "discount_percent": percent, "currency": "CNY"},
            "release": {"coming_soon": phase == "upcoming"},
            "genres": [{"id": "70"}] if phase == "early" else []}


class SteamStateTests(unittest.TestCase):
    def test_queue_format_preserves_article_text_and_removes_only_legacy_headers(self):
        self.assertEqual(
            steam._format_queue_message({"event": "news", "text": "Steam notice"}),
            "【Steam · 新闻】\nSteam notice",
        )
        self.assertEqual(
            steam._format_queue_message({
                "event": "news",
                "text": "Steam 游戏新闻\n游戏标题\nhttps://example.test/news",
            }),
            "【Steam · 新闻】\n游戏标题\nhttps://example.test/news",
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_panel_draft_and_save(self):
        script = Path(__file__).with_name("test_steam_ui.js")
        subprocess.run([shutil.which("node"), str(script)], check=True, timeout=30,
                       capture_output=True, text=True, encoding="utf-8")

    def test_source_parsing_rejects_arbitrary_hosts_and_html(self):
        self.assertEqual(steam_data.app_id("https://store.steampowered.com/app/620/Portal_2/"), "620")
        self.assertEqual(steam_data.app_id("https://evil.example/app/620/"), "")
        self.assertEqual(steam_data.creator_url("https://evil.example/developer/Valve"), "")
        with self.assertRaises(ValueError):
            steam_data.parse_announcements(b"<html>Not RSS</html>")
        with self.assertRaises(ValueError):
            steam_data.creator_app_ids({"success": 1, "creator_clan_id": 4}, "4")

    def test_feed_baselines_per_group_and_deduplicates_rss_and_news(self):
        state = steam_state.empty_state()
        first = {"title": "An announcement", "published": 1700000000, "link": "https://example.com/1"}
        next_item = {"title": "New announcement", "published": 1700001000, "link": "https://example.com/2"}
        rss = dict(next_item, published="Tue, 14 Nov 2023 22:30:00 +0000")
        self.assertEqual(steam_state.post_identity("620", rss), steam_state.post_identity("620", next_item))
        sig = steam_state.signature("game", "620")
        steam_state.feed_update(state, GAME, "g1", "announcements", [first], sig, 20)
        steam_state.feed_update(state, GAME, "g1", "announcements", [first, next_item], sig, 20)
        steam_state.feed_update(state, GAME, "g2", "announcements", [first, next_item], sig, 20)
        self.assertEqual([x["group"] for x in state["queue"]], ["g1"])
        steam_state.feed_update(state, GAME, "g1", "announcements", [rss, next_item], sig, 20)
        self.assertEqual(len(state["queue"]), 1)
        validate_runtime("steam.json", state)

    def test_creator_listing_price_and_release_transitions(self):
        state = steam_state.empty_state()
        sig = steam_state.signature(CREATOR["kind"], CREATOR["value"])
        steam_state.catalog_update(state, CREATOR, "g1", ["620"], sig)
        steam_state.game_update(state, CREATOR, "g1", game(), "CN", 20)
        self.assertEqual(state["queue"], [])
        steam_state.catalog_update(state, CREATOR, "g1", ["620", "621"], sig)
        steam_state.game_update(state, CREATOR, "g1", game("621"), "CN", 20)
        steam_state.game_update(state, CREATOR, "g1", game(price=500, percent=50, phase="early"), "CN", 20)
        steam_state.game_update(state, CREATOR, "g1", game(price=500, percent=50, phase="released"), "CN", 20)
        texts = [item["text"] for item in state["queue"]]
        self.assertEqual(len(texts), 4)
        self.assertTrue(any("新游戏" in text for text in texts))
        self.assertTrue(any("折扣" in text for text in texts))
        self.assertTrue(any("抢先体验开售" in text for text in texts))
        self.assertTrue(any("抢先体验转正式版" in text for text in texts))
        validate_runtime("steam.json", state)

    def test_missing_price_and_release_are_not_changes(self):
        state = steam_state.empty_state()
        steam_state.game_update(state, CREATOR, "g1", game(), "CN", 20)
        before = copy.deepcopy(state)
        missing = game(); missing["price"] = None; missing["genres"] = None
        steam_state.game_update(state, CREATOR, "g1", missing, "CN", 20)
        self.assertEqual(before, state)

    def test_full_queue_advances_feed_in_batches_without_losing_items(self):
        state = steam_state.empty_state()
        sig = steam_state.signature("game", "620")
        first = {"title": "before", "published": 1700000000, "link": "https://example.com/0"}
        later = [{"title": str(i), "published": 1700000000 + i, "link": "https://example.com/" + str(i)} for i in range(1, 4)]
        steam_state.feed_update(state, GAME, "g1", "news", [first], sig, 1)
        delivered = []
        for _ in range(3):
            steam_state.feed_update(state, GAME, "g1", "news", [first] + later, sig, 1)
            self.assertEqual(len(state["queue"]), 1)
            delivered.append(state["queue"].pop()["identity"])
        self.assertEqual(len(set(delivered)), 3)
        self.assertEqual(state["routes"]["s1"]["g1"]["news"]["watermark"], 1700000003)

    def test_full_queue_does_not_lose_simultaneous_price_and_release(self):
        state = steam_state.empty_state()
        steam_state.game_update(state, CREATOR, "g1", game(), "CN", 1)
        steam_state.game_update(state, CREATOR, "g1", game(price=500, percent=50, phase="early"), "CN", 1)
        self.assertEqual(state["queue"][0]["event"], "discount")
        state["queue"].clear()
        steam_state.game_update(state, CREATOR, "g1", game(price=500, percent=50, phase="early"), "CN", 1)
        self.assertEqual(state["queue"][0]["event"], "early_access")

    def test_new_creator_game_already_in_early_access_uses_specific_event(self):
        state = steam_state.empty_state()
        sig = steam_state.signature(CREATOR["kind"], CREATOR["value"])
        steam_state.catalog_update(state, CREATOR, "g1", ["620"], sig)
        steam_state.catalog_update(state, CREATOR, "g1", ["620", "621"], sig)
        steam_state.game_update(state, CREATOR, "g1", game("621", phase="early"), "CN", 20)
        self.assertEqual(len(state["queue"]), 1)
        self.assertEqual(state["queue"][0]["event"], "early_access")
        validate_runtime("steam.json", state)

    def test_same_announcement_stays_routable_when_one_feed_is_disabled(self):
        state = steam_state.empty_state()
        steam_state.enqueue(state, "s1", "g1", "article", "text", 5, "announcements", "620")
        steam_state.enqueue(state, "s1", "g1", "article", "text", 5, "news", "620")
        self.assertEqual(len(state["queue"]), 1)
        settings = {"sources": [dict(GAME, events=["news"])], "routes": {"s1": ["g1"]}}
        self.assertTrue(steam._routable(state["queue"][0], settings))
        validate_runtime("steam.json", state)

    def test_dlc_and_bundle_are_optional_and_keep_paused_items(self):
        state = steam_state.empty_state()
        source = dict(GAME, events=["discount"], include_dlc=True, include_bundles=True)
        dlc = dict(game("621"), type="dlc")
        bundle = dict(id="232", name="Bundle", final=1000, discount=0,
                      final_formatted="¥10", initial_formatted="", initial_label="捆绑优惠前")
        steam_state.game_update(state, source, "g1", dlc, "CN", 10)
        steam_state.bundle_update(state, source, "g1", bundle, "CN", 10)
        steam_state.game_update(state, source, "g1", dict(dlc, price=dict(dlc["price"], final=500, discount_percent=50)), "CN", 10)
        steam_state.bundle_update(state, source, "g1", dict(bundle, final=700, discount=30, final_formatted="¥7"), "CN", 10)
        self.assertEqual([item["category"] for item in state["queue"]], ["dlc", "bundle"])
        disabled = dict(source, include_dlc=False, include_bundles=False)
        settings = {"sources": [disabled], "routes": {"s1": ["g1"]}}
        self.assertTrue(all(not steam._routable(item, settings) for item in state["queue"]))
        steam_state.reconcile_routes(state, [disabled], settings["routes"])
        events = state["routes"]["s1"]["g1"]
        self.assertNotIn("bundle_discount", events)
        self.assertEqual(events["discount"]["apps"], {})
        self.assertEqual(len(state["queue"]), 2)
        validate_runtime("steam.json", state)

    def test_bundle_ownership_and_incomplete_store_batch(self):
        item = {"id": 232, "item_type": 2, "success": 1, "visible": True,
                "basic_info": {"publishers": [{"creator_clan_account_id": 4}]},
                "purchase_options": [{"bundleid": 232, "purchase_option_name": "Bundle", "final_price_in_cents": "700",
                    "formatted_final_price": "¥7", "combined_discount_pct": 30}]}
        rows = steam_data.store_items({"response": {"store_items": [item]}}, ["232"], "bundleid")
        self.assertEqual(steam_data.bundle_details(rows["232"], "232", "4", "publishers")["final"], 700)
        self.assertEqual(steam_data.bundle_ids({"id": 570, "visible": True}), [])
        self.assertIsNone(steam_data.bundle_details(rows["232"], "232", "5", "publishers"))
        with self.assertRaises(ValueError):
            steam_data.store_items({"response": {"store_items": []}}, ["232"], "bundleid")
        with self.assertRaises(ValueError):
            steam_data.bundle_details(dict(item, basic_info={}), "232", "4", "publishers")

    def test_invalid_state_and_settings_are_rejected(self):
        state = steam_state.empty_state(); state["queue"] = [{"seq": 1}]
        with self.assertRaises(ValueError): validate_runtime("steam.json", state)
        settings = SettingsModel.default().to_dict()
        settings["steam_sources"] = [GAME]
        self.assertIsNone(validate_settings(settings, validate_forwarding_groups=True))
        settings["steam_region"] = "../"
        self.assertIsNotNone(validate_settings(settings, validate_forwarding_groups=True))
        settings["steam_region"] = "CN"
        settings["steam_sources"] = [dict(GAME, kind=[])]
        self.assertIsNotNone(validate_settings(settings, validate_forwarding_groups=True))

    def test_profile_export_import_and_route_isolation(self):
        with tempfile.TemporaryDirectory(prefix="steam-profiles-") as directory:
            path = str(Path(directory) / "settings.json")
            data = SettingsModel.default().to_dict()
            data["qq_group_openids"] = [dict(openid="G1", name="one", enabled=True, bot_id="111"),
                                        dict(openid="G2", name="two", enabled=True, bot_id="222")]
            data["steam_sources"] = [copy.deepcopy(GAME)]
            data["steam_forwarding_groups"] = [dict(id="sg", name="Steam", enabled=True, sources=["s1"], groups=["G1"])]
            Path(path).write_text(__import__("json").dumps(data), encoding="utf-8")
            bot_profiles.migrate(path, ["111", "222"])
            document = portable(path, "111")
            self.assertEqual(document["config"]["steam_sources"][0]["id"], "s1")
            candidate, _, _, _ = prepare(path, "222", {"document": document, "mode": "replace", "mapping": {"G1": "G2"}})
            self.assertEqual(candidate["steam_forwarding_groups"][0]["groups"], ["G2"])
            with mock.patch.object(config, "SETTINGS_FILE", path), bot_profiles.scope("111"):
                snapshot = config.get_steam_polling_snapshot()
            self.assertEqual(snapshot["routes"], {"s1": ["G1"]})
            with mock.patch.object(config, "SETTINGS_FILE", path), bot_profiles.scope("222"):
                self.assertEqual(config.get_steam_polling_snapshot()["routes"], {"s1": []})
            runtime = Path(bot_profiles.runtime_path(path, "111", "steam.json"))
            runtime.parent.mkdir(parents=True, exist_ok=True)
            original = steam_state.empty_state()
            runtime.write_text(json.dumps(original), encoding="utf-8")
            service = BackupRestoreService(path, str(Path(directory) / "discord_last.json"),
                str(Path(directory) / "retry_pending.json"), str(Path(directory) / "delivery_dedup.sqlite3"),
                str(Path(directory) / "rollbacks"), json_file_lock, _atomic_write_json_unlocked,
                lambda: SettingsModel.default().to_dict(), validate_settings, nonebot.logger)
            archive = service.create_backup_archive(True)
            self.assertIn("bot_profile_runtime/official_111/steam.json", service.read_restore_archive(archive))
            runtime.write_text(json.dumps(steam_state.empty_state()), encoding="utf-8")
            service.restore_backup_archive(archive, True, True)
            self.assertEqual(json.loads(runtime.read_text(encoding="utf-8")), original)


class SteamStartupTests(unittest.IsolatedAsyncioTestCase):
    STORE = "https://store.steampowered.test/apps"
    COMMUNITY = "https://steamcommunity.test/news"
    STEAM_PROXY = "http://steam-proxy.test:8080"
    DISCORD_PROXY = "http://discord-proxy.test:8080"

    async def asyncSetUp(self):
        self.now = 1000.0
        self.steam_proxy = None
        self.discord_proxy = self.DISCORD_PROXY
        self.replies = []
        self.requests = []
        patches = [
            mock.patch.multiple(steam, _client=None, _proxy_client=None, _proxy_address=None,
                                _last_request=0.0, _host_cooldowns={}, _request_lock=asyncio.Lock()),
            mock.patch.object(steam, "get_network_proxy", side_effect=lambda service: self.steam_proxy),
            mock.patch.object(steam, "get_driver", side_effect=lambda: SimpleNamespace(
                config=SimpleNamespace(discord_proxy=self.discord_proxy))),
            mock.patch.object(steam, "asyncio", SimpleNamespace(
                to_thread=asyncio.to_thread, sleep=self.sleep, CancelledError=asyncio.CancelledError)),
            mock.patch.object(steam, "time", SimpleNamespace(monotonic=lambda: self.now, time=lambda: self.now)),
            mock.patch.object(steam.httpx, "AsyncClient", side_effect=self.client),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    async def sleep(self, seconds):
        if seconds > 1:
            self.assertFalse(steam._request_lock.locked(), "retry wait must not block other hosts")
        self.now += seconds

    def client(self, proxy=None, **kwargs):
        async def get(url, params=None):
            self.requests.append((url, proxy, self.now))
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return httpx.Response(reply[0], headers=reply[1], request=httpx.Request("GET", url))

        return SimpleNamespace(get=get, aclose=mock.AsyncMock())

    async def test_explicit_steam_proxy_is_used_on_first_request_and_retry(self):
        self.steam_proxy = self.STEAM_PROXY
        self.discord_proxy = None
        self.replies = [httpx.ConnectTimeout("proxy connection"), (200, {})]

        await steam._get(self.STORE)

        self.assertEqual([proxy for _, proxy, _ in self.requests], [self.STEAM_PROXY, self.STEAM_PROXY])

    async def test_server_disconnect_uses_discord_fallback_after_direct_request(self):
        self.replies = [httpx.RemoteProtocolError("remote disconnected"), (200, {})]

        await steam._get(self.STORE)

        self.assertEqual([proxy for _, proxy, _ in self.requests], [None, self.DISCORD_PROXY])

    async def test_rate_limit_waits_on_same_route_for_seconds_and_http_date(self):
        self.replies = [
            (429, {"Retry-After": "5"}),
            (429, {"Retry-After": "Thu, 01 Jan 1970 00:17:00 GMT"}),
            (200, {}),
        ]

        await steam._get(self.STORE)

        self.assertEqual([proxy for _, proxy, _ in self.requests], [None, None, None])
        self.assertEqual([timestamp for _, _, timestamp in self.requests], [1000, 1005, 1020])

    async def test_long_rate_limit_defers_same_host_but_allows_other_hosts(self):
        self.replies = [(429, {"Retry-After": "300"}), (200, {}), (200, {})]

        with self.assertRaisesRegex(httpx.HTTPError, "HTTP 429"):
            await steam._get(self.STORE)
        with self.assertRaisesRegex(httpx.HTTPError, "HTTP 429"):
            await steam._get("https://store.steampowered.test/catalog")
        await steam._get(self.COMMUNITY)
        self.now += 300
        await steam._get(self.STORE)

        self.assertEqual([url for url, _, _ in self.requests], [self.STORE, self.COMMUNITY, self.STORE])
        self.assertGreaterEqual(self.requests[-1][2] - self.requests[0][2], 300)

    async def test_source_is_visible_during_startup_and_blank_error_is_reported(self):
        owner = "steam-startup-test"
        initial = {"last_completed_poll_ts": 950}
        settings = {"sources": [CREATOR], "routes": {"s2": ["group"]}, "region": "CN",
                    "sources_total": 1, "sources_enabled": 1, "protection": {}}

        async def poll_source(*args):
            self.assertTrue(steam._status[owner]["scanning"])
            self.assertEqual(steam._status[owner]["current_source"], "Valve")
            self.assertEqual(steam._status[owner]["last_completed_poll_ts"], 950)
            raise httpx.ConnectTimeout("")

        with mock.patch.object(steam, "_status", {owner: initial}), \
                mock.patch.object(steam, "_snapshot", return_value={"sequence": 0, "queue": []}), \
                mock.patch.object(steam, "_change"), \
                mock.patch.object(steam, "_poll_source", side_effect=poll_source):
            await steam._poll_owner(owner, settings)
            result = steam._status[owner]

        self.assertFalse(result["scanning"])
        self.assertEqual(result["last_completed_poll_ts"], 1000)
        self.assertEqual(result["source_health"][0]["error"], "ConnectTimeout")


class SteamRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "steam.json")
        self.path_patch = mock.patch.object(steam, "_path", return_value=self.path)
        self.path_patch.start()
        self.real_card_avatar_url = steam._card_avatar_url
        self.avatar_patch = mock.patch.object(steam, "_card_avatar_url", new=mock.AsyncMock(return_value=""))
        self.avatar_patch.start()
        steam._states.clear(); steam._locks.clear(); steam._delivery_locks.clear(); steam._retry_after.clear(); steam._poll_jobs.clear(); steam._next_poll.clear(); steam._card_images.clear()
        message_card._avatar_cache.clear()

    async def asyncTearDown(self):
        self.path_patch.stop()
        self.avatar_patch.stop()
        # Windows can briefly keep a recently replaced JSON or lock file open.
        for attempt in range(5):
            try:
                self.directory.cleanup()
                break
            except OSError:
                if attempt == 4:
                    raise
                await asyncio.sleep(0.1 * (attempt + 1))
        steam._states.clear(); steam._locks.clear(); steam._delivery_locks.clear(); steam._retry_after.clear(); steam._poll_jobs.clear(); steam._next_poll.clear(); steam._card_images.clear()
        message_card._avatar_cache.clear()

    async def test_card_avatar_success_and_failure_still_deliver(self):
        from PIL import Image, ImageFont

        image = BytesIO()
        Image.new("RGB", (100, 100), (240, 30, 50)).save(image, "PNG")
        icon_url = "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/620/" + "a" * 40 + ".jpg"
        assets = {"620": {"assets": {"community_icon": "a" * 40}}}
        settings = {"sources": [GAME], "routes": {"s1": ["g1"]},
                    "protection": {"min_send_interval": 1, "hourly_limit": 30}}
        delivery = {"image_card_enabled": True, "image_card_theme": "dark", "translation": {}}
        with mock.patch.object(steam, "_card_avatar_url", new=self.real_card_avatar_url), \
             mock.patch.object(message_card, "_font", side_effect=lambda size, bold=False, emoji=False: ImageFont.load_default(size=size)), \
             mock.patch.object(steam, "_browse", new=mock.AsyncMock(return_value=assets)), \
             mock.patch.object(media, "fetch_bytes", new=mock.AsyncMock(side_effect=[image.getvalue(), None])) as download, \
             mock.patch.object(config, "get_steam_source_delivery_config", return_value=delivery), \
             mock.patch.object(steam, "translate_text_parts", new=mock.AsyncMock(side_effect=lambda parts, *args, **kwargs: parts)), \
             mock.patch.object(steam, "load_last_messages_async", new=mock.AsyncMock(return_value={})), \
             mock.patch.object(steam, "send_and_record", new=mock.AsyncMock(return_value={"g1": True})) as send, \
             mock.patch.object(steam, "record", new=mock.AsyncMock()):
            for index, expected in enumerate(((240, 30, 50, 255), (138, 146, 158, 255)), 1):
                await steam._change("", lambda state: steam_state.enqueue(
                    state, "s1", "g1", "card" + str(index),
                    "Portal 2\nNews\nhttps://store.steampowered.com/app/620/", 20, "news", "620"))
                message_card._avatar_cache.clear()
                await steam._dispatch_owner("", settings)
                self.assertEqual((await steam._snapshot(""))["queue"], [])
                args = send.await_args.args
                self.assertEqual(args[2], [])
                self.assertEqual(args[3][0]["kind"], "转发卡片")
                with Image.open(BytesIO(args[3][0]["segment"].data["content"])) as card:
                    self.assertEqual(card.getpixel((message_card.PAD + 30, 55)), expected)
                await steam._change("", lambda state: state.update(sent_at=[]))
        self.assertEqual(send.await_count, 2)
        self.assertEqual(download.await_count, 2)
        self.assertEqual(download.await_args.args[0], icon_url)

    async def test_atomic_queue_failure_preserves_snapshot(self):
        await steam._change("", lambda state: steam_state.enqueue(state, "s1", "g1", "item1", "first", 1, "news", "620"))
        old = await steam._snapshot("")
        with self.assertRaises(OverflowError):
            await steam._change("", lambda state: steam_state.enqueue(state, "s1", "g1", "item2", "second", 1, "news", "620"))
        self.assertEqual(await steam._snapshot(""), old)
        steam._states.clear()
        self.assertEqual(await steam._snapshot(""), old)

    async def test_cancelled_write_keeps_disk_and_cache_in_sync(self):
        entered, release = threading.Event(), threading.Event()
        real_write = steam.atomic_write_json
        def slow_write(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("write was not released")
            return real_write(*args, **kwargs)
        with mock.patch.object(steam, "atomic_write_json", side_effect=slow_write):
            task = asyncio.create_task(steam._change("", lambda state: steam_state.enqueue(state, "s1", "g1", "one", "first", 20, "news", "620")))
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await steam._change("", lambda state: steam_state.enqueue(state, "s1", "g1", "two", "second", 20, "news", "620"))
        self.assertEqual([item["identity"] for item in (await steam._snapshot(""))["queue"]], ["one", "two"])
        self.assertEqual(len(json.loads(Path(self.path).read_text(encoding="utf-8"))["queue"]), 2)

    async def test_failure_in_one_group_does_not_block_another(self):
        await steam._change("", lambda state: steam_state.enqueue(state, "s1", "g1", "item1", "first", 20, "news", "620"))
        await steam._change("", lambda state: steam_state.enqueue(state, "s1", "g2", "item2", "second", 20, "news", "620"))
        settings = {"sources": [GAME], "routes": {"s1": ["g1", "g2"]}, "protection": {"min_send_interval": 1, "hourly_limit": 30}}
        with mock.patch.object(steam, "load_last_messages_async", new=mock.AsyncMock(return_value={})), \
             mock.patch.object(steam, "send_and_record", new=mock.AsyncMock(side_effect=[{"g1": False}, {"g2": True}])), \
             mock.patch.object(steam, "record", new=mock.AsyncMock()):
            await steam._dispatch_owner("", settings)
            await steam._dispatch_owner("", settings)
        self.assertEqual([item["group"] for item in (await steam._snapshot(""))["queue"]], ["g1"])

    async def test_dispatch_discards_unroutable_queue_without_changing_baselines(self):
        await steam._change("", lambda state: steam_state.enqueue(state, "s1", "g1", "one", "first", 20, "news", "620"))
        await steam._change("", lambda state: steam_state.enqueue(state, "s1", "g2", "two", "second", 20, "news", "620"))
        settings = {"sources": [GAME], "routes": {"s1": ["g1"]},
                    "protection": {"min_send_interval": 1, "hourly_limit": 30}}
        before = await steam._snapshot("")
        with mock.patch.object(steam, "load_last_messages_async", new=mock.AsyncMock(return_value={})), \
             mock.patch.object(steam, "send_and_record", new=mock.AsyncMock(return_value={"g1": False})), \
             mock.patch.object(steam, "_card_avatar_url", new=mock.AsyncMock(return_value="")), \
             mock.patch.object(steam, "record", new=mock.AsyncMock()):
            await steam._dispatch_owner("", settings)
        after = await steam._snapshot("")
        self.assertEqual([item["group"] for item in after["queue"]], ["g1"])
        self.assertEqual(after["routes"], before["routes"])

    async def test_rss_fallback_preserves_watermark_for_api_catchup(self):
        source = dict(GAME, events=["announcements"])
        sig = steam_state.signature("game", "620")
        first = {"id": "old", "title": "old", "link": "https://example.com/old", "published": 1700000000}
        missed = {"id": "missed", "title": "missed", "link": "https://example.com/missed", "published": 1700000500}
        new = {"id": "new", "title": "new", "link": "https://example.com/new", "published": 1700001000}
        await steam._change("", lambda state: steam_state.feed_update(state, source, "g1", "announcements", [first], sig, 10))
        xml = b'<rss><channel><item><guid>new</guid><title>new</title><link>https://example.com/new</link><pubDate>Tue, 14 Nov 2023 22:30:00 +0000</pubDate></item></channel></rss>'
        with mock.patch.object(steam, "_get", new=mock.AsyncMock(return_value=mock.Mock(content=xml))), \
             mock.patch.object(steam, "_news", new=mock.AsyncMock(side_effect=[RuntimeError("offline"), [first, missed, new]])):
            with self.assertRaises(RuntimeError):
                await steam._poll_game_feeds("", source, ["g1"], 10)
            state = await steam._snapshot("")
            self.assertEqual(state["routes"]["s1"]["g1"]["announcements"]["watermark"], 1700000000)
            self.assertEqual(len(state["queue"]), 1)
            await steam._poll_game_feeds("", source, ["g1"], 10)
        state = await steam._snapshot("")
        self.assertEqual(len(state["queue"]), 2)
        self.assertEqual(state["routes"]["s1"]["g1"]["announcements"]["watermark"], 1700001000)

    async def test_empty_feed_baseline_still_paginates_later_news(self):
        sig = steam_state.signature("game", "620", [])
        await steam._change("", lambda state: steam_state.feed_update(state, GAME, "g1", "news", [], sig, 200))
        state = await steam._snapshot("")
        watermark = steam._watermark(state, "s1", ["g1"], "news", sig)
        self.assertEqual(watermark, 0)
        page = [{"gid": str(i), "title": "item " + str(i), "url": "https://example.com/" + str(i),
                 "date": 1700000000 + i} for i in range(1, 101)]
        first = mock.Mock(); first.json.return_value = {"appnews": {"appid": 620, "newsitems": page}}
        second = mock.Mock(); second.json.return_value = {"appnews": {"appid": 620, "newsitems": []}}
        with mock.patch.object(steam, "_get", new=mock.AsyncMock(side_effect=[first, second])) as get:
            result = await steam._news("620", watermark)
        self.assertEqual(len(result), 100)
        self.assertEqual(get.await_count, 2)

    async def test_game_source_discovers_dlc_and_bundle_prices_end_to_end(self):
        source = dict(GAME, events=["discount"], include_dlc=True, include_bundles=True)
        changed = False
        async def details(appid, region, cache):
            data = game(appid, price=500 if changed and appid == "621" else 1000,
                        percent=50 if changed and appid == "621" else 0)
            data["type"] = "dlc" if appid == "621" else "game"
            data["dlc"] = ["621"] if appid == "620" else []
            return data
        async def browse(ids, field, region):
            if field == "appid":
                return {appid: {"visible": True, "purchase_options": [{"bundleid": 232}]} for appid in ids}
            return {"232": {"visible": True, "purchase_options": [{"bundleid": 232,
                "purchase_option_name": "Bundle", "final_price_in_cents": "700" if changed else "1000",
                "formatted_final_price": "¥7" if changed else "¥10", "combined_discount_pct": 30 if changed else 0}]}}
        with mock.patch.object(steam, "_details", side_effect=details), mock.patch.object(steam, "_browse", side_effect=browse):
            await steam._poll_source("", copy.deepcopy(source), ["g1"], "CN", 20, {})
            self.assertEqual((await steam._snapshot(""))["queue"], [])
            changed = True
            await steam._poll_source("", copy.deepcopy(source), ["g1"], "CN", 20, {})
        queue = (await steam._snapshot(""))["queue"]
        self.assertEqual({item["category"] for item in queue}, {"dlc", "bundle"})

    async def test_panel_api_saves_account_settings_and_proxies_steam(self):
        root = Path(self.directory.name)
        settings_path = root / "settings.json"
        initial = SettingsModel.default().to_dict()
        initial["qq_group_openids"] = [dict(openid="G1", name="group", enabled=True, bot_id="111")]
        settings_path.write_text(json.dumps(initial), encoding="utf-8")
        bot_profiles.migrate(str(settings_path), ["111"])
        panel_file = Path(__file__).resolve().parents[1] / "panel" / "管理面板.pyw"
        loader = SourceFileLoader("steam_test_panel", str(panel_file))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        panel = importlib.util.module_from_spec(spec)
        saved_stdout, saved_stderr = sys.stdout, sys.stderr
        with mock.patch.object(paths, "DATA_DIR", root):
            try:
                loader.exec_module(panel)
            finally:
                sys.stdout, sys.stderr = saved_stdout, saved_stderr
            try:
                with mock.patch.object(panel, "_ensure_bot_profiles"), \
                     mock.patch.object(panel, "bot_status", return_value={"running": True}), \
                     mock.patch.object(panel, "bot_api_get", return_value={"ok": True, "queue_pending": 3}):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel.app), base_url="http://127.0.0.1") as client:
                        unauthorized = await client.get("/api/settings?bot_id=111")
                        self.assertEqual(unauthorized.status_code, 401)
                        headers = {"X-WRelay-Token": panel.PANEL_TOKEN}
                        response = await client.get("/api/settings?bot_id=111", headers=headers)
                        body = response.json()
                        body["steam_sources"] = [copy.deepcopy(GAME)]
                        body["steam_forwarding_groups"] = [dict(id="steam-group", name="Steam", enabled=True, sources=["s1"], groups=["G1"])]
                        result = await client.post("/api/settings/save?bot_id=111", headers=headers, json=body)
                        self.assertTrue(result.json().get("ok"), result.text)
                        self.assertEqual(bot_profiles.read(str(settings_path), "111")["steam_sources"][0]["id"], "s1")
                        status = await client.get("/api/steam/status?bot_id=111", headers=headers)
                        self.assertEqual(status.json()["queue_pending"], 3)
            finally:
                panel._panel_log._file.close()

    async def test_real_send_pipeline_deduplicates_across_sources(self):
        class FakeOfficialBot:
            self_id = "111"
            def __init__(self): self.messages = []
            async def send_to_group(self, group_openid, message):
                self.messages.append((group_openid, str(message)))
        bot = FakeOfficialBot()
        second = dict(GAME, id="s2")
        await steam._change("", lambda state: steam_state.enqueue(state, "s1", "g1", "unique-article", "Steam notice", 20, "news", "620"))
        await steam._change("", lambda state: steam_state.enqueue(state, "s2", "g1", "unique-article", "Steam notice", 20, "news", "620"))
        settings = {"sources": [GAME, second], "routes": {"s1": ["g1"], "s2": ["g1"]}, "protection": {"min_send_interval": 1, "hourly_limit": 30}}
        with mock.patch.object(history, "LAST_MESSAGE_FILE", str(Path(self.directory.name) / "last.json")), \
             mock.patch.multiple(history, _last=None, _last_mtime=None, _last_size=None), \
             mock.patch.object(delivery_dedup, "DELIVERY_DEDUP_DB_FILE", str(Path(self.directory.name) / "dedup.sqlite3")), \
             mock.patch.object(delivery_dedup, "_volatile_deliveries", OrderedDict()), \
             mock.patch.object(sender, "get_qq_bot", return_value=bot), \
             mock.patch.object(sender, "get_qq_bots", return_value={"111": bot}), \
             mock.patch.object(sender, "get_group_bot_ids", return_value={"g1": "111"}), \
             mock.patch.object(sender.personal_qq, "connected_bots", return_value={}), \
             mock.patch.object(sender, "_record_trace", new=mock.AsyncMock()), \
             mock.patch.object(retry, "_purge_delivered_async", new=mock.AsyncMock()), \
             mock.patch.object(steam, "record", new=mock.AsyncMock()):
            await steam._dispatch_owner("", settings)
            await steam._change("", lambda state: state.update(sent_at=[]))
            await steam._dispatch_owner("", settings)
            self.assertEqual(len(bot.messages), 1)
            self.assertIn("Steam notice", bot.messages[0][1])
            self.assertEqual((await steam._snapshot(""))["queue"], [])

    async def test_personal_qq_uses_the_existing_delivery_guard(self):
        class FakeOneBot:
            self_id = "12345"
            def __init__(self): self.messages = []
            async def send_group_msg(self, group_id, message):
                self.messages.append((group_id, message))
                return {"message_id": 1}
        bot = FakeOneBot()
        personal = personal_qq.PersonalSender(bot)
        group = "personal:98765"
        await steam._change("", lambda state: steam_state.enqueue(state, "s1", group, "personal-article", "Steam notice", 20, "news", "620"))
        settings = {"sources": [GAME], "routes": {"s1": [group]}, "protection": {"min_send_interval": 1, "hourly_limit": 30}}
        with mock.patch.object(history, "LAST_MESSAGE_FILE", str(Path(self.directory.name) / "last.json")), \
             mock.patch.multiple(history, _last=None, _last_mtime=None, _last_size=None), \
             mock.patch.object(delivery_dedup, "DELIVERY_DEDUP_DB_FILE", str(Path(self.directory.name) / "dedup.sqlite3")), \
             mock.patch.object(delivery_dedup, "_volatile_deliveries", OrderedDict()), \
             mock.patch.object(personal_qq, "UNCERTAIN_FILE", str(Path(self.directory.name) / "uncertain.json")), \
             mock.patch.object(sender, "get_qq_bot", return_value=None), \
             mock.patch.object(sender, "get_qq_bots", return_value={}), \
             mock.patch.object(personal_qq, "any_connected", return_value=True), \
             mock.patch.object(personal_qq, "connected_bots", return_value={"personal:12345": personal}), \
             mock.patch.object(personal_qq, "_ready", {"12345": personal}), \
             mock.patch.object(personal_qq.config, "enabled_connections", return_value={"12345": {}}), \
             mock.patch.object(sender, "get_group_bot_ids", return_value={group: "personal:12345"}), \
             mock.patch.object(sender, "_record_trace", new=mock.AsyncMock()), \
             mock.patch.object(retry, "_purge_delivered_async", new=mock.AsyncMock()), \
             mock.patch.object(steam, "record", new=mock.AsyncMock()):
            await steam._dispatch_owner("", settings)
            self.assertEqual(len(bot.messages), 1)
            self.assertEqual(bot.messages[0][0], 98765)
            self.assertEqual((await steam._snapshot(""))["queue"], [])
            self.assertEqual(personal_qq.uncertain_records(), {})

    async def test_personal_qq_timeout_is_not_automatically_resent(self):
        class TimedOutOneBot:
            self_id = "12345"
            def __init__(self): self.attempts = 0
            async def send_group_msg(self, group_id, message):
                self.attempts += 1
                raise TimeoutError("unknown delivery outcome")
        bot = TimedOutOneBot()
        personal = personal_qq.PersonalSender(bot)
        group = "personal:98765"
        await steam._change("", lambda state: steam_state.enqueue(state, "s1", group, "uncertain-article", "Steam notice", 20, "news", "620"))
        settings = {"sources": [GAME], "routes": {"s1": [group]}, "protection": {"min_send_interval": 1, "hourly_limit": 30}}
        with mock.patch.object(history, "LAST_MESSAGE_FILE", str(Path(self.directory.name) / "last.json")), \
             mock.patch.multiple(history, _last=None, _last_mtime=None, _last_size=None), \
             mock.patch.object(delivery_dedup, "DELIVERY_DEDUP_DB_FILE", str(Path(self.directory.name) / "dedup.sqlite3")), \
             mock.patch.object(delivery_dedup, "_volatile_deliveries", OrderedDict()), \
             mock.patch.object(personal_qq, "UNCERTAIN_FILE", str(Path(self.directory.name) / "uncertain.json")), \
             mock.patch.object(sender, "get_qq_bot", return_value=None), \
             mock.patch.object(sender, "get_qq_bots", return_value={}), \
             mock.patch.object(personal_qq, "any_connected", return_value=True), \
             mock.patch.object(personal_qq, "connected_bots", return_value={"personal:12345": personal}), \
             mock.patch.object(personal_qq, "_ready", {"12345": personal}), \
             mock.patch.object(personal_qq.config, "enabled_connections", return_value={"12345": {}}), \
             mock.patch.object(sender, "get_group_bot_ids", return_value={group: "personal:12345"}), \
             mock.patch.object(sender, "_record_trace", new=mock.AsyncMock()), \
             mock.patch.object(retry, "_purge_delivered_async", new=mock.AsyncMock()), \
             mock.patch.object(steam, "record", new=mock.AsyncMock()):
            await steam._dispatch_owner("", settings)
            self.assertEqual(bot.attempts, 1)
            pending = personal_qq.uncertain_records()
            self.assertEqual(len(pending), 1)
            steam._retry_after.clear()
            await steam._dispatch_owner("", settings)
            self.assertEqual(bot.attempts, 1)
            self.assertEqual(len((await steam._snapshot(""))["queue"]), 1)
            await personal_qq.resolve_uncertain(next(iter(pending)), "delivered")
            steam._retry_after.clear()
            await steam._dispatch_owner("", settings)
            self.assertEqual(bot.attempts, 1)
            self.assertEqual((await steam._snapshot(""))["queue"], [])


if __name__ == "__main__":
    unittest.main()
