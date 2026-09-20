"""验证 Discord 视频附件会作为 QQ 视频富媒体转发。

用法：.venv/Scripts/python.exe scripts/test_fetch_video.py
"""
import asyncio
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import fetch
from plugins.filter import MESSAGE_TYPE_FILE


async def main():
    message = {
        "channel_name": "clips",
        "username": "tester",
        "attachments": [{
            "url": "https://cdn.discordapp.com/attachments/clip.mp4",
            "filename": "clip.mp4",
        }],
    }

    with mock.patch.object(
        fetch,
        "fetch_bytes_many",
        return_value={message["attachments"][0]["url"]: b"video-bytes"},
    ):
        text_parts, media_items, has_content = await fetch.build_parts(
            message,
            enabled_types={MESSAGE_TYPE_FILE},
        )

    assert has_content is True
    assert len(media_items) == 1
    assert media_items[0]["kind"] == "视频"
    assert media_items[0]["segment"].type == "file_video"
    assert len(text_parts) == 1, text_parts
    print("PASS - Discord 视频附件以 QQ 视频富媒体发送")


if __name__ == "__main__":
    asyncio.run(main())
