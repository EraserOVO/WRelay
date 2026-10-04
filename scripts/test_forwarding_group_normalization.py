"""Verify malformed membership fields fail closed without string iteration."""
from plugins import config
from plugins.settings_model import SettingsModel


def main():
    qq_groups = [{"openid": "group-1", "enabled": True}]

    discord_data = {
        "discord_channels": [{"id": "channel-1", "enabled": True}],
        "qq_group_openids": qq_groups,
        "forwarding_groups": [{
            "id": "discord-group",
            "channels": "channel-1",
            "groups": "group-1",
        }],
    }
    discord = config._ensure_forwarding_groups(discord_data)
    assert discord == [{
        "id": "discord-group",
        "name": "discord-group",
        "enabled": True,
        "channels": [],
        "groups": [],
    }]

    qq_data = {
        "qq_group_openids": qq_groups,
        "qq_forwarding_groups": [{"id": "qq-group", "groups": "group-1"}],
    }
    qq = config._ensure_qq_forwarding_groups(qq_data)
    assert qq[0]["groups"] == []

    x_data = {
        "qq_group_openids": qq_groups,
        "x_rss_sources": [{"source": "alice", "enabled": True}],
        "x_forwarding_groups": [{
            "id": "x-group",
            "sources": "alice",
            "groups": "group-1",
        }],
    }
    x_groups = config._ensure_x_forwarding_groups(x_data)
    assert x_groups[0]["sources"] == []
    assert x_groups[0]["groups"] == []

    bilibili_data = {
        "qq_group_openids": qq_groups,
        "bilibili_sources": [{
            "id": "up:1", "kind": "up", "value": "1", "enabled": True,
        }],
        "bilibili_forwarding_groups": [{
            "id": "bilibili-group",
            "sources": "up:1",
            "groups": "group-1",
        }],
    }
    bilibili = config._ensure_bilibili_forwarding_groups(bilibili_data)
    assert bilibili[0]["sources"] == []
    assert bilibili[0]["groups"] == []

    malformed_legacy = SettingsModel.from_data({
        "qq_group_openids": qq_groups,
        "discord_channels": [],
        "forwarding_groups": [{
            "id": "legacy-group",
            "channels": ["channel-1"],
            "groups": "group-1",
        }],
    })
    assert malformed_legacy["qq_forwarding_groups"] == []

    missing_legacy_groups = SettingsModel.from_data({
        "qq_group_openids": qq_groups,
        "discord_channels": [],
    })
    assert missing_legacy_groups["qq_forwarding_groups"][0]["groups"] == ["group-1"]

    print("PASS - 四类转发组的非列表成员字段按空列表安全归一化")


if __name__ == "__main__":
    main()
