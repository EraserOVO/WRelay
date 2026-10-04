"""验证 RSSHub 临时失败重试与非临时错误快速返回。

不访问真实 RSSHub；用法：.venv/Scripts/python.exe scripts/test_x_rss_fetch_retry.py
"""
import asyncio
import sys
from unittest import mock

import nonebot

nonebot.init()

from plugins import x_rss as x_rss_mod


class Response:
    def __init__(self, status_code, content=b"", etag=None):
        self.status_code = status_code
        self.content = content
        self.headers = {} if etag is None else {"etag": etag}


async def main():
    client = mock.Mock()
    client.get = mock.AsyncMock(side_effect=[
        Response(502),
        Response(429),
        Response(200, b"<rss/>", '"fresh"'),
    ])
    with mock.patch.object(x_rss_mod, "_get_feed_client", return_value=client), \
            mock.patch.object(x_rss_mod.asyncio, "sleep", new=mock.AsyncMock()) as sleep:
        body, etag, err = await x_rss_mod._fetch_feed("http://rsshub.test/feed", '"old"')
    assert body == b"<rss/>" and etag == '"fresh"' and err is None
    assert client.get.await_count == 3
    assert sleep.await_args_list == [mock.call(1), mock.call(3)]

    client = mock.Mock()
    client.get = mock.AsyncMock(return_value=Response(404))
    with mock.patch.object(x_rss_mod, "_get_feed_client", return_value=client), \
            mock.patch.object(x_rss_mod.asyncio, "sleep", new=mock.AsyncMock()) as sleep:
        body, etag, err = await x_rss_mod._fetch_feed("http://rsshub.test/missing")
    assert body is None and etag is None and err == "HTTP 404"
    assert client.get.await_count == 1
    assert sleep.await_count == 0
    print("PASS - RSSHub 临时失败退避重试，非临时 4xx 不重试")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
