"""验证 Discord 实时入口配置快照一次读取频道、群路由与筛选项。"""
import copy
from unittest import mock

from plugins import config


SETTINGS = {
    "discord_channels": [
        {
            "id": "C1",
            "name": "频道一",
            "enabled": True,
            "filter_usernames": ["ignored-user"],
            "filter_keywords": ["blocked"],
            "filter_message_types": ["text"],
        },
        {"id": "C2", "name": "停用频道", "enabled": False},
    ],
    "qq_group_openids": [
        {"openid": "G1", "enabled": True},
        {"openid": "G2", "enabled": False},
    ],
    "forwarding_groups": [
        {
            "id": "FG1",
            "name": "组一",
            "enabled": True,
            "channels": ["C1"],
            "groups": ["G1", "G2"],
        },
    ],
}


def test_single_load_and_accessor_equivalence():
    with mock.patch.object(
        config,
        "_load_settings",
        side_effect=lambda: copy.deepcopy(SETTINGS),
    ) as load_settings:
        route = config.get_discord_relay_config("C1")

    assert load_settings.call_count == 1, load_settings.call_count
    assert route == {
        "channel_name": "频道一",
        "groups": ["G1"],
        "filter": {
            "filter_usernames": ["ignored-user"],
            "filter_keywords": ["blocked"],
            "filter_message_types": ["text"],
        },
    }, route

    with mock.patch.object(
        config,
        "_load_settings",
        side_effect=lambda: copy.deepcopy(SETTINGS),
    ):
        separate = {
            "channel_name": config.get_active_channels()["C1"],
            "groups": config.get_groups_for_channel("C1"),
            "filter": config.get_channel_filter("C1"),
        }
    assert route == separate, (route, separate)

    with mock.patch.object(
        config,
        "_load_settings",
        side_effect=lambda: copy.deepcopy(SETTINGS),
    ) as load_settings:
        backfill = config.get_backfill_runtime_snapshot()
    assert load_settings.call_count == 1, load_settings.call_count
    assert backfill == {
        "channels": {"C1": "频道一"},
        "groups_by_channel": {"C1": ["G1"]},
        "enabled": True,
        "limit": 10,
    }, backfill


def test_inactive_channel_has_no_route():
    with mock.patch.object(
        config,
        "_load_settings",
        side_effect=lambda: copy.deepcopy(SETTINGS),
    ):
        assert config.get_discord_relay_config("C2") is None


if __name__ == "__main__":
    test_single_load_and_accessor_equivalence()
    test_inactive_channel_has_no_route()
    print("PASS - Discord 实时配置快照单次读取且与原 getter 一致")
