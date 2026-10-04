"""Verify Discord body text and custom emoji keep order and filter behavior."""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import fetch
from plugins.filter import MESSAGE_TYPE_EMOJI, MESSAGE_TYPE_TEXT


def text_content(parts):
    return "".join(
        part.data.get("text", "")
        for part in parts
        if getattr(part, "type", None) == "text"
    )


async def main():
    emoji_url = "https://cdn.discordapp.com/emojis/12345.png"
    message = {
        "channel_name": "body",
        "username": "tester",
        "content": "before <:spark:12345> after",
    }
    parsed_parts = [
        ("text", "before "),
        ("emoji", emoji_url),
        ("text", " after"),
    ]

    with mock.patch.object(
        fetch, "parse_discord_emoji_parts", return_value=parsed_parts
    ), mock.patch.object(
        fetch,
        "fetch_bytes_many",
        new=mock.AsyncMock(return_value={emoji_url: b"emoji-bytes"}),
    ) as download:
        text_parts, media_items, has_content = await fetch.build_parts(
            message,
            enabled_types={MESSAGE_TYPE_TEXT, MESSAGE_TYPE_EMOJI},
        )

    body_parts = text_parts[1:]
    assert [part.type for part in body_parts] == ["text", "file_image", "text"]
    assert body_parts[0].data["text"] == "\nbefore "
    assert body_parts[2].data["text"] == " after"
    assert media_items == []
    assert has_content is True
    download.assert_awaited_once_with([emoji_url])
    print("PASS - 正文文字与 Emoji 按原顺序生成 QQ 消息段")

    with mock.patch.object(
        fetch, "parse_discord_emoji_parts", return_value=parsed_parts
    ), mock.patch.object(
        fetch, "fetch_bytes_many", new=mock.AsyncMock()
    ) as download:
        text_parts, _media_items, has_content = await fetch.build_parts(
            message,
            enabled_types={MESSAGE_TYPE_TEXT},
        )
    assert all(part.type == "text" for part in text_parts)
    assert "before " in text_content(text_parts)
    assert " after" in text_content(text_parts)
    assert has_content is True
    download.assert_not_awaited()
    print("PASS - 关闭 Emoji 类型时保留正文且不发起表情下载")

    with mock.patch.object(
        fetch, "parse_discord_emoji_parts", return_value=parsed_parts
    ), mock.patch.object(
        fetch, "fetch_bytes_many", new=mock.AsyncMock(return_value={})
    ):
        text_parts, _media_items, has_content = await fetch.build_parts(
            message,
            enabled_types={MESSAGE_TYPE_EMOJI},
        )
    fallback_text = text_content(text_parts)
    assert emoji_url in fallback_text
    assert "表情" in fallback_text
    assert has_content is True
    print("PASS - Emoji 下载失败时回退为原表情链接")


if __name__ == "__main__":
    asyncio.run(main())
