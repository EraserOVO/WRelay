"""验证 Bilibili 来源状态异步读取和更新不会阻塞事件循环。"""
import asyncio
import threading
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili


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
            bilibili,
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
    state = {"sources": {}}
    await check_offloaded(
        "_load_state",
        bilibili._load_state_async,
        state,
    )
    await check_offloaded(
        "_update_source_state",
        lambda: bilibili._update_source_state_async("source-1", etag="tag"),
        None,
    )
    print("PASS - Bilibili 来源状态读写在线程池执行，不阻塞事件循环")


if __name__ == "__main__":
    asyncio.run(main())
