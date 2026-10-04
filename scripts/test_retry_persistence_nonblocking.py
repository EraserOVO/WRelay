"""验证 Discord 延迟重试队列异步持久化不阻塞事件循环。"""
import asyncio
import threading
from unittest import mock

import nonebot

nonebot.init()

from plugins import retry


async def test_recovery_read_does_not_block_event_loop():
    saved_pending = retry._pending
    saved_loop_auto = retry._LOOP_AUTO_START
    saved_load_error = retry._pending_load_error
    saved_last_error = retry._last_error
    retry._pending = {}
    retry._LOOP_AUTO_START = False
    retry._pending_load_error = "temporary read failure"
    retry._last_error = retry._pending_load_error

    read_started = threading.Event()
    release_read = threading.Event()

    def blocking_read():
        read_started.set()
        if not release_read.wait(1):
            raise AssertionError("测试未及时释放模拟重试队列读取")
        return {"entries": []}

    async def save_without_disk():
        return True

    task = None
    try:
        with mock.patch.object(retry, "_read_pending", side_effect=blocking_read), \
                mock.patch.object(retry, "_save_pending_async", side_effect=save_without_disk):
            task = asyncio.create_task(
                retry.schedule_retry_async(
                    "C1",
                    "100",
                    {"channel_id": "C1", "content": "body"},
                    ["G1"],
                )
            )
            started = await asyncio.wait_for(
                asyncio.to_thread(read_started.wait, 0.5),
                timeout=0.75,
            )
            assert started

            loop_tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(loop_tick.set)
            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
            release_read.set()
            assert await task is True

        assert retry._pending_load_error == ""
        assert retry._pending["C1"]["100"].groups == {"G1"}
        print("PASS - 重试队列读取恢复在线程池执行，事件循环保持响应")
    finally:
        release_read.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        retry._pending = saved_pending
        retry._LOOP_AUTO_START = saved_loop_auto
        retry._pending_load_error = saved_load_error
        retry._last_error = saved_last_error


async def main():
    saved_pending = retry._pending
    saved_loop_auto = retry._LOOP_AUTO_START
    saved_load_error = retry._pending_load_error
    saved_last_error = retry._last_error
    retry._pending = {}
    retry._LOOP_AUTO_START = False
    retry._pending_load_error = ""
    retry._last_error = ""

    write_started = threading.Event()
    release_write = threading.Event()
    written_snapshots = []

    def blocking_write(snapshot):
        written_snapshots.append(snapshot)
        write_started.set()
        if not release_write.wait(1):
            raise AssertionError("测试未及时释放模拟重试队列落盘")
        return True

    task = None
    repeated_cancel_task = None
    try:
        with mock.patch.object(retry, "_save_pending_snapshot", side_effect=blocking_write):
            task = asyncio.create_task(
                retry.schedule_retry_async(
                    "C1",
                    "101",
                    {"channel_id": "C1", "content": "body"},
                    ["G1"],
                )
            )
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

        assert retry._pending["C1"]["101"].groups == {"G1"}
        assert len(written_snapshots) == 1
        assert written_snapshots[0]["entries"][0]["message_id"] == "101"
        assert written_snapshots[0]["entries"][0]["groups"] == ["G1"]

        write_started.clear()
        release_write.clear()
        with mock.patch.object(retry, "_save_pending_snapshot", side_effect=blocking_write):
            repeated_cancel_task = asyncio.create_task(
                retry.schedule_retry_async(
                    "C1",
                    "102",
                    {"channel_id": "C1", "content": "second body"},
                    ["G2"],
                )
            )
            started = await asyncio.wait_for(
                asyncio.to_thread(write_started.wait, 0.5),
                timeout=0.75,
            )
            assert started
            repeated_cancel_task.cancel()
            await asyncio.sleep(0)
            repeated_cancel_task.cancel()
            await asyncio.sleep(0)
            assert not repeated_cancel_task.done(), "重复取消提前结束仍在落盘的重试快照"
            release_write.set()
            result = await asyncio.gather(
                repeated_cancel_task,
                return_exceptions=True,
            )
            assert isinstance(result[0], asyncio.CancelledError), result
        assert len(written_snapshots) == 2
        assert written_snapshots[1]["entries"][-1]["message_id"] == "102"
        print("PASS - 重试队列慢写不阻塞事件循环，重复取消会等待快照落盘")
    finally:
        release_write.set()
        for pending_task in (task, repeated_cancel_task):
            if pending_task is not None and not pending_task.done():
                await asyncio.gather(pending_task, return_exceptions=True)
        retry._pending = saved_pending
        retry._LOOP_AUTO_START = saved_loop_auto
        retry._pending_load_error = saved_load_error
        retry._last_error = saved_last_error

    await test_recovery_read_does_not_block_event_loop()


if __name__ == "__main__":
    asyncio.run(main())
