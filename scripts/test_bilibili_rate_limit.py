"""验证 Bilibili 限速、积压裁剪和投稿关键词筛选规则。"""

from plugins.bilibili_rate_limit import (
    DEFAULT_PROTECTION,
    choose_overflow,
    normalize_protection_config,
    prune_sent_timestamps,
    rate_wait_seconds,
)
from plugins.filter import check_bilibili_video_filter


def main():
    items = [{"id": str(index)} for index in range(1, 6)]
    kept, dropped = choose_overflow(items, 2, "latest")
    assert [item["id"] for item in kept] == ["4", "5"]
    assert [item["id"] for item in dropped] == ["1", "2", "3"]

    kept, dropped = choose_overflow(items, 2, "oldest")
    assert [item["id"] for item in kept] == ["1", "2"]
    assert [item["id"] for item in dropped] == ["3", "4", "5"]

    config = normalize_protection_config({
        "min_send_interval": 30,
        "hourly_limit": 3,
        "backlog_limit": 5,
        "overflow_policy": "oldest",
    })
    assert config["overflow_policy"] == "oldest"
    assert normalize_protection_config({
        "min_send_interval": float("inf"),
        "hourly_limit": float("-inf"),
        "backlog_limit": float("inf"),
    }) == DEFAULT_PROTECTION
    assert rate_wait_seconds([950], 1000, 60, 10) == 10
    assert rate_wait_seconds([0, 100, 200], 1000, 0, 3) == 2600
    assert prune_sent_timestamps([0, 100, 950], 4000) == [950.0]

    post = {"title": "CS 攻略实况", "description": "", "author": "测试作者", "tags": ["CS"]}
    assert check_bilibili_video_filter({"filter_keywords": ["CS"]}, post)
    assert check_bilibili_video_filter({"filter_keywords": ["攻略"]}, post)
    assert not check_bilibili_video_filter({"filter_keywords": ["新闻"]}, post)
    assert not check_bilibili_video_filter({"filter_keywords": ["广告"]}, post)
    assert check_bilibili_video_filter({"filter_keywords": ["测试作者"]}, post)
    assert not check_bilibili_video_filter({"filter_keywords": ["其他作者"]}, post)
    print("PASS - Bilibili 限速、积压和投稿关键词筛选")


if __name__ == "__main__":
    main()
