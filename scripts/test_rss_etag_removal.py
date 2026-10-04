"""Verify a 200 response without an ETag clears the prior validator."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili, x_rss


async def check_x_etag_removal():
    key = "example-user"
    url = "https://rss.example.test/twitter/user/example-user"
    old_posts = [{"id": "100"}]
    new_posts = [{"id": "101"}]
    state = {"sources": {key: {"url": url, "etag": "old-etag"}}}
    x_rss._posts_cache.clear()
    x_rss._posts_cache[key] = old_posts

    async def fetch(_url, _etag):
        return b"fresh feed", None, None

    old_state_file = x_rss.X_STATE_FILE
    with tempfile.TemporaryDirectory(prefix="wrelay_x_etag_test_") as tmp:
        path = Path(tmp) / "x_rss.json"
        x_rss.X_STATE_FILE = str(path)
        path.write_text(json.dumps(state), encoding="utf-8")
        try:
            with (
                mock.patch.object(x_rss, "_fetch_feed", side_effect=fetch),
                mock.patch.object(x_rss, "parse_feed", return_value=new_posts),
                mock.patch.object(x_rss, "_send_posts", return_value=(1, 1, 0)),
            ):
                await x_rss._poll_source({"key": key, "url": url, "name": "Example"})

            saved = json.loads(path.read_text(encoding="utf-8"))
            assert "etag" not in saved["sources"][key]
            assert x_rss._posts_cache[key] == new_posts
        finally:
            x_rss.X_STATE_FILE = old_state_file


async def check_bilibili_etag_removal():
    source_id = "bili-user"
    url = "https://rss.example.test/bilibili/user/video/bili-user"
    new_posts = [{"id": "post-101", "cursor_id": "101"}]
    state = {
        "sources": {
            source_id: {
                "url": url,
                "etag": "old-etag",
                "active_post_types": ["视频"],
            },
        },
    }
    bilibili._posts_cache.clear()
    bilibili._posts_cache[source_id] = [{"id": "post-100", "cursor_id": "100"}]

    async def fetch(_url, _etag):
        return b"fresh feed", None, None

    with (
        mock.patch.object(bilibili, "_load_state", return_value=state),
        mock.patch.object(bilibili, "_save_state"),
        mock.patch.object(bilibili, "_fetch_feed", side_effect=fetch),
        mock.patch.object(bilibili, "parse_feed", return_value=new_posts),
        mock.patch.object(bilibili, "_send_posts", return_value=(1, 1, 0)),
    ):
        await bilibili._poll_source({
            "id": source_id,
            "name": "Example",
            "feeds": [{"type": "视频", "url": url, "state_key": source_id}],
        })

    assert "etag" not in state["sources"][source_id]
    assert bilibili._posts_cache[source_id] == new_posts


async def main():
    await check_x_etag_removal()
    await check_bilibili_etag_removal()
    print("PASS - 200 响应未返回 ETag 时清除旧验证器")


if __name__ == "__main__":
    asyncio.run(main())
