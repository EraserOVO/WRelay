"""验证 X RSS 来源状态异步读取和更新不会阻塞事件循环。"""
import asyncio
import json
import tempfile
import threading
from pathlib import Path
from unittest import mock

import nonebot

nonebot.init()

from plugins import x_rss


def check_corrupt_state_is_preserved():
    old_state_file = x_rss.X_STATE_FILE
    with tempfile.TemporaryDirectory(prefix="wrelay_x_state_corruption_") as tmp:
        path = Path(tmp) / "x_rss.json"
        x_rss.X_STATE_FILE = str(path)
        try:
            for original in (
                b"{invalid",
                b"[]",
                b'{"sources": []}',
            ):
                path.write_bytes(original)
                x_rss._update_source_state(
                    "source-1",
                    etag='"fresh"',
                    source_url="https://rss.example/feed",
                )
                assert path.read_bytes() == original

            path.unlink()
            x_rss._update_source_state(
                "source-1",
                etag='"fresh"',
                source_url="https://rss.example/feed",
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            assert saved["sources"]["source-1"]["etag"] == '"fresh"'
            assert saved["sources"]["source-1"]["url"] == "https://rss.example/feed"
        finally:
            x_rss.X_STATE_FILE = old_state_file
    print("PASS - 损坏的 X RSS 缓存不会被状态更新覆盖，缺失文件仍可初始化")


async def check_offloaded(patch_target, invoke, result):
    operation_started = threading.Event()
    release_operation = threading.Event()

    def blocking_operation(*args, **kwargs):
        operation_started.set()
        if not release_operation.wait(1):
            raise AssertionError("测试未及时释放模拟状态 I/O")
        return result

    task = None
    try:
        with mock.patch.object(
            x_rss,
            patch_target,
            side_effect=blocking_operation,
        ):
            task = asyncio.create_task(invoke())
            started = await asyncio.wait_for(
                asyncio.to_thread(operation_started.wait, 0.5),
                timeout=0.75,
            )
            assert started

            loop_tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(loop_tick.set)
            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
            release_operation.set()
            assert await task == result
    finally:
        release_operation.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def main():
    check_corrupt_state_is_preserved()
    state = {"sources": {}}
    await check_offloaded(
        "_load_state",
        x_rss._load_state_async,
        state,
    )
    await check_offloaded(
        "_update_source_state",
        lambda: x_rss._update_source_state_async("source-1", etag="tag"),
        None,
    )

    operation_started = threading.Event()
    release_operation = threading.Event()
    requested_urls = []
    main_thread = threading.current_thread()

    def blocking_rsshub_base():
        assert threading.current_thread() is not main_thread
        operation_started.set()
        if not release_operation.wait(1):
            raise AssertionError("测试未及时释放模拟 RSSHub 配置读取")
        return "https://rss.example/base"

    class ErrorResponse:
        status_code = 503
        text = "unavailable"

    class FeedClient:
        async def get(self, url, **kwargs):
            requested_urls.append(url)
            return ErrorResponse()

    task = None
    try:
        with (
            mock.patch.object(
                x_rss,
                "get_x_rsshub_base",
                side_effect=blocking_rsshub_base,
            ),
            mock.patch.object(x_rss, "_get_feed_client", return_value=FeedClient()),
        ):
            task = asyncio.create_task(
                x_rss.fetch_x_post("https://x.com/example/status/123")
            )
            started = await asyncio.wait_for(
                asyncio.to_thread(operation_started.wait, 0.5),
                timeout=0.75,
            )
            assert started

            loop_tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(loop_tick.set)
            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
            release_operation.set()
            assert await task == (None, None, "HTTP 503")
            assert requested_urls == [
                "https://rss.example/base/twitter/tweet/example/status/123"
            ]
    finally:
        release_operation.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)

    print("PASS - X RSS 状态读写和单条抓取配置读取在线程池执行")


if __name__ == "__main__":
    asyncio.run(main())
