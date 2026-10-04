"""验证 Discord 视频附件会作为 QQ 视频富媒体转发。

用法：.venv/Scripts/python.exe scripts/test_fetch_video.py
"""
import asyncio
import unittest.mock as mock
from types import SimpleNamespace

import nonebot

nonebot.init()

from plugins import fetch
from plugins import media
from plugins.filter import MESSAGE_TYPE_AUDIO, MESSAGE_TYPE_FILE, MESSAGE_TYPE_IMAGE


async def main():
    original_semaphore = media._semaphore
    fake_driver = SimpleNamespace(config=SimpleNamespace(
        media_concurrency=-1,
        api_timeout=float("inf"),
        media_max_bytes=0,
    ))
    try:
        media._semaphore = None
        with mock.patch.object(media, "get_driver", return_value=fake_driver):
            assert media._get_semaphore()._value == media.DEFAULT_MEDIA_CONCURRENCY
            assert media._get_timeout() == media.DEFAULT_MEDIA_TIMEOUT
            assert media._get_max_download_bytes() == media.DEFAULT_MAX_DOWNLOAD_BYTES

        fake_driver.config = SimpleNamespace(
            media_concurrency="2",
            api_timeout="45",
            media_max_bytes="1024",
        )
        media._semaphore = None
        with mock.patch.object(media, "get_driver", return_value=fake_driver):
            assert media._get_semaphore()._value == 2
            assert media._get_timeout() == 45
            assert media._get_max_download_bytes() == 1024
    finally:
        media._semaphore = original_semaphore
    print("PASS - 媒体下载配置无效值回退默认，正值正常读取")

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

    image_url = "https://cdn.discordapp.com/attachments/photo.png"
    audio_url = "https://cdn.discordapp.com/attachments/voice.mp3"
    video_url = "https://cdn.discordapp.com/attachments/clip.mp4"
    media_message = {
        "channel_name": "clips",
        "username": "tester",
        "attachments": [
            {"url": image_url, "filename": "photo.png"},
            {"url": audio_url, "filename": "voice.mp3"},
            {"url": video_url, "filename": "clip.mp4"},
        ],
    }
    with mock.patch.object(
        fetch,
        "fetch_bytes_many",
        side_effect=[
            {image_url: b"image-bytes"},
            {},
            {video_url: b"video-bytes"},
        ],
    ) as fetch_media:
        text_parts, media_items, has_content = await fetch.build_parts(
            media_message,
            enabled_types={MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_AUDIO, MESSAGE_TYPE_FILE},
        )

    assert has_content is True
    assert [item["kind"] for item in media_items] == ["图片", "视频"]
    assert [item["segment"].type for item in media_items] == [
        "file_image",
        "file_video",
    ]
    assert fetch_media.await_count == 3
    text_content = "".join(
        part.data.get("text", "")
        for part in text_parts
        if getattr(part, "type", None) == "text"
    )
    assert audio_url in text_content, text_content
    print("PASS - 图片、音频、视频共用下载渲染且失败时保留链接")

    file_url = "https://cdn.discordapp.com/attachments/notes.txt"
    file_message = {
        "channel_name": "clips",
        "username": "tester",
        "attachments": [
            {"url": image_url, "filename": "photo.png"},
            {"url": audio_url, "filename": "voice.mp3"},
            {"url": video_url, "filename": "clip.mp4"},
            {"url": file_url, "filename": "notes.txt"},
        ],
    }
    with mock.patch.object(
        fetch,
        "fetch_bytes_many",
        return_value={video_url: b"video-bytes"},
    ) as fetch_media:
        text_parts, media_items, has_content = await fetch.build_parts(
            file_message,
            enabled_types={MESSAGE_TYPE_FILE},
        )

    text_content = "".join(
        part.data.get("text", "")
        for part in text_parts
        if getattr(part, "type", None) == "text"
    )
    assert has_content is True
    assert file_url in text_content
    assert len(media_items) == 1 and media_items[0]["kind"] == "视频"
    assert fetch_media.await_count == 1
    print("PASS - 文件筛选只渲染普通文件链接和视频附件")


if __name__ == "__main__":
    asyncio.run(main())
