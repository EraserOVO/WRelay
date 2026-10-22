"""Bilibili 来源 URL、筛选配置和转发组路由测试。"""

import json
import tempfile
from pathlib import Path

import nonebot

nonebot.init()

from plugins import config as cfg


TMP = Path(tempfile.mkdtemp(prefix="wrelay_bilibili_test_"))


def load_case(data):
    path = TMP / "settings.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cfg.SETTINGS_FILE = str(path)
    cfg._settings_cache = None
    cfg._settings_cache_mtime = None
    cfg._settings_cache_size = None


def group(openid, enabled=True):
    return {"openid": openid, "enabled": enabled, "name": openid, "qq_id": ""}


def source(kind, value, enabled=True, **extra):
    item = {"kind": kind, "value": value, "name": value, "enabled": enabled}
    item.update(extra)
    return item


def main():
    base = {
        "qq_group_openids": [group("G1"), group("G2"), group("G3", False)],
        "bilibili_sources": [
            source("up", "123456"),
            source("tag", "动作游戏"),
            source(
                "keyword",
                "独立游戏",
                filter_keywords=["精选关键词"],
                filter_tags=["精品"],
                filter_title_keywords=["实况"],
                filter_exclude_keywords=["广告"],
                filter_authors=["测试作者"],
            ),
        ],
        "bilibili_rsshub_base": "http://rsshub.test/",
        "bilibili_forwarding_groups": [],
    }
    load_case(base)
    sources = cfg.get_bilibili_sources()
    assert [item["id"] for item in sources] == ["up:123456", "tag:动作游戏", "keyword:独立游戏"]
    assert sources[0]["url"] == "http://rsshub.test/bilibili/user/video/123456"
    assert sources[1]["url"].endswith("/bilibili/vsearch/%E5%8A%A8%E4%BD%9C%E6%B8%B8%E6%88%8F/pubdate/0")
    assert cfg.get_bilibili_source_filter("tag:动作游戏") == {
        "filter_keywords": [],
        "filter_bilibili_post_types": ["视频"],
        "filter_message_types": ["文字", "图片", "链接", "视频"],
    }
    keyword_filter = cfg.get_bilibili_source_filter("keyword:独立游戏")
    assert keyword_filter == {
        "filter_keywords": ["精选关键词"],
        "filter_bilibili_post_types": ["视频"],
        "filter_message_types": ["文字", "图片", "链接", "视频"],
    }
    assert not any(key in keyword_filter for key in (
        "filter_tags",
        "filter_title_keywords",
        "filter_exclude_keywords",
        "filter_authors",
    ))

    default_groups = cfg.get_bilibili_forwarding_groups()
    assert default_groups == [{
        "id": "bilibili_default",
        "name": "转发组1",
        "enabled": True,
        "sources": ["keyword:独立游戏", "tag:动作游戏", "up:123456"],
        "groups": ["G1", "G2"],
    }]
    assert cfg.get_bilibili_groups_for_source("up:123456") == ["G1", "G2"]
    assert cfg.get_bilibili_groups_for_source("tag:动作游戏") == ["G1", "G2"]

    base["bilibili_forwarding_groups"] = [{
        "id": "only-tag",
        "name": "只发标签",
        "enabled": True,
        "sources": ["tag:动作游戏"],
        "groups": ["G2", "G3"],
    }]
    load_case(base)
    assert cfg.get_bilibili_groups_for_source("up:123456") == []
    assert cfg.get_bilibili_groups_for_source("tag:动作游戏") == ["G2"]
    print("PASS - Bilibili 来源 URL、筛选配置和转发组路由")


if __name__ == "__main__":
    main()
