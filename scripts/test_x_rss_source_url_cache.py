"""Verify X RSS validators and parsed posts are bound to the source URL."""

import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import x_rss


async def main():
    key = "example-user"
    old_url = "https://old.example.test/twitter/user/example-user"
    new_url = "https://new.example.test/twitter/user/example-user"
    old_posts = [{"id": "old-post"}]
    new_posts = [{"id": "new-post"}]
    state = {
        "sources": {
            key: {"etag": "old-etag", "url": old_url},
        },
    }
    x_rss._posts_cache.clear()
    x_rss._posts_cache[key] = old_posts
    requests = []
    sent = []

    async def fetch(url, etag):
        requests.append((url, etag))
        if etag:
            return None, etag, None
        return b"fresh feed", "new-etag", None

    def update_state(source_key, etag=None, clear_etag=False, source_url=None):
        source = state["sources"].setdefault(source_key, {})
        if clear_etag:
            source.pop("etag", None)
        elif etag:
            source["etag"] = etag
        if source_url is not None:
            source["url"] = source_url

    async def send(_source, posts):
        sent.extend(posts)
        return 1, 1, 0

    with (
        mock.patch.object(x_rss, "_load_state", return_value=state),
        mock.patch.object(x_rss, "_update_source_state", side_effect=update_state),
        mock.patch.object(x_rss, "_fetch_feed", side_effect=fetch),
        mock.patch.object(x_rss, "parse_feed", return_value=new_posts),
        mock.patch.object(x_rss, "_send_posts", side_effect=send),
    ):
        result = await x_rss._poll_source({
            "key": key,
            "url": new_url,
            "name": "Example",
        })

    assert requests == [(new_url, None)]
    assert state["sources"][key] == {"url": new_url, "etag": "new-etag"}
    assert x_rss._posts_cache[key] == new_posts
    assert sent == new_posts
    assert result["result"] == "ok"
    print("PASS - X RSS URL 变更后清除旧 ETag 与文章缓存")


if __name__ == "__main__":
    asyncio.run(main())
