"""验证活跃 QQ 群列表对重复 OpenID 保序去重。"""

from unittest import mock

from plugins import config


def main():
    settings = {
        "qq_group_openids": [
            {"openid": "group-a", "enabled": False},
            {"openid": "group-a", "enabled": True},
            {"openid": "group-b", "enabled": True},
            {"openid": "group-a", "enabled": True},
            {"openid": "group-off", "enabled": False},
        ]
    }
    with mock.patch.object(config, "_load_settings", return_value=settings):
        assert config.get_active_groups() == ["group-a", "group-b"]

    with mock.patch.object(
        config,
        "_load_settings",
        return_value={"qq_group_openids": [{"openid": "group-off", "enabled": False}]},
    ):
        assert config.get_active_groups() == []

    relay_settings = {
        "qq_group_openids": [
            {"openid": "source", "enabled": False},
            {"openid": "source", "enabled": True},
            {"openid": "target", "enabled": True},
        ],
        "qq_forwarding_groups": [
            {"id": "relay", "enabled": True, "groups": ["source", "target"]}
        ],
    }
    with mock.patch.object(config, "_load_settings", return_value=relay_settings):
        assert config.get_related_group_openids("source") == {"target"}

    relay_settings["qq_group_openids"][1]["enabled"] = False
    with mock.patch.object(config, "_load_settings", return_value=relay_settings):
        assert config.get_related_group_openids("source") == set()
    print("PASS - 活跃群去重及重复源群资格")


if __name__ == "__main__":
    main()
