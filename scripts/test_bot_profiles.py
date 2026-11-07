"""Temporary-file checks for account isolation, reviewed imports and full backups."""
import asyncio
import io
import json
import sys
import tempfile
import zipfile
import importlib.util
from importlib.machinery import SourceFileLoader
from copy import deepcopy
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nonebot
nonebot.init(_env_file=None)
from nonebot import logger
from plugins import bot_profiles as store, config
from plugins.json_io import ConcurrentWriteError, json_file_lock, _atomic_write_json_unlocked
from plugins.settings_model import SettingsModel, validate_settings
from plugins.profile_runtime import RSSProfiles
from panel.profile_api import portable, prepare
from panel.backup_restore import BackupRestoreService
import httpx


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value), encoding="utf-8")


async def main():
    with tempfile.TemporaryDirectory(prefix="wrelay_profiles_") as tmp:
        root = Path(tmp)
        path = str(root / "settings.json")
        legacy = SettingsModel.default().to_dict()
        legacy.update(discord_channels=[dict(id="12345", name="channel", enabled=True)],
            qq_group_openids=[dict(openid="G1", name="one", enabled=True, bot_id="111"),
                dict(openid="G2", name="two", enabled=True, bot_id="222"),
                dict(openid="personal:98765", name="personal", enabled=True, bot_id="personal:12345")],
            x_rss_sources=[dict(source="alice", name="Alice", enabled=True, filter_keywords=["one"])],
            forwarding_groups=[dict(id="same", name="Discord", enabled=True, channels=["12345"], groups=["G1", "G2"])],
            x_forwarding_groups=[dict(id="same", name="X", enabled=True, sources=["alice"], groups=["G1", "G2", "personal:98765"])])
        legacy["ai"]["api_key"] = "must-not-export"
        write(path, legacy)
        write(root / "x_rss.json", {"alice": {"last_id": "100"}})
        original = Path(path).read_bytes()
        store.migrate(path, ["111", "222", "personal:12345"])
        assert Path(path + ".before-bot-profiles.json").read_bytes() == original
        assert "x_rss_sources" not in store.strict_read(path)
        assert store.read(path, "111")["forwarding_groups"][0]["groups"] == ["G1"]
        assert store.read(path, "222")["forwarding_groups"][0]["groups"] == ["G2"]
        assert store.strict_read(store.runtime_path(path, "222", "x_rss.json"))["alice"]["last_id"] == "100"
        first_revision = store.revision(path)
        store.migrate(path, ["111", "222", "personal:12345"])
        assert store.revision(path) == first_revision, "Repeated account lookup must not invalidate previews"
        other = Path(store.profile_path(path, "222")).read_bytes()
        data = store.read(path, "111")
        data["x_rss_sources"][0]["filter_keywords"] = ["changed"]
        data["x_rss_poll_interval"] = 600
        store.save(path, data, first_revision)
        assert Path(store.profile_path(path, "222")).read_bytes() == other
        assert store.read(path, "222")["x_rss_poll_interval"] != 600
        try:
            store.save(path, data, first_revision)
            raise AssertionError("stale save allowed")
        except ConcurrentWriteError:
            pass
        print("PASS - migration, independent source settings, stable reads and stale-save protection")

        document = portable(path, "111")
        assert "must-not-export" not in json.dumps(document)
        assert "qq_user_openids" not in document["config"]
        body = dict(document=document, mode="merge", mapping={"G1": "G2"})
        candidate, revision, digest, warnings = prepare(path, "222", body)
        assert prepare(path, "222", body)[2] == digest
        assert candidate["x_forwarding_groups"][-1]["groups"] == ["G2"]
        assert candidate["x_rss_sources"][0]["filter_keywords"] == ["one"]
        assert warnings
        alias = deepcopy(document)
        alias["config"]["x_rss_sources"][0]["source"] = "@ALICE"
        alias["config"]["x_forwarding_groups"][0]["sources"] = ["ALICE"]
        aliased = prepare(path, "222", dict(body, document=alias))[0]
        assert len(aliased["x_rss_sources"]) == 1
        assert aliased["x_forwarding_groups"][-1]["sources"] == ["alice"]
        personal = prepare(path, "personal:12345", dict(body, mapping={"G1": "personal:98765"}))[0]
        assert personal["x_forwarding_groups"][-1]["id"] != candidate["x_forwarding_groups"][-1]["id"]
        try:
            prepare(path, "222", dict(body, document=dict(document, version=True)))
            raise AssertionError("boolean format version accepted")
        except ValueError:
            pass
        for mapping in ({"G1": "G1"}, {"G1": "unknown"}):
            try:
                prepare(path, "222", dict(body, mapping=mapping))
                raise AssertionError("foreign group accepted")
            except ValueError:
                pass
        paused = prepare(path, "222", dict(body, mapping={}))[0]
        assert paused["x_forwarding_groups"][-1]["enabled"] is False
        store.save(path, candidate, revision)
        assert Path(store.profile_path(path, "222") + ".bak").exists()
        assert len(prepare(path, "222", body)[0]["x_forwarding_groups"]) == len(candidate["x_forwarding_groups"])
        old_root = Path(path).read_bytes()
        old_profile = Path(store.profile_path(path, "111")).read_bytes()
        original_write = store._atomic_write_json_unlocked
        def fail_root(target, *args, **kwargs):
            if target == path:
                raise OSError("injected disk error")
            original_write(target, *args, **kwargs)
        with mock.patch.object(store, "_atomic_write_json_unlocked", side_effect=fail_root):
            try:
                store.save(path, store.read(path, "111"))
                raise AssertionError("failure ignored")
            except OSError:
                pass
        assert Path(path).read_bytes() == old_root
        assert Path(store.profile_path(path, "111")).read_bytes() == old_profile
        print("PASS - portable export, explicit mapping, deterministic merge, conflict checks and rollback")

        store.migrate(path, ["333"])
        with mock.patch.object(config, "SETTINGS_FILE", path), store.scope("333"):
            assert config.get_forwarding_groups() == []
            assert config.get_x_forwarding_groups() == []
            assert config.get_bilibili_forwarding_groups() == []
            assert config.get_youtube_forwarding_groups() == []
        broken = Path(store.profile_path(path, "111"))
        valid = broken.read_bytes()
        broken.write_text("bad", encoding="utf-8")
        try:
            store.read(path, "111")
            raise AssertionError("corrupt account became defaults")
        except ValueError:
            pass
        assert store.read(path, "222")["x_rss_sources"]
        assert store.read(path)["_profile_errors"] == ["111"]
        with mock.patch.object(config, "SETTINGS_FILE", path):
            runtime_check = RSSProfiles({"_runtime_status": {}, "logger": logger}, "x")
            result = await runtime_check.status("111")
            assert result["last_result"] == "config_failed" and result["config_error"]
            assert not result["protection_error"]
            all_status = await runtime_check.status_all()
            assert "111" in all_status["config_error"] and not all_status["protection_error"]
        broken.write_bytes(valid)
        print("PASS - empty account stays unrouted; broken account fails closed without blocking others")

        ns = {"X_STATE_FILE": "x_rss.json", "_posts_cache": {}, "_runtime_status": {}, "logger": logger}
        runtime = RSSProfiles(ns, "x")
        seen = []
        async def operation():
            owner = store.CURRENT.get()
            ns["_posts_cache"]["alice"] = owner
            ns["_runtime_status"]["owner"] = owner
            seen.append((owner, ns["X_STATE_FILE"]))
            await asyncio.sleep(0)
            assert ns["_posts_cache"]["alice"] == owner
        with mock.patch.object(config, "SETTINGS_FILE", path):
            await asyncio.gather(runtime.call("111", operation), runtime.call("222", operation))
        assert seen[0][1] != seen[1][1]
        assert runtime.states["111"]["_posts_cache"]["alice"] == "111"
        assert ns["_posts_cache"] == {} and ns["X_STATE_FILE"] == "x_rss.json"
        print("PASS - simultaneous RSS operations isolate state, caches and file paths")

        async def interval():
            return await asyncio.to_thread(config.get_x_rss_poll_interval)
        ns["_read_poll_interval"] = interval
        with mock.patch.object(config, "SETTINGS_FILE", path), mock.patch("plugins.sender.get_qq_bots", return_value={"111": object(), "222": object()}), mock.patch("plugins.personal_qq.connected_bots", return_value={}):
            seen.clear()
            await runtime.poll(operation)
            assert [owner for owner, _ in seen] == ["111", "222"]
            assert runtime.next["111"] > runtime.next["222"]
            seen.clear()
            await runtime.poll(operation)
            assert not seen
            runtime.states["111"]["_runtime_status"]["source_health"] = [{"id": "alice", "name": "Alice", "consecutive_failures": 3}]
            status = await runtime.status_all()
            assert status["source_health"][0]["id"] == "111:alice"
            assert status["sources_total"] >= 2
        started, finish = asyncio.Event(), asyncio.Event()
        async def long_operation():
            started.set()
            await finish.wait()
            assert store.CURRENT.get() == "111" and "official_111" in ns["X_STATE_FILE"]
        with mock.patch.object(config, "SETTINGS_FILE", path):
            task = asyncio.create_task(runtime.call("111", long_operation))
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            waiting = asyncio.create_task(runtime.call("222", operation))
            await asyncio.sleep(0)
            assert not waiting.done()
            finish.set()
            await asyncio.gather(task, waiting, return_exceptions=True)
            assert task.cancelled() and waiting.exception() is None
        print("PASS - independent RSS intervals, maintenance aggregation and repeated-cancel isolation")

        from plugins import relay, retry, backfill
        for owner, words in (("111", ["block"]), ("222", [])):
            value = store.read(path, owner)
            value["discord_channels"][0]["filter_keywords"] = words
            store.save(path, value)
        rendered_filters, sent_groups = {}, []
        async def render(message, channel_id, message_id, **kwargs):
            rendered_filters[store.CURRENT.get()] = kwargs["filter_config"]["filter_keywords"]
            return ["content"], []
        async def send(channel_id, message_id, text, media, groups):
            sent_groups.extend(groups)
            return retry.SendResult({g: True for g in groups})
        entry = mock.Mock(groups={"G1", "G2"}, message={}, source_label="test")
        observed = []
        async def event(bot, value):
            snapshot = await asyncio.to_thread(config.get_discord_relay_config, "12345")
            if snapshot:
                observed.append((store.CURRENT.get(), snapshot["filter"]["filter_keywords"]))
        async def missed():
            observed.append((store.CURRENT.get(), (await asyncio.to_thread(config.get_backfill_runtime_snapshot))["limit"]))
        with mock.patch.object(config, "SETTINGS_FILE", path):
            with mock.patch.object(retry, "prepare_discord_message", side_effect=render), mock.patch.object(retry, "send_and_record", side_effect=send):
                result = await retry._send_profile_retry("12345", "101", entry, store.ids(path))
            assert result == {"G1": True, "G2": True}
            assert rendered_filters == {"111": ["block"], "222": []}
            assert sent_groups == ["G1", "G2"]
            with mock.patch.object(relay, "_process_profile_event", side_effect=event):
                await relay._process_event(None, None)
            assert ("111", ["block"]) in observed and ("222", []) in observed
            observed.clear()
            with mock.patch.object(backfill, "_backfill_missed_profile", side_effect=missed):
                with store.scope("222"):
                    await backfill._backfill_missed()
            assert [owner for owner, _ in observed] == ["222"]
        print("PASS - live Discord routing, retry rendering and requested backfill use account-specific settings")

        # Exercise the real protected panel routes without reading production credentials.
        panel_path = Path(__file__).resolve().parents[1] / "panel" / "管理面板.pyw"
        spec = importlib.util.spec_from_file_location("profile_test_panel", panel_path, loader=SourceFileLoader("profile_test_panel", str(panel_path)))
        panel = importlib.util.module_from_spec(spec)
        old_stdout, old_stderr = sys.stdout, sys.stderr
        try:
            spec.loader.exec_module(panel)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
        with mock.patch.object(panel, "SETTINGS_FILE", path), mock.patch.object(panel, "_ensure_bot_profiles", return_value=None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel.app), base_url="http://127.0.0.1") as client:
                endpoint = "/api/bot-profile/import?bot_id=222"
                assert (await client.post(endpoint, json=body)).status_code == 401
                headers = {"X-WRelay-Token": panel.PANEL_TOKEN}
                before = Path(store.profile_path(path, "222")).read_bytes()
                reviewed = (await client.post(endpoint, json=body, headers=headers)).json()
                assert reviewed["ok"] and "preview_hash" in reviewed
                assert Path(store.profile_path(path, "222")).read_bytes() == before
                rejected = await client.post(endpoint, json=dict(body, confirm=True, revision=reviewed["revision"], preview_hash="wrong"), headers=headers)
                assert rejected.status_code == 400
                saved = await client.post(endpoint, json=dict(body, confirm=True, revision=reviewed["revision"], preview_hash=reviewed["preview_hash"]), headers=headers)
                assert saved.json().get("saved") is True, saved.text
                loaded = await client.get("/api/settings?bot_id=222", headers=headers)
                assert loaded.json()["_profile_bot"] == "222" and loaded.json()["_revision"]
                loaded_empty = await client.get("/api/settings?bot_id=333", headers=headers)
                response = await client.post("/api/settings/save?bot_id=333", json=loaded_empty.json(), headers=headers)
                assert response.json()["ok"], response.text
                foreign = loaded.json()
                foreign["_revision"] = store.revision(path)
                response = await client.post("/api/settings/save?bot_id=111", json=foreign, headers=headers)
                assert response.status_code == 400
        print("PASS - protected panel preview/confirmation, empty-account saves and account mismatch rejection")

        service = BackupRestoreService(path, str(root / "discord_last.json"), str(root / "retry_pending.json"),
            str(root / "delivery_dedup.sqlite3"), str(root / "rollbacks"), json_file_lock,
            _atomic_write_json_unlocked, lambda: SettingsModel.default().to_dict(), validate_settings, logger)
        backup = service.create_backup_archive()
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            assert "bot_profiles/official_111.json" in archive.namelist()
            assert not any(name.startswith("bot_profile_runtime/") for name in archive.namelist())
        state_backup = service.create_backup_archive(True)
        assert "bot_profile_runtime/official_111/x_rss.json" in service.read_restore_archive(state_backup)
        write(path, SettingsModel.default().to_dict())
        service.restore_backup_archive(backup)
        assert store.read(path, "222")["x_rss_sources"][0]["source"] == "alice"
        for unsafe in ("bot_profiles/../../oops.json", "bot_profiles/official_1.json/extra", "bot_profile_runtime/official_111/private.json"):
            try:
                service.profile_target(unsafe)
                raise AssertionError("unsafe archive path accepted")
            except ValueError:
                pass
        print("PASS - full backup and restore include profiles, optional account state and strict path checks")
        current_state = Path(store.runtime_path(path, "111", "x_rss.json"))
        current_state.write_bytes(b"preserve-newer-runtime")
        legacy["backfill_limit"] = 17
        write(path, legacy)
        store.migrate(path, ["111", "222", "personal:12345"])
        assert current_state.read_bytes() == b"preserve-newer-runtime"
        assert list(root.glob("settings.json.before-bot-profiles.*.json"))
        assert Path(store.profile_path(path, "111") + ".bak").exists()
        print("PASS - restoring legacy settings can migrate again without overwriting newer account runtime")


if __name__ == "__main__":
    asyncio.run(main())
