"""Verify RSS polling sources and target routes share one settings snapshot."""
import copy
from unittest import mock

from plugins import config


def check_x_snapshot():
    data = {
        "x_rsshub_base": "https://rss.example/base/",
        "x_rss_sources": [
            {"source": "alice", "enabled": True},
            {"source": "bob", "enabled": False},
        ],
        "x_rss_target_groups": [],
        "x_forwarding_groups": [
            {"id": "route-a", "sources": ["alice"], "groups": ["G1", "G2", "G3"]},
            {"id": "route-b", "sources": ["alice"], "groups": ["G2"]},
            {"id": "disabled", "enabled": False, "sources": ["alice"], "groups": ["G1"]},
        ],
        "qq_group_openids": [
            {"openid": "G2", "enabled": True},
            {"openid": "G1", "enabled": True},
            {"openid": "G3", "enabled": False},
        ],
    }
    with mock.patch.object(config, "_load_settings", return_value=copy.deepcopy(data)) as load:
        sources, routes = config.get_x_rss_polling_snapshot()
    load.assert_called_once_with()
    assert [source["url"] for source in sources] == [
        "https://rss.example/base/twitter/user/alice",
        "https://rss.example/base/twitter/user/bob",
    ]
    assert routes == {"alice": ["G2", "G1"]}, routes

    # Legacy configurations without explicit forwarding groups still use the
    # old target whitelist, without re-reading settings during normalization.
    legacy = copy.deepcopy(data)
    legacy.pop("x_forwarding_groups")
    legacy["x_rss_target_groups"] = ["G1"]
    with mock.patch.object(config, "_load_settings", return_value=legacy) as load:
        _, legacy_routes = config.get_x_rss_polling_snapshot()
    load.assert_called_once_with()
    assert legacy_routes == {"alice": ["G1"]}, legacy_routes


def check_bilibili_snapshot():
    data = {
        "bilibili_rsshub_base": "https://rss.example/base/",
        "bilibili_sources": [
            {"id": "up:1", "kind": "up", "value": "1", "enabled": True},
            {"id": "tag:off", "kind": "tag", "value": "off", "enabled": False},
        ],
        "bilibili_forwarding_groups": [
            {"id": "route-a", "sources": ["up:1"], "groups": ["G1", "G2", "G3"]},
            {"id": "disabled", "enabled": False, "sources": ["up:1"], "groups": ["G1"]},
        ],
        "qq_group_openids": [
            {"openid": "G2", "enabled": True},
            {"openid": "G1", "enabled": True},
            {"openid": "G3", "enabled": False},
        ],
    }
    with mock.patch.object(config, "_load_settings", return_value=copy.deepcopy(data)) as load:
        sources, routes = config.get_bilibili_polling_snapshot()
    load.assert_called_once_with()
    assert sources[0]["url"] == "https://rss.example/base/bilibili/user/video/1"
    assert routes == {"up:1": ["G2", "G1"]}, routes


def main():
    check_x_snapshot()
    check_bilibili_snapshot()
    print("PASS - X/Bilibili 来源和目标群路由由单份 settings 快照生成")


if __name__ == "__main__":
    main()
