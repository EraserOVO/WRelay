"""验证管理 API 的 JSON 文件读取不会阻塞 bot 事件循环。"""
import asyncio
import threading
from types import SimpleNamespace
from unittest import mock

import nonebot

nonebot.init()

from plugins import backfill_api, health_api


async def check_offloaded(target, attribute, invoke, read_result, response):
    read_started = threading.Event()
    release_read = threading.Event()

    def blocking_read(*args, **kwargs):
        read_started.set()
        if not release_read.wait(1):
            raise AssertionError("测试未及时释放模拟 API 文件读取")
        return read_result

    task = None
    try:
        with mock.patch.object(target, attribute, side_effect=blocking_read):
            task = asyncio.create_task(invoke())
            started = await asyncio.wait_for(
                asyncio.to_thread(read_started.wait, 0.5),
                timeout=0.75,
            )
            assert started

            loop_tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(loop_tick.set)
            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
            release_read.set()
            assert await task == response
    finally:
        release_read.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def main():
    token = "t" * 32
    request = SimpleNamespace(
        url=SimpleNamespace(path="/api/health"),
        headers={"x-wrelay-token": token},
    )

    async def call_next(_request):
        return {"ok": True}

    await check_offloaded(
        health_api,
        "_panel_token",
        lambda: health_api.bot_api_token_guard(request, call_next),
        token,
        {"ok": True},
    )
    await check_offloaded(
        health_api,
        "get_delivery_traces",
        lambda: health_api.api_message_trace(20),
        [{"message_id": "101"}],
        {"ok": True, "entries": [{"message_id": "101"}]},
    )
    await check_offloaded(
        backfill_api,
        "_read_audit",
        backfill_api.api_channels_audit,
        {"readable_total": 1},
        {"ok": True, "audit": {"readable_total": 1}},
    )
    print("PASS - 管理 API 文件读取在线程池执行，不阻塞事件循环")


if __name__ == "__main__":
    asyncio.run(main())
