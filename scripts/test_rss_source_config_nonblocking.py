"""验证 X 与 Bilibili 每轮来源配置读取不会阻塞事件循环。"""
import asyncio
from contextlib import ExitStack
import time
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili, x_rss


async def verify_nonblocking(module, getter_name):
    def slow_empty_sources():
        time.sleep(0.25)
        return [], {}

    with mock.patch.object(module, getter_name, side_effect=slow_empty_sources):
        task = asyncio.create_task(module._poll_all())
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        await task
    assert elapsed < 0.15, "{} 来源配置读取阻塞事件循环: {:.3f}s".format(
        module.__name__, elapsed
    )


async def verify_route_nonblocking(module):
    getter_name = (
        "get_x_groups_for_source"
        if module is x_rss
        else "get_bilibili_groups_for_source"
    )

    def slow_empty_route(_source_id):
        time.sleep(0.25)
        return []

    with mock.patch.object(module, getter_name, side_effect=slow_empty_route):
        task = asyncio.create_task(module._load_source_groups("source"))
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        result = await task
    assert result == []
    assert elapsed < 0.15, "{} 发送路由读取阻塞事件循环: {:.3f}s".format(
        module.__name__, elapsed
    )


async def verify_bilibili_dispatch_snapshot_nonblocking():
    def slow_snapshot():
        time.sleep(0.25)
        return [], {}

    empty_state = {"sent_at": [], "queues": {}}
    patchers = [
        mock.patch.object(bilibili, "get_bilibili_polling_snapshot", side_effect=slow_snapshot),
        mock.patch.object(bilibili, "get_bilibili_protection", return_value={}),
        mock.patch.object(bilibili, "normalize_protection_config", return_value={
            "min_send_interval": 0,
            "hourly_limit": 0,
        }),
        mock.patch.object(bilibili, "rate_wait_seconds", return_value=0),
        mock.patch.object(
            bilibili,
            "_load_protection_state_async",
            new=mock.AsyncMock(return_value=(empty_state, "")),
        ),
        mock.patch.object(bilibili, "_save_protection_state_async", new=mock.AsyncMock()),
    ]
    with ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        task = asyncio.create_task(bilibili._dispatch_one_locked())
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        result = await task
    assert result == (0, 0), result
    assert elapsed < 0.15, "Bilibili 队列调度配置读取阻塞事件循环: {:.3f}s".format(elapsed)


async def verify_bilibili_protection_config_nonblocking():
    expected = {"min_send_interval": 30, "hourly_limit": 60}

    def slow_protection_config():
        time.sleep(0.25)
        return expected

    with mock.patch.object(
        bilibili,
        "get_bilibili_protection",
        side_effect=slow_protection_config,
    ):
        task = asyncio.create_task(bilibili._load_protection_config())
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        result = await task
    assert result == expected
    assert elapsed < 0.15, "Bilibili 保护配置读取阻塞事件循环: {:.3f}s".format(elapsed)


async def main():
    await verify_nonblocking(x_rss, "get_x_rss_polling_snapshot")
    await verify_nonblocking(bilibili, "get_bilibili_polling_snapshot")
    await verify_route_nonblocking(x_rss)
    await verify_route_nonblocking(bilibili)
    await verify_bilibili_dispatch_snapshot_nonblocking()
    await verify_bilibili_protection_config_nonblocking()
    print("PASS - X/Bilibili 轮询快照和发送路由慢读取不阻塞事件循环")


if __name__ == "__main__":
    asyncio.run(main())
