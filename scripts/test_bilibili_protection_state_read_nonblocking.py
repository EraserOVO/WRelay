"""验证 Bilibili 保护队列首次读取不会阻塞 bot 事件循环。"""
import asyncio
import threading
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili


async def main():
    old_state = bilibili._protection_runtime_state
    old_error = bilibili._protection_load_error
    bilibili._protection_runtime_state = None
    bilibili._protection_load_error = ""
    started = threading.Event()
    release = threading.Event()

    def slow_read():
        started.set()
        if not release.wait(1):
            raise AssertionError("测试未及时释放模拟保护队列读取")
        return {"version": 1, "queues": {}, "sent_at": []}

    task = None
    try:
        with mock.patch.object(bilibili, "_read_protection_state", side_effect=slow_read):
            task = asyncio.create_task(bilibili._load_protection_state_async())
            began = await asyncio.wait_for(
                asyncio.to_thread(started.wait, 0.75),
                timeout=1,
            )
            assert began
            tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(tick.set)
            await asyncio.wait_for(tick.wait(), timeout=0.2)
            release.set()
            state, error = await task
            assert state == {"version": 1, "queues": {}, "sent_at": []}
            assert error == ""
            assert bilibili._protection_runtime_state is state
            assert bilibili._protection_load_error == ""
    finally:
        release.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        bilibili._protection_runtime_state = old_state
        bilibili._protection_load_error = old_error

    bilibili._protection_runtime_state = None
    bilibili._protection_load_error = ""
    with mock.patch.object(
        bilibili,
        "_read_protection_state",
        side_effect=[ValueError("模拟损坏"), {"version": 1, "queues": {}, "sent_at": []}],
    ):
        failed_state, failed_error = await bilibili._load_protection_state_async()
        recovered_state, recovered_error = await bilibili._load_protection_state_async()
    assert failed_state == {"version": 1, "queues": {}, "sent_at": []}
    assert "模拟损坏" in failed_error
    assert recovered_error == ""
    assert recovered_state is bilibili._protection_runtime_state
    assert bilibili._protection_load_error == ""
    bilibili._protection_runtime_state = old_state
    bilibili._protection_load_error = old_error

    print("PASS - Bilibili 保护队列慢读取不阻塞事件循环")


if __name__ == "__main__":
    asyncio.run(main())
