"""Verify whitespace-only Discord bodies do not suppress Embed links."""

import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import fetch


async def main():
    embed_url = "https://example.test/post"
    message = {
        "channel_id": "channel-1",
        "channel_name": "announcements",
        "username": "tester",
        "content": " \n\t ",
        "embeds": [{"url": embed_url}],
        "attachments": [],
    }
    with mock.patch.object(fetch, "_resolve_reference", return_value=None), \
            mock.patch.object(fetch, "make_text", side_effect=lambda value: value):
        text_parts, media_items, has_content = await fetch.build_parts(message)

    assert not media_items
    assert has_content is True
    assert embed_url in "".join(text_parts)
    print("PASS - 空白正文不会抑制 Discord Embed 链接")

    first_image = "https://example.test/first.png"
    fallback_image = "https://example.test/missing.png"
    embeds = [
        {
            "title": "First",
            "url": "https://example.test/first",
            "image_url": first_image,
        },
        {
            "title": "Second",
            "url": "https://example.test/second",
            "image_url": fallback_image,
        },
    ]
    image_results = [
        {first_image: b"image-bytes"},
        {fallback_image: None},
    ]
    with mock.patch.object(fetch, "_resolve_reference", return_value=None), \
            mock.patch.object(fetch, "make_text", side_effect=lambda value: value), \
            mock.patch.object(
                fetch,
                "fetch_bytes_many",
                new_callable=mock.AsyncMock,
                side_effect=image_results,
            ), \
            mock.patch.object(fetch.QQMessageSegment, "file_image", return_value="image-segment"), \
            mock.patch.object(fetch, "make_media_link", side_effect=lambda kind, url: f"[{kind}] {url}"):
        text_parts, media_items, has_content = await fetch.build_parts({
            "channel_id": "channel-1",
            "channel_name": "announcements",
            "username": "tester",
            "content": "",
            "embeds": embeds,
            "attachments": [],
        })

    assert has_content is True
    rendered = "".join(text_parts)
    assert rendered.index("【First】") < rendered.index("first")
    assert rendered.index("first") < rendered.index("【Second】")
    assert rendered.index("【Second】") < rendered.index("second")
    assert rendered.endswith(f"[图片] {fallback_image}")
    assert media_items == [{
        "kind": "图片",
        "url": first_image,
        "segment": "image-segment",
    }]
    print("PASS - Embed 标题/链接顺序与图片下载回退保持一致")


if __name__ == "__main__":
    asyncio.run(main())
