"""Verify one media URL is fetched once per batch, preserving result order."""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import media


async def main():
    calls = []

    async def fake_fetch(url):
        calls.append(url)
        return ("bytes:" + url).encode("utf-8")

    with mock.patch.object(media, "fetch_bytes", side_effect=fake_fetch):
        result = await media.fetch_bytes_many([
            "https://cdn.example/a.png",
            "https://cdn.example/b.png",
            "https://cdn.example/a.png",
        ])

    assert calls == [
        "https://cdn.example/a.png",
        "https://cdn.example/b.png",
    ], calls
    assert list(result) == [
        "https://cdn.example/a.png",
        "https://cdn.example/b.png",
    ], result
    assert result == {
        "https://cdn.example/a.png": b"bytes:https://cdn.example/a.png",
        "https://cdn.example/b.png": b"bytes:https://cdn.example/b.png",
    }, result
    print("PASS - 媒体批次按 URL 去重并保留首次出现顺序")


if __name__ == "__main__":
    asyncio.run(main())
