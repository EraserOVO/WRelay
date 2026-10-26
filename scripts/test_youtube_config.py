"""YouTube 来源配置归一化、转发组路由、筛选、翻译与校验测试。"""

import json
import tempfile
from pathlib import Path

import nonebot

nonebot.init()

from plugins import config as cfg
from plugins.filter import check_youtube_video_filter
from plugins.settings_model import (
    SettingsModel,
    _validate_youtube_sources_and_protection,
    validate_settings,
)


TMP = Path(tempfile.mkdtemp(prefix="wrelay_youtube_test_"))
CHANNEL_ID = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
SOURCE_ID = "channel:" + CHANNEL_ID


def load_case(data):
    path = TMP / "settings.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cfg.SETTINGS_FILE = str(path)
    cfg._settings_cache = None
    cfg._settings_cache_mtime = None
    cfg._settings_cache_size = None
    cfg._settings_cache_revision = None


def group(openid, enabled=True):
    return {"openid": openid, "enabled": enabled, "name": openid, "qq_id": ""}


def base_settings():
    return {
        "qq_group_openids": [group("G1"), group("G2"), group("G3", False)],
        "youtube_sources": [
            {
                "kind": "channel",
                "value": CHANNEL_ID,
                "name": "频道A",
                "enabled": True,
                "filter_keywords": ["AI"],
                "filter_message_types": ["链接", "文字"],
                "translation_enabled": True,
                "translation_append_original": False,
                "translation_show_datetime": True,
            },
            {
                "kind": "channel",
                "value": "@LinusTechTips",
                "enabled": False,
            },
            {
                "kind": "channel",
                "value": "not a channel!",
                "name": "无效来源",
                "enabled": True,
            },
        ],
        "youtube_forwarding_groups": [],
    }


