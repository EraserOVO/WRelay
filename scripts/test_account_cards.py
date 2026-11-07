"""Account migration, scheduled targets and delivery history in a temporary workspace."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nonebot
nonebot.init(_env_file=None)
from plugins import bot_profiles, config, message_trace, stats, scheduled_message
from plugins.settings_model import SettingsModel


async def main():
    with tempfile.TemporaryDirectory(prefix="wrelay-account-cards-") as temp:
        root = Path(temp)
        settings = root / "settings.json"
        legacy = SettingsModel.default().to_dict()
        legacy["qq_group_openids"] = [
            {"openid": "g1", "enabled": True, "bot_id": "111"},
            {"openid": "g2", "enabled": True, "bot_id": "222"},
        ]
        legacy["qq_user_openids"] = [{"openid": "user", "enabled": True}]
        legacy["scheduled_message"]["target_groups"] = ["g1", "g2"]
        settings.write_text(json.dumps(legacy), encoding="utf-8")
        with patch.object(config, "SETTINGS_FILE", str(settings)):
            bot_profiles.migrate(str(settings), ["111", "222"])
            a = bot_profiles.read(str(settings), "111")
            b = bot_profiles.read(str(settings), "222")
            assert a["qq_user_openids"] == b["qq_user_openids"] == legacy["qq_user_openids"]
            assert a["scheduled_message"]["target_groups"] == ["g1"]
            assert b["scheduled_message"]["target_groups"] == ["g2"]
            a["qq_user_openids"][0]["enabled"] = False
            a["translation"]["enabled"] = True
            a["qq_c2c_relay_filter"]["filter_message_types"] = []
            cross_group = [{"id": "cross", "enabled": True, "groups": ["g1", "g2"]}]
            with bot_profiles.scope("111"):
                bot_profiles.save(str(settings), a)
                assert not config.is_user_authorized("user")
                assert config.get_translation_config()["enabled"] is True
                assert scheduled_message._message_target_groups({"target_mode": "all"}) == ["g1"]
                with patch.object(scheduled_message, "get_qq_forwarding_groups", return_value=cross_group):
                    assert scheduled_message._message_target_groups({"target_mode": "forward_group", "forward_groups": ["cross"]}) == ["g1"]
            with bot_profiles.scope("222"):
                assert config.is_user_authorized("user")
                assert config.get_translation_config()["enabled"] is False
                assert scheduled_message._message_target_groups({"target_mode": "all"}) == ["g2"]
                with patch.object(scheduled_message, "get_qq_forwarding_groups", return_value=cross_group):
                    assert scheduled_message._message_target_groups({"target_mode": "forward_group", "forward_groups": ["cross"]}) == ["g2"]
            assert not config.is_user_authorized("user")
            bot_profiles.migrate(str(settings), ["111", "222", "333"])
            assert bot_profiles.read(str(settings), "333")["qq_user_openids"] == []

        # Upgrade an already split installation whose users and card settings
        # are still global, then verify retries never overwrite later changes.
        old_root = root / "old-split"
        old_root.mkdir()
        old_settings = old_root / "settings.json"
        old = SettingsModel.default().to_dict()
        old["bot_profile_ids"] = ["111", "222"]
        old["qq_group_openids"] = legacy["qq_group_openids"]
        old["qq_user_openids"] = legacy["qq_user_openids"]
        old["scheduled_message"]["target_groups"] = ["g1", "g2"]
        old_settings.write_text(json.dumps(old), encoding="utf-8")
        for owner in old["bot_profile_ids"]:
            profile = {"format": "wrelay-bot-profile", "version": 1, "bot_id": owner, "config": {}}
            path = Path(bot_profiles.profile_path(str(old_settings), owner))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(profile), encoding="utf-8")
        bot_profiles.migrate(str(old_settings), ["111", "222"])
        first = bot_profiles.read(str(old_settings), "111")
        second = bot_profiles.read(str(old_settings), "222")
        assert first["scheduled_message"]["target_groups"] == ["g1"]
        assert second["scheduled_message"]["target_groups"] == ["g2"]
        assert first["qq_user_openids"] == second["qq_user_openids"] == old["qq_user_openids"]
        first["qq_user_openids"][0]["enabled"] = False
        with bot_profiles.scope("111"):
            bot_profiles.save(str(old_settings), first)
        bot_profiles.migrate(str(old_settings), ["111", "222"])
        assert bot_profiles.read(str(old_settings), "111")["qq_user_openids"][0]["enabled"] is False
        assert bot_profiles.read(str(old_settings), "222")["qq_user_openids"][0]["enabled"] is True

        with patch.object(message_trace, "TRACE_FILE", str(root / "trace.json")):
            with bot_profiles.scope("111"):
                await message_trace.record_delivery("x_rss:source", "42", [], [], ["g1"], {"g1": True})
            with bot_profiles.scope("222"):
                await message_trace.record_delivery("x_rss:source", "42", [], [], ["g2"], {"g2": False})
            one = message_trace.get_delivery_traces(50, "111")
            two = message_trace.get_delivery_traces(50, "222")
            assert len(one) == len(two) == 1
            assert one[0]["result"] == "success" and two[0]["result"] == "failed"
            assert one[0]["groups"][0]["id"] == "g1" and two[0]["groups"][0]["id"] == "g2"

        with patch.object(stats, "STATS_FILE", str(root / "stats.json")), patch.object(stats, "_stats", None):
            with bot_profiles.scope("111"):
                await stats.record(True)
            with bot_profiles.scope("222"):
                await stats.record(False)
            saved = json.loads((root / "stats.json").read_text(encoding="utf-8"))
            assert saved["bot_stats"]["111"]["total_forwarded"] == 1
            assert saved["bot_stats"]["222"]["total_failed"] == 1
    print("PASS: account migration, permissions, schedule targets, traces and counters")


if __name__ == "__main__":
    asyncio.run(main())
