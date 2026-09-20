"""验证 RSSHub 不可用时回退到 X 公开时间线解析。

不访问真实网络；用法：.venv/Scripts/python.exe scripts/test_x_rss_syndication_fallback.py
"""
import asyncio
import json
import sys
from unittest import mock

import nonebot

nonebot.init()

from plugins import x_rss as x_rss_mod
from plugins.x_rss_parser import parse_feed


class Response:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content
        self.headers = {}


def syndication_html():
    payload = {
        "props": {"pageProps": {"timeline": {"entries": [{
            "type": "tweet",
            "content": {"tweet": {
                "id_str": "1234567890123456789",
                "created_at": "Wed Sep 16 09:00:00 +0000 2026",
                "full_text": "hello from X",
                "user": {"name": "Example", "screen_name": "example"},
                "entities": {"hashtags": [{"text": "news"}]},
                "extended_entities": {"media": [{
                    "type": "photo",
                    "media_url_https": "https://pbs.twimg.com/media/demo.jpg",
                }]},
            }},
        }]}}},
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script></html>"
    ).encode()


async def main():
    client = mock.Mock()
    client.get = mock.AsyncMock(side_effect=[
        Response(502), Response(502), Response(502), Response(200, syndication_html()),
    ])
    with mock.patch.object(x_rss_mod, "_get_feed_client", return_value=client), \
            mock.patch.object(x_rss_mod.asyncio, "sleep", new=mock.AsyncMock()):
        body, etag, err = await x_rss_mod._fetch_feed(
            "http://localhost:1200/twitter/user/example"
        )
    assert err is None and etag is None and client.get.await_count == 4
    posts = parse_feed(body)
    assert len(posts) == 1
    assert posts[0]["id"] == "1234567890123456789"
    assert posts[0]["media"][0]["kind"] == "图片"
    print("PASS - RSSHub 失败时回退 X 公开时间线并保留媒体字段")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
