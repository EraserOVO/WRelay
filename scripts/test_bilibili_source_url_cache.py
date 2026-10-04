"""Verify Bilibili validators and parsed posts are bound to the feed URL."""

import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili


async def check_feed_parse_failure_is_scoped_to_post_type():
    source_id = "bili-multi"
    video_url = "https://rss.example.test/bilibili/video"
    column_url = "https://rss.example.test/bilibili/column"
    column_posts = [{"id": "column-post", "cursor_id": "101"}]
    state = {
        "sources": {
            source_id: {
                "active_post_types": ["视频", "专栏"],
            },
            "video-feed": {"url": video_url, "etag": "video-etag"},
            "column-feed": {
                "url": column_url,
                "etag": "column-etag",
                "feed_initialized": True,
                "baseline_cursor_id": "100",
            },
        },
    }
    bilibili._posts_cache.clear()
    requests = []
    sent = []

    async def fetch(url, etag):
        requests.append((url, etag))
        return (b"feed body", etag + "-next", None)

    def parse(_body, *, post_type):
        if post_type == "视频":
            raise ValueError("invalid video feed")
        return column_posts

    def update_state(
        key,
        etag=None,
        clear_etag=False,
        source_url=None,
        baseline_cursor_id=None,
        feed_initialized=None,
        active_post_types=None,
    ):
        source_state = state["sources"].setdefault(key, {})
        if clear_etag:
            source_state.pop("etag", None)
        elif etag:
            source_state["etag"] = etag
        if source_url is not None:
            source_state["url"] = source_url
        if baseline_cursor_id is not None:
            source_state["baseline_cursor_id"] = str(baseline_cursor_id)
        if feed_initialized is not None:
            source_state["feed_initialized"] = bool(feed_initialized)
        if active_post_types is not None:
            source_state["active_post_types"] = list(active_post_types)

    async def send(_source, posts):
        sent.extend(posts)
        return len(posts), len(posts), 0

    with (
        mock.patch.object(bilibili, "_load_state", return_value=state),
        mock.patch.object(bilibili, "_update_source_state", side_effect=update_state),
        mock.patch.object(bilibili, "_fetch_feed", side_effect=fetch),
        mock.patch.object(bilibili, "parse_feed", side_effect=parse),
        mock.patch.object(bilibili, "_send_posts", side_effect=send),
    ):
        result = await bilibili._poll_source({
            "id": source_id,
            "name": "Example",
            "feeds": [
                {"type": "视频", "url": video_url, "state_key": "video-feed"},
                {"type": "专栏", "url": column_url, "state_key": "column-feed"},
            ],
        })

    assert requests == [
        (video_url, "video-etag"),
        (column_url, "column-etag"),
    ]
    assert "etag" not in state["sources"]["video-feed"]
    assert state["sources"]["column-feed"]["etag"] == "column-etag-next"
    assert bilibili._posts_cache == {"column-feed": column_posts}
    assert sent == column_posts
    assert result == {
        "result": "parse_failed",
        "error": "视频：invalid video feed",
        "new": 1,
        "sent": 1,
        "failed": 0,
    }
    print("PASS - 单个投稿类型解析失败时清除其 ETag，其他类型仍缓存并投递")


async def main():
    source_id = "bili-user"
    old_url = "https://old.example.test/bilibili/user/video/bili-user"
    new_url = "https://new.example.test/bilibili/user/video/bili-user"
    old_posts = [{"id": "old-post", "cursor_id": "10"}]
    new_posts = [{"id": "new-post", "cursor_id": "11"}]
    state = {
        "sources": {
            source_id: {
                "url": old_url,
                "etag": "old-etag",
                "active_post_types": ["视频"],
                "baseline_cursor_id": "9",
            },
        },
    }
    bilibili._posts_cache.clear()
    bilibili._posts_cache[source_id] = old_posts
    requests = []
    sent = []

    async def fetch(url, etag):
        requests.append((url, etag))
        if etag:
            return None, etag, None
        return b"fresh feed", "new-etag", None

    def update_state(
        key,
        etag=None,
        clear_etag=False,
        source_url=None,
        baseline_cursor_id=None,
        feed_initialized=None,
        active_post_types=None,
    ):
        source = state["sources"].setdefault(key, {})
        if clear_etag:
            source.pop("etag", None)
        elif etag:
            source["etag"] = etag
        if source_url is not None:
            source["url"] = source_url
        if baseline_cursor_id is not None:
            source["baseline_cursor_id"] = str(baseline_cursor_id)
        if feed_initialized is not None:
            source["feed_initialized"] = bool(feed_initialized)
        if active_post_types is not None:
            source["active_post_types"] = list(active_post_types)

    async def send(_source, posts):
        sent.extend(posts)
        return 1, 1, 0

    with (
        mock.patch.object(bilibili, "_load_state", return_value=state),
        mock.patch.object(bilibili, "_update_source_state", side_effect=update_state),
        mock.patch.object(bilibili, "_fetch_feed", side_effect=fetch),
        mock.patch.object(bilibili, "parse_feed", return_value=new_posts),
        mock.patch.object(bilibili, "_send_posts", side_effect=send),
    ):
        result = await bilibili._poll_source({
            "id": source_id,
            "name": "Example",
            "feeds": [{
                "type": "视频",
                "url": new_url,
                "state_key": source_id,
            }],
        })

    assert requests == [(new_url, None)]
    assert state["sources"][source_id]["url"] == new_url
    assert state["sources"][source_id]["etag"] == "new-etag"
    assert state["sources"][source_id]["baseline_cursor_id"] == "9"
    assert bilibili._posts_cache[source_id] == new_posts
    assert sent == new_posts
    assert result["result"] == "ok"
    print("PASS - Bilibili URL 变更后清除旧 ETag 与文章缓存")
    await check_feed_parse_failure_is_scoped_to_post_type()


if __name__ == "__main__":
    asyncio.run(main())