def main():
    base = base_settings()
    load_case(base)
    sources = cfg.get_youtube_sources()
    assert [source["id"] for source in sources] == [
        SOURCE_ID,
        "channel:@LinusTechTips",
    ]
    assert sources[0]["kind"] == "channel"
    assert sources[0]["name"] == "频道A"
    assert sources[0]["url"] == cfg.channel_feed_url(CHANNEL_ID)
    assert sources[0]["filter_message_types"] == ["文字", "链接"]
    assert sources[1]["name"] == "@LinusTechTips"
    assert sources[1]["url"] == ""
    assert sources[1]["enabled"] is False
    assert cfg.get_youtube_poll_interval() == 300

    default_groups = cfg.get_youtube_forwarding_groups()
    assert default_groups == [{
        "id": "youtube_default",
        "name": "转发组1",
        "enabled": True,
        "sources": [SOURCE_ID],
        "groups": ["G1", "G2"],
    }]
    assert cfg.get_youtube_groups_for_source(SOURCE_ID) == ["G1", "G2"]
    assert cfg.get_youtube_groups_for_source("channel:@LinusTechTips") == []
    assert cfg.get_youtube_groups_for_source("channel:missing") == []

    delivery = cfg.get_youtube_source_delivery_config(SOURCE_ID)
    assert delivery["filter"] == {
        "filter_keywords": ["AI"],
        "filter_message_types": ["文字", "链接"],
    }
    assert delivery["translation"]["enabled"] is True
    assert delivery["translation"]["append_original"] is False
    assert delivery["translation"]["show_datetime"] is True
    disabled_delivery = cfg.get_youtube_source_delivery_config(
        "channel:@LinusTechTips"
    )
    assert disabled_delivery["translation"]["enabled"] is False
    assert disabled_delivery["translation"]["append_original"] is True
    assert disabled_delivery["translation"]["show_datetime"] is False
    assert disabled_delivery["filter"]["filter_message_types"] == [
        "文字",
        "图片",
        "链接",
    ]

    base["youtube_forwarding_groups"] = [
        {
            "id": "news",
            "name": "新闻组",
            "enabled": True,
            "sources": [SOURCE_ID, "channel:@LinusTechTips", "channel:missing"],
            "groups": ["G2", "G3", "UNKNOWN"],
        },
        {
            "id": "paused",
            "name": "暂停组",
            "enabled": False,
            "sources": [SOURCE_ID],
            "groups": ["G1"],
        },
        {
            "id": "news",
            "name": "重复组",
            "sources": [SOURCE_ID],
            "groups": ["G1"],
        },
    ]
    load_case(base)
    normalized = cfg.get_youtube_forwarding_groups()
    assert [item["id"] for item in normalized] == ["news", "paused"]
    assert normalized[0]["sources"] == [SOURCE_ID, "channel:@LinusTechTips"]
    assert normalized[0]["groups"] == ["G2", "G3"]
    assert cfg.get_youtube_groups_for_source(SOURCE_ID) == ["G2"]
    assert cfg.get_youtube_groups_for_source("channel:@LinusTechTips") == []
    status = cfg.get_youtube_status_snapshot()
    assert len(status["sources"]) == 2
    assert len(status["forwarding_groups"]) == 2
    assert status["protection"]["min_send_interval"] == 60
    assert status["protection"]["hourly_limit"] == 10
    assert status["protection"]["backlog_limit"] == 10

    base["youtube_poll_interval"] = 60
    base["youtube_protection"] = {
        "min_send_interval": 30,
        "hourly_limit": 5,
        "backlog_limit": 3,
        "overflow_policy": "oldest",
    }
    load_case(base)
    assert cfg.get_youtube_poll_interval() == 60
    assert cfg.get_youtube_protection() == {
        "min_send_interval": 30,
        "hourly_limit": 5,
        "backlog_limit": 3,
        "overflow_policy": "oldest",
    }
    for bad_interval in (0, 29, -1, "abc", None):
        base["youtube_poll_interval"] = bad_interval
        load_case(base)
        assert cfg.get_youtube_poll_interval() == 300

    valid = {
        "youtube_sources": [
            {
                "id": SOURCE_ID,
                "kind": "channel",
                "value": CHANNEL_ID,
                "enabled": True,
                "filter_message_types": ["文字", "图片", "链接"],
                "translation_enabled": True,
            }
        ],
        "youtube_protection": {
            "min_send_interval": 60,
            "hourly_limit": 10,
            "backlog_limit": 10,
            "overflow_policy": "latest",
        },
    }
    assert _validate_youtube_sources_and_protection(valid) is None
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": {}}
    ) == "youtube_sources 必须是列表"
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": [None]}
    ) == "youtube_sources 的每一项必须是对象"
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": [{"kind": "search", "value": "x"}]}
    ) == "youtube_sources.kind 只支持 channel"
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": [{"value": 1}]}
    ) == "youtube_sources.value 必须是字符串"
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": [{"value": "x", "enabled": "yes"}]}
    ) == "youtube_sources.enabled 必须是布尔值"
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": [{"value": "x", "filter_keywords": "AI"}]}
    ) == "youtube_sources.filter_keywords 必须是字符串列表"
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": [{"value": "x", "filter_message_types": ["视频"]}]}
    ) == "youtube_sources.filter_message_types 包含不支持的类型"
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": [{"value": "x", "translation_enabled": 1}]}
    ) == "youtube_sources.translation_enabled 必须是布尔值"
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": [], "youtube_protection": []}
    ) == "youtube_protection 必须是对象"
    for protection, expected in (
        (
            {"min_send_interval": 1.5, "hourly_limit": 1, "backlog_limit": 1},
            "youtube_protection.min_send_interval 必须是整数",
        ),
        (
            {"min_send_interval": 0, "hourly_limit": 0, "backlog_limit": 1},
            "youtube_protection.hourly_limit 范围必须是 1-10000",
        ),
        (
            {"min_send_interval": 0, "hourly_limit": 1, "backlog_limit": 1001},
            "youtube_protection.backlog_limit 范围必须是 1-1000",
        ),
        (
            {
                "min_send_interval": 0,
                "hourly_limit": 1,
                "backlog_limit": 1,
                "overflow_policy": "newest",
            },
            "youtube_protection.overflow_policy 只支持 latest 或 oldest",
        ),
    ):
        assert _validate_youtube_sources_and_protection(
            {"youtube_sources": [], "youtube_protection": protection}
        ) == expected

    # 兼容接入 YouTube 之前的配置：维护检查与备份校验读取的是未归一化的
    # settings.json，缺 youtube_sources 必须通过校验，读取层按空来源运行
    legacy = SettingsModel.default({}, [group("G1"), group("G2")]).to_dict()
    for key in [key for key in legacy if key.startswith("youtube")]:
        del legacy[key]
    assert validate_settings(legacy) is None
    load_case(legacy)
    assert cfg.get_youtube_sources() == []
    assert _validate_youtube_sources_and_protection(
        {"youtube_sources": None}
    ) == "youtube_sources 必须是列表"

    post = {
        "title": "AI 编程实战",
        "description": "本期介绍本地模型",
        "author": "测试频道",
    }
    assert check_youtube_video_filter({}, post)
    assert check_youtube_video_filter({"filter_keywords": ["ai"]}, post)
    assert check_youtube_video_filter({"filter_keywords": ["本地模型"]}, post)
    assert check_youtube_video_filter({"filter_keywords": ["测试频道"]}, post)
    assert not check_youtube_video_filter({"filter_keywords": ["游戏"]}, post)
    print("PASS - YouTube 来源、路由、筛选、翻译与配置校验")


if __name__ == "__main__":
    main()
