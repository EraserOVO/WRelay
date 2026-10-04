"""验证共享去重游标磁盘读取不会阻塞 asyncio 消息循环。"""
import asyncio
import threading
from unittest import mock

from plugins import history as history_mod
from plugins.history import load_last_messages_async


async def main():
    reader_started = threading.Event()
    release_reader = threading.Event()

    def blocking_reader():
        reader_started.set()
        if not release_reader.wait(1):
            raise AssertionError("测试未及时释放后台读取")
        return {"C1": {"G1": "100"}}

    task = asyncio.create_task(load_last_messages_async(blocking_reader))
    try:
        started = await asyncio.wait_for(
            asyncio.to_thread(reader_started.wait, 0.5),
            timeout=0.75,
        )
        assert started

        loop_tick = asyncio.Event()
        asyncio.get_running_loop().call_soon(loop_tick.set)
        await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
        release_reader.set()
        assert await task == {"C1": {"G1": "100"}}
        print("PASS - 去重游标磁盘读取在线程池执行，不阻塞事件循环")
    finally:
        release_reader.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)

    reader_started.clear()
    release_reader.clear()
    saved_snapshots = []

    def current_cursor():
        return {"C1": {"G1": "100"}}

    def save_snapshot(data):
        saved_snapshots.append({key: dict(value) for key, value in data.items()})

    read_task = write_task = None
    try:
        with mock.patch.object(history_mod, "load_last_messages", side_effect=current_cursor), \
                mock.patch.object(history_mod, "save_last_messages", side_effect=save_snapshot):
            read_task = asyncio.create_task(load_last_messages_async(blocking_reader))
            started = await asyncio.wait_for(
                asyncio.to_thread(reader_started.wait, 0.5),
                timeout=0.75,
            )
            assert started
            read_task.cancel()
            await asyncio.sleep(0)
            assert not read_task.done(), "取消中的读取应继续持有游标锁"

            write_task = asyncio.create_task(
                history_mod.update_last_messages(
                    lambda data: data.setdefault("C1", {}).update({"G2": "200"}) or True
                )
            )
            await asyncio.sleep(0)
            assert not write_task.done(), "游标写入越过了仍在读取的线程"

            release_reader.set()
            results = await asyncio.gather(read_task, write_task, return_exceptions=True)
            assert isinstance(results[0], asyncio.CancelledError), results
            assert results[1] is True, results
            assert saved_snapshots == [{"C1": {"G1": "100", "G2": "200"}}], saved_snapshots
        print("PASS - 取消中的游标读取完成前持锁，后续写入基于最新快照")
    finally:
        release_reader.set()
        for pending_task in (read_task, write_task):
            if pending_task is not None and not pending_task.done():
                await asyncio.gather(pending_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
