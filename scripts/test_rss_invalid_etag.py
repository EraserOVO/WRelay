"""Verify malformed persisted RSS ETags are ignored at request construction."""

import asyncio
from contextlib import ExitStack
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili, x_rss


class FakeResponse:
    status_code = 200
    content = b"feed body"
    headers = {"etag": '"fresh-etag"'}


class FakeClient:
    def __init__(self):
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


async def main():
    for module in (x_rss, bilibili):
        client = FakeClient()
        patches = [mock.patch.object(module, "_get_feed_client", return_value=client)]
        if module is bilibili:
            patches.extend((
                mock.patch.object(module, "FEED_REQUEST_GAP", 0),
                mock.patch.object(module, "_last_feed_request_ts", 0),
            ))
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            result = await module._fetch_feed("https://rss.example.test/feed", etag=123)

        assert result == (b"feed body", '"fresh-etag"', None)
        assert len(client.calls) == 1
        assert "If-None-Match" not in client.calls[0][1]["headers"]

    print("PASS - X/Bilibili RSS 忽略非字符串持久化 ETag")


if __name__ == "__main__":
    asyncio.run(main())
