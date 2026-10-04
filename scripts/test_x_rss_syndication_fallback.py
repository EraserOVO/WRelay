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


def syndication_html(media=None, entries=None):
    if media is None:
        media = [{
            "type": "photo",
            "media_url_https": "https://pbs.twimg.com/media/demo.jpg",
        }]
    default_entries = [{
            "type": "tweet",
            "content": {"tweet": {
                "id_str": "1234567890123456789",
                "created_at": "Wed Sep 16 09:00:00 +0000 2026",
                "full_text": "hello from X",
                "user": {"name": "Example", "screen_name": "example"},
                "entities": {"hashtags": [{"text": "news"}]},
                "extended_entities": {"media": media},
            }},
        }]
    payload = {
        "props": {"pageProps": {"timeline": {"entries": entries if entries is not None else default_entries}}},
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

    video_body = syndication_html([{
        "type": "video",
        "video_info": {"variants": [
            {
                "content_type": "video/mp4",
                "url": "https://video.example/bad-bitrate.mp4",
                "bitrate": float("inf"),
            },
            {
                "content_type": "video/mp4",
                "url": "https://video.example/high-bitrate.mp4",
                "bitrate": 320000,
            },
        ]},
    }])
    video_posts = parse_feed(video_body)
    assert video_posts[0]["media"][0]["url"] == "https://video.example/high-bitrate.mp4"

    malformed_media_body = syndication_html([
        {"type": "video", "video_info": "invalid"},
        {"type": "video", "video_info": {"variants": "invalid"}},
    ])
    malformed_media_posts = parse_feed(malformed_media_body)
    assert len(malformed_media_posts) == 1
    assert malformed_media_posts[0]["text"] == "hello from X"
    assert malformed_media_posts[0]["media"] == []

    mixed_entries_body = syndication_html(entries=[
        {"type": "tweet", "content": "invalid"},
        {"type": "tweet", "content": {"tweet": {
            "id_str": "9876543210987654321",
            "full_text": "valid after malformed",
            "user": {"screen_name": "example"},
        }}},
    ])
    mixed_entries_posts = parse_feed(mixed_entries_body)
    assert len(mixed_entries_posts) == 1
    assert mixed_entries_posts[0]["id"] == "9876543210987654321"
    assert mixed_entries_posts[0]["text"] == "valid after malformed"

    malformed_users_body = syndication_html(entries=[{
        "type": "tweet",
        "content": {"tweet": {
            "id_str": "1122334455667788990",
            "full_text": "original text",
            "user": "invalid",
            "retweeted_status": {
                "full_text": "retweeted text",
                "user": "invalid",
            },
            "quoted_status": {
                "full_text": "quoted text",
                "user": "invalid",
            },
        }},
    }])
    malformed_users_posts = parse_feed(malformed_users_body)
    assert len(malformed_users_posts) == 1
    malformed_user_post = malformed_users_posts[0]
    assert malformed_user_post["author"] == ""
    assert malformed_user_post["rt_author"] is None
    assert malformed_user_post["text"] == "retweeted text"
    assert malformed_user_post["quote"]["author"] == ""
    assert malformed_user_post["quote"]["text"] == "quoted text"
    print("PASS - RSSHub 失败时回退 X 公开时间线并保留媒体字段")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
