"""验证 Bilibili 积压队列落盘不会阻塞 asyncio 事件循环。"""
import asyncio
import copy
import threading
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili


async def main():
    write_started = threading.Event()
    release_write = threading.Event()
    written_states = []
    previous_error = bilibili._protection_load_error
    bilibili._protection_load_error = ""

    def blocking_write(*args, **kwargs):
        written_states.append(copy.deepcopy(args[1]))
        write_started.set()
        if not release_write.wait(1):
            raise AssertionError("测试未及时释放模拟落盘")

    state = {"version": 1, "queues": {}, "sent_at": []}
    task = None
    try:
        with mock.patch.object(bilibili, "atomic_write_json", side_effect=blocking_write) as save:
            task = asyncio.create_task(bilibili._save_protection_state_async(state))
            started = await asyncio.wait_for(
                asyncio.to_thread(write_started.wait, 0.5),
                timeout=0.75,
            )
            assert started

            loop_tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(loop_tick.set)
            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
            release_write.set()
            assert await task is True
            assert save.call_count == 1
            assert save.call_args.args == (bilibili.BILIBILI_PROTECTION_FILE, state)
            assert save.call_args.kwargs == {"indent": 2}

        write_started.clear()
        release_write.clear()
        written_states.clear()
        protected_state = {"version": 1, "queues": {}, "sent_at": []}
        lock_acquired = asyncio.Event()

        async def save_while_holding_protection_lock():
            async with bilibili._protection_lock:
                return await bilibili._save_protection_state_async(protected_state)

        async def wait_for_protection_lock():
            async with bilibili._protection_lock:
                lock_acquired.set()

        lock_waiter = None
        try:
            with mock.patch.object(
                bilibili,
                "atomic_write_json",
                side_effect=blocking_write,
            ):
                task = asyncio.create_task(save_while_holding_protection_lock())
                started = await asyncio.wait_for(
                    asyncio.to_thread(write_started.wait, 0.5),
                    timeout=0.75,
                )
                assert started
                protected_state["queues"]["source"] = {
                    "items": [{"id": "mutated-after-start"}]
                }
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done(), "重复取消提前释放了 Bilibili 队列写锁"
                assert bilibili._protection_lock.locked()

                lock_waiter = asyncio.create_task(wait_for_protection_lock())
                await asyncio.sleep(0)
                assert not lock_acquired.is_set(), "后续投递越过了仍在落盘的保护状态"

                release_write.set()
                result = await asyncio.gather(task, return_exceptions=True)
                assert isinstance(result[0], asyncio.CancelledError), result
                await lock_waiter
                assert lock_acquired.is_set()
                assert written_states == [
                    {"version": 1, "queues": {}, "sent_at": []}
                ]
        finally:
            release_write.set()
            for pending_task in (task, lock_waiter):
                if pending_task is not None and not pending_task.done():
                    pending_task.cancel()
                    await asyncio.gather(pending_task, return_exceptions=True)
        print("PASS - Bilibili 队列慢写不阻塞事件循环，独立快照在重复取消后仍先完成")
    finally:
        release_write.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        bilibili._protection_load_error = previous_error


if __name__ == "__main__":
    asyncio.run(main())
