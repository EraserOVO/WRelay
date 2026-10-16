"""验证 RSSHub 冷启动宽限期：启动初期连接被拒记为 pending 而非失败。

覆盖：
- 宽限期内连接异常 → pending（不计失败轮次、不推进游标）
- 宽限期结束后连接异常 → fetch_failed（真实失败照常报告）
- 宽限期内 HTTP 错误（如 503）不算 pending，仍是 fetch_failed

不访问真实 RSSHub；用法：.venv/Scripts/python.exe scripts/test_rsshub_cold_start.py
"""
import asyncio
import sys
import time
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili as bilibili_mod
from plugins import x_rss as x_rss_mod


class Response:
    def __init__(self, status_code, content=b"", etag=None):
        self.status_code = status_code
        self.content = content
        self.headers = {} if etag is None else {"etag": etag}


def cold_start(module):
    """把进程启动时间重置为"刚刚启动"，进入冷启动宽限期。"""
    return mock.patch.object(module, "_PROCESS_STARTED_AT", time.time())


def warm(module):
    """把进程启动时间前移，越过宽限期。"""
    return mock.patch.object(
        module, "_PROCESS_STARTED_AT",
        time.time() - module.RSSHUB_COLD_START_GRACE - 10,
    )


async def check_x_rss():
    client = mock.Mock()
    client.get = mock.AsyncMock(side_effect=OSError("All connection attempts failed"))

    # 冷启动期内：连接异常 + 回退也失败 → pending
    with cold_start(x_rss_mod), \
            mock.patch.object(x_rss_mod, "_get_feed_client", return_value=client), \
            mock.patch.object(x_rss_mod.asyncio, "sleep", new=mock.AsyncMock()):
        body, etag, err = await x_rss_mod._fetch_feed(
            "http://127.0.0.1:18120/twitter/user/example")
    assert body is None and etag is None
    assert "RSSHub 可能仍在启动" in err, err

    source = {"key": "example", "name": "example",
              "url": "http://127.0.0.1:18120/twitter/user/example"}
    with cold_start(x_rss_mod), \
            mock.patch.object(x_rss_mod, "_get_feed_client", return_value=client), \
            mock.patch.object(x_rss_mod.asyncio, "sleep", new=mock.AsyncMock()), \
            mock.patch.object(x_rss_mod, "_load_state_async",
                              new=mock.AsyncMock(return_value={"sources": {}})), \
            mock.patch.object(x_rss_mod, "_update_source_state_async",
                              new=mock.AsyncMock()):
        result = await x_rss_mod._poll_source(source)
    assert result["result"] == "pending", result

    # 宽限期外：同样的连接异常是真实失败
    with warm(x_rss_mod), \
            mock.patch.object(x_rss_mod, "_get_feed_client", return_value=client), \
            mock.patch.object(x_rss_mod.asyncio, "sleep", new=mock.AsyncMock()), \
            mock.patch.object(x_rss_mod, "_load_state_async",
                              new=mock.AsyncMock(return_value={"sources": {}})), \
            mock.patch.object(x_rss_mod, "_update_source_state_async",
                              new=mock.AsyncMock()) as update_state:
        result = await x_rss_mod._poll_source(source)
    assert result["result"] == "fetch_failed", result
    # 失败时绝不推进游标状态（除了 URL/ETag 记录）
    for call in update_state.await_args_list:
        assert "etag" not in call.kwargs or call.kwargs.get("clear_etag"), call

    # 冷启动期内 HTTP 503 仍算真实失败（RSSHub 已就绪但上游异常）
    client_503 = mock.Mock()
    client_503.get = mock.AsyncMock(return_value=Response(503))
    with cold_start(x_rss_mod), \
            mock.patch.object(x_rss_mod, "_get_feed_client", return_value=client_503), \
            mock.patch.object(x_rss_mod.asyncio, "sleep", new=mock.AsyncMock()), \
            mock.patch.object(x_rss_mod, "_load_state_async",
                              new=mock.AsyncMock(return_value={"sources": {}})), \
            mock.patch.object(x_rss_mod, "_update_source_state_async",
                              new=mock.AsyncMock()):
        result = await x_rss_mod._poll_source(source)
    assert result["result"] == "fetch_failed", result

    print("PASS - X RSS 冷启动宽限：连接异常 pending，宽限外与 HTTP 错误仍为失败")


async def check_bilibili():
    source = {"id": "tag:wuppo", "name": "wuppo",
              "url": "http://127.0.0.1:18120/bilibili/vsearch/wuppo/pubdate/0"}
    feed = {"type": "视频", "url": source["url"], "state_key": source["id"]}

    client = mock.Mock()
    client.get = mock.AsyncMock(side_effect=OSError("All connection attempts failed"))

    with cold_start(bilibili_mod), \
            mock.patch.object(bilibili_mod, "_get_feed_client", return_value=client), \
            mock.patch.object(bilibili_mod.asyncio, "sleep", new=mock.AsyncMock()), \
            mock.patch.object(bilibili_mod, "_update_source_state_async",
                              new=mock.AsyncMock()):
        posts, error = await bilibili_mod._poll_feed_posts(
            source, source["id"], feed, "视频", source["id"])
    assert posts is None and error is not None
    assert error[0] == "pending", error

    with warm(bilibili_mod), \
            mock.patch.object(bilibili_mod, "_get_feed_client", return_value=client), \
            mock.patch.object(bilibili_mod.asyncio, "sleep", new=mock.AsyncMock()), \
            mock.patch.object(bilibili_mod, "_update_source_state_async",
                              new=mock.AsyncMock()):
        posts, error = await bilibili_mod._poll_feed_posts(
            source, source["id"], feed, "视频", source["id"])
    assert posts is None and error is not None
    assert error[0] == "fetch_failed", error

    print("PASS - Bilibili 冷启动宽限：连接异常 pending，宽限期外为失败")


async def main():
    await check_x_rss()
    await check_bilibili()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
