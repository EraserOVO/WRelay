"""Verify RSS poll task references and runtime status follow task lifecycle."""
import asyncio
import copy
from contextlib import ExitStack
import sys
import threading
import time
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili as bilibili_mod
from plugins import config as config_mod
from plugins import x_rss as x_rss_mod


async def verify_lifecycle(module, interval_name):
    class TestQQBot:
        pass

    stopped = asyncio.Event()

    async def wait_until_cancelled():
        await stopped.wait()

    with mock.patch.object(module, "QQBot", TestQQBot), \
            mock.patch.object(module, "_poll_all", side_effect=wait_until_cancelled), \
            mock.patch.object(module, interval_name, return_value=30):
        bot = TestQQBot()
        await module._start_poll_loop(bot)
        first_task = module._poll_task
        assert first_task is not None and not first_task.done()
        assert module.runtime_status()["loop_started"] is True

        # A repeated connect event must not start a duplicate polling task.
        await module._start_poll_loop(bot)
        assert module._poll_task is first_task

        first_task.cancel()
        try:
            await first_task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        assert module._poll_task is None
        assert module._loop_started is False
        assert module.runtime_status()["loop_started"] is False

        # A later connect event can recover by starting a fresh task.
        stopped.clear()
        await module._start_poll_loop(bot)
        second_task = module._poll_task
        assert second_task is not None and second_task is not first_task
        assert module.runtime_status()["loop_started"] is True
        second_task.cancel()
        try:
            await second_task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        assert module._poll_task is None
        assert module.runtime_status()["loop_started"] is False


async def verify_interval_read_nonblocking(module, interval_name):
    def slow_interval():
        time.sleep(0.25)
        return 30

    async def no_poll():
        return None

    with mock.patch.object(module, "_poll_all", side_effect=no_poll), \
            mock.patch.object(module, interval_name, side_effect=slow_interval):
        task = asyncio.create_task(module._poll_loop())
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert elapsed < 0.15, "{} 的间隔配置读取阻塞事件循环: {:.3f}s".format(
            module.__name__, elapsed
        )


async def verify_interval_read_failure_keeps_worker_alive(module, interval_name):
    class TestQQBot:
        pass

    previous_stopping = module._poll_stopping
    previous_task = module._poll_task
    previous_started = module._loop_started
    previous_status_started = module._runtime_status["loop_started"]
    module._poll_stopping = False
    module._poll_task = None
    module._loop_started = False
    module._runtime_status["loop_started"] = False
    module._poll_wakeup.clear()
    interval_calls = 0
    interval_error_seen = threading.Event()
    poll_calls = 0

    def flaky_interval():
        nonlocal interval_calls
        interval_calls += 1
        if interval_calls <= 2:
            if interval_calls == 2:
                interval_error_seen.set()
            raise OSError("模拟临时配置读取失败")
        return 30

    async def no_poll():
        nonlocal poll_calls
        poll_calls += 1

    task = None
    try:
        with mock.patch.object(module, "QQBot", TestQQBot), \
                mock.patch.object(module, "get_bots", return_value={"qq": TestQQBot()}), \
                mock.patch.object(module, "_poll_all", side_effect=no_poll), \
                mock.patch.object(
                    module, interval_name, side_effect=flaky_interval
                ):
            await module._start_poll_loop(TestQQBot())
            task = module._poll_task
            assert task is not None
            seen = await asyncio.wait_for(
                asyncio.to_thread(interval_error_seen.wait, 1),
                timeout=1.25,
            )
            assert seen
            assert poll_calls == 1
            assert not task.done(), "间隔配置读取异常后 X RSS worker 退出"

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
            assert module._poll_task is None
            assert module._loop_started is False
        print("PASS - {} 间隔配置读取失败时使用默认值并保持 worker".format(
            module.__name__
        ))
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        module._poll_task = previous_task
        module._loop_started = previous_started
        module._runtime_status["loop_started"] = previous_status_started
        module._poll_stopping = previous_stopping


async def verify_connect_interval_read_nonblocking(module, interval_name):
    class TestQQBot:
        pass

    stopped = asyncio.Event()

    async def wait_until_cancelled():
        await stopped.wait()

    def slow_interval():
        time.sleep(0.25)
        return 30

    with mock.patch.object(module, "QQBot", TestQQBot), \
            mock.patch.object(module, "_poll_all", side_effect=wait_until_cancelled), \
            mock.patch.object(module, interval_name, side_effect=slow_interval):
        task = asyncio.create_task(module._start_poll_loop(TestQQBot()))
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        await task
        poll_task = module._poll_task
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0)
        assert module._poll_task is None
        assert module.runtime_status()["loop_started"] is False
        assert elapsed < 0.15, "{} 重连启动读取阻塞事件循环: {:.3f}s".format(
            module.__name__, elapsed
        )


async def verify_status_interval_read_nonblocking(module, interval_name):
    sources_name = (
        "get_x_rss_sources"
        if module is x_rss_mod
        else "get_bilibili_status_snapshot"
    )
    status_name = "api_x_rss_status" if module is x_rss_mod else "api_bilibili_status"

    def slow_interval():
        time.sleep(0.25)
        return 30

    source_config_result = (
        []
        if module is x_rss_mod
        else {"sources": [], "protection": {}, "forwarding_groups": []}
    )
    patchers = [
        mock.patch.object(module, "get_bots", return_value={}),
        mock.patch.object(module, sources_name, return_value=source_config_result),
        mock.patch.object(module, "runtime_status", return_value={"loop_started": False}),
        mock.patch.object(module, interval_name, side_effect=slow_interval),
    ]
    if module is bilibili_mod:
        patchers.extend([
            mock.patch.object(module, "_load_protection_state", return_value={"queues": {}}),
        ])

    with ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        task = asyncio.create_task(getattr(module, status_name)())
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        result = await task
    assert result["poll_interval"] == 30
    assert elapsed < 0.15, "{} 状态接口读取阻塞事件循环: {:.3f}s".format(
        module.__name__, elapsed
    )


def verify_bilibili_status_snapshot_single_read():
    settings = {
        "bilibili_rsshub_base": "https://rss.example/",
        "bilibili_sources": [{
            "id": "up:1",
            "kind": "up",
            "value": "1",
            "enabled": True,
        }],
        "bilibili_protection": {},
        "bilibili_forwarding_groups": [{
            "id": "group-1",
            "enabled": True,
            "sources": ["up:1"],
            "groups": ["G1"],
        }],
        "qq_group_openids": [{"openid": "G1", "enabled": True}],
    }
    with mock.patch.object(
        config_mod,
        "_load_settings",
        return_value=copy.deepcopy(settings),
    ) as load:
        snapshot = config_mod.get_bilibili_status_snapshot()
    load.assert_called_once_with()
    assert len(snapshot["sources"]) == 1
    assert snapshot["protection"]
    assert [group["id"] for group in snapshot["forwarding_groups"]] == ["group-1"]


async def verify_status_source_read_nonblocking(module):
    getter_name = (
        "get_x_rss_sources"
        if module is x_rss_mod
        else "get_bilibili_status_snapshot"
    )
    status_name = "api_x_rss_status" if module is x_rss_mod else "api_bilibili_status"

    def slow_config():
        time.sleep(0.25)
        if module is x_rss_mod:
            return []
        return {"sources": [], "protection": {}, "forwarding_groups": []}

    patchers = [
        mock.patch.object(module, "get_bots", return_value={}),
        mock.patch.object(module, getter_name, side_effect=slow_config),
        mock.patch.object(module, "runtime_status", return_value={"loop_started": False}),
        mock.patch.object(
            module,
            "get_x_rss_poll_interval" if module is x_rss_mod else "get_bilibili_poll_interval",
            return_value=30,
        ),
    ]
    if module is bilibili_mod:
        patchers.append(mock.patch.object(
            module,
            "_load_protection_state_async",
            new=mock.AsyncMock(return_value=({"queues": {}}, "")),
        ))
    with ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        task = asyncio.create_task(getattr(module, status_name)())
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        result = await task
    assert result["sources_total"] == 0
    assert elapsed < 0.15, "{} 状态来源配置读取阻塞事件循环: {:.3f}s".format(
        module.__name__, elapsed
    )


async def verify_concurrent_start_is_deduplicated(module, interval_name):
    class TestQQBot:
        pass

    interval_started = threading.Event()
    release_interval = threading.Event()
    interval_calls = 0

    def slow_interval():
        nonlocal interval_calls
        interval_calls += 1
        interval_started.set()
        if not release_interval.wait(1):
            raise AssertionError("测试未及时释放模拟轮询间隔配置读取")
        return 30

    async def wait_until_cancelled():
        await asyncio.Event().wait()

    module._poll_stopping = False
    with mock.patch.object(module, "QQBot", TestQQBot), \
            mock.patch.object(module, "_poll_all", side_effect=wait_until_cancelled), \
            mock.patch.object(module, interval_name, side_effect=slow_interval):
        first_start = asyncio.create_task(module._start_poll_loop(TestQQBot()))
        started = await asyncio.wait_for(
            asyncio.to_thread(interval_started.wait, 0.5),
            timeout=0.75,
        )
        assert started
        second_start = asyncio.create_task(module._start_poll_loop(TestQQBot()))
        await second_start
        assert interval_calls == 1
        assert module._poll_task is None

        release_interval.set()
        await first_start
        poll_task = module._poll_task
        assert poll_task is not None and not poll_task.done()
        poll_task.cancel()
        await asyncio.gather(poll_task, return_exceptions=True)
        await asyncio.sleep(0)
        assert module._poll_task is None
        assert module._loop_started is False
        assert module.runtime_status()["loop_started"] is False


async def verify_shutdown_stops_poll_before_client(module, interval_name):
    class TestQQBot:
        pass

    poll_started = asyncio.Event()

    async def wait_until_cancelled():
        poll_started.set()
        await asyncio.Event().wait()

    class FeedClient:
        closed = False

        async def aclose(self):
            self.closed = True

    module._poll_stopping = False
    client = FeedClient()
    bots = {}
    with mock.patch.object(module, "QQBot", TestQQBot), \
            mock.patch.object(module, "get_bots", return_value=bots), \
            mock.patch.object(module, "_poll_all", side_effect=wait_until_cancelled), \
            mock.patch.object(module, interval_name, return_value=30):
        module._feed_client = client
        bot = TestQQBot()
        bots["qq"] = bot
        await module._start_poll_loop(bot)
        poll_task = module._poll_task
        assert poll_task is not None
        await poll_started.wait()

        await module._close_feed_client()

        assert poll_task.done()
        assert module._poll_task is None
        assert module._loop_started is False
        assert module.runtime_status()["loop_started"] is False
        assert client.closed
        assert module._feed_client is None
        await module._start_poll_loop(bot)
        assert module._poll_task is None


async def verify_offline_pause_and_reconnect_wake(module, interval_name):
    class TestQQBot:
        pass

    bots = {}
    interval_read = threading.Event()
    poll_started = asyncio.Event()
    poll_calls = 0

    def interval():
        interval_read.set()
        return 60

    async def poll_once():
        nonlocal poll_calls
        poll_calls += 1
        poll_started.set()

    module._poll_stopping = False
    module._poll_wakeup.clear()


    with mock.patch.object(module, "QQBot", TestQQBot), \
            mock.patch.object(module, "get_bots", return_value=bots), \
            mock.patch.object(module, "_poll_all", side_effect=poll_once), \
            mock.patch.object(module, interval_name, side_effect=interval):
        bot = TestQQBot()
        await module._start_poll_loop(bot)
        read_started = await asyncio.wait_for(
            asyncio.to_thread(interval_read.wait, 0.5),
            timeout=0.75,
        )
        assert read_started
        await asyncio.sleep(0)
        assert poll_calls == 0

        bots["qq"] = bot
        await module._start_poll_loop(bot)
        await asyncio.wait_for(poll_started.wait(), timeout=0.75)
        assert poll_calls == 1

        poll_task = module._poll_task
        assert poll_task is not None
        poll_task.cancel()
        await asyncio.gather(poll_task, return_exceptions=True)
        await asyncio.sleep(0)
        assert module._poll_task is None
        assert module._loop_started is False
        assert module.runtime_status()["loop_started"] is False
    module._poll_wakeup.clear()


async def verify_x_single_post_request_shutdown():
    previous_client = x_rss_mod._feed_client
    previous_feed_stopping = x_rss_mod._feed_stopping
    previous_poll_stopping = x_rss_mod._poll_stopping
    previous_tasks = set(x_rss_mod._x_request_tasks)
    x_rss_mod._feed_client = None
    x_rss_mod._feed_stopping = False
    x_rss_mod._poll_stopping = False
    x_rss_mod._x_request_tasks.clear()
    request_started = asyncio.Event()
    request_cancelled = asyncio.Event()

    class FakeFeedClient:
        closed = False

        async def get(self, *_args, **_kwargs):
            request_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                request_cancelled.set()
                raise

        async def aclose(self):
            assert request_cancelled.is_set(), "X RSS 客户端在单条请求收尾前关闭"
            self.closed = True

    client = FakeFeedClient()
    x_rss_mod._feed_client = client
    caller = None
    try:
        with mock.patch.object(
            x_rss_mod,
            "get_x_rsshub_base",
            return_value="https://rss.example",
        ):
            caller = asyncio.create_task(
                x_rss_mod.fetch_x_post("https://x.com/example/status/123")
            )
            await asyncio.wait_for(request_started.wait(), timeout=1)
            assert caller in x_rss_mod._x_request_tasks

            await x_rss_mod._close_feed_client()

            result = await asyncio.gather(caller, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError), result
            assert request_cancelled.is_set()
            assert client.closed
            assert x_rss_mod._feed_client is None
            assert x_rss_mod._x_request_tasks == set()
            try:
                await x_rss_mod._x_get("https://rss.example/late")
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("关闭后仍允许发起 X RSS 请求")
        print("PASS - X 单条推文请求在关机时回收，关闭后拒绝新请求")
    finally:
        if caller is not None and not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        x_rss_mod._x_request_tasks.clear()
        x_rss_mod._x_request_tasks.update(previous_tasks)
        x_rss_mod._feed_client = previous_client
        x_rss_mod._feed_stopping = previous_feed_stopping
        x_rss_mod._poll_stopping = previous_poll_stopping


async def verify_bilibili_manual_post_request_shutdown():
    previous_client = bilibili_mod._feed_client
    previous_feed_stopping = bilibili_mod._feed_stopping
    previous_poll_stopping = bilibili_mod._poll_stopping
    previous_tasks = set(bilibili_mod._feed_request_tasks)
    previous_poll_task = bilibili_mod._poll_task
    previous_loop_started = bilibili_mod._loop_started
    previous_status_loop_started = bilibili_mod._runtime_status["loop_started"]
    bilibili_mod._feed_client = None
    bilibili_mod._feed_stopping = False
    bilibili_mod._poll_stopping = False
    bilibili_mod._feed_request_tasks.clear()
    request_started = asyncio.Event()
    request_cancelled = asyncio.Event()

    class FakeFeedClient:
        closed = False

        async def get(self, *_args, **_kwargs):
            request_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                request_cancelled.set()
                raise

        async def aclose(self):
            assert request_cancelled.is_set(), "Bilibili 客户端在手动抓取收尾前关闭"
            self.closed = True

    client = FakeFeedClient()
    bilibili_mod._feed_client = client
    caller = None
    try:
        with mock.patch.object(
            bilibili_mod,
            "parse_manual_post_url",
            return_value={"kind": "视频", "video_ref": {"bvid": "BV1test", "avid": "", "short_url": None, "page": 1}},
        ):
            caller = asyncio.create_task(
                bilibili_mod.fetch_bilibili_manual_post(
                    "https://www.bilibili.com/video/BV1test"
                )
            )
            await asyncio.wait_for(request_started.wait(), timeout=1)
            assert caller in bilibili_mod._feed_request_tasks

            await bilibili_mod._close_feed_client()

            result = await asyncio.gather(caller, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError), result
            assert request_cancelled.is_set()
            assert client.closed
            assert bilibili_mod._feed_client is None
            assert bilibili_mod._feed_request_tasks == set()
            try:
                await bilibili_mod._bilibili_get("https://api.bilibili.com/late")
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("关闭后仍允许发起 Bilibili RSS 请求")
        print("PASS - Bilibili 私聊单条投稿请求在关机时回收，关闭后拒绝新请求")
    finally:
        if caller is not None and not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        bilibili_mod._feed_request_tasks.clear()
        bilibili_mod._feed_request_tasks.update(previous_tasks)
        bilibili_mod._feed_client = previous_client
        bilibili_mod._feed_stopping = previous_feed_stopping
        bilibili_mod._poll_stopping = previous_poll_stopping
        bilibili_mod._poll_task = previous_poll_task
        bilibili_mod._loop_started = previous_loop_started
        bilibili_mod._runtime_status["loop_started"] = previous_status_loop_started


async def main():
    verify_bilibili_status_snapshot_single_read()
    await verify_lifecycle(x_rss_mod, "get_x_rss_poll_interval")
    await verify_lifecycle(bilibili_mod, "get_bilibili_poll_interval")
    await verify_interval_read_failure_keeps_worker_alive(
        x_rss_mod, "get_x_rss_poll_interval"
    )
    await verify_interval_read_failure_keeps_worker_alive(
        bilibili_mod, "get_bilibili_poll_interval"
    )
    await verify_interval_read_nonblocking(x_rss_mod, "get_x_rss_poll_interval")
    await verify_interval_read_nonblocking(bilibili_mod, "get_bilibili_poll_interval")
    await verify_connect_interval_read_nonblocking(x_rss_mod, "get_x_rss_poll_interval")
    await verify_connect_interval_read_nonblocking(bilibili_mod, "get_bilibili_poll_interval")
    await verify_status_interval_read_nonblocking(x_rss_mod, "get_x_rss_poll_interval")
    await verify_status_interval_read_nonblocking(bilibili_mod, "get_bilibili_poll_interval")
    await verify_status_source_read_nonblocking(x_rss_mod)
    await verify_status_source_read_nonblocking(bilibili_mod)
    await verify_concurrent_start_is_deduplicated(x_rss_mod, "get_x_rss_poll_interval")
    await verify_concurrent_start_is_deduplicated(
        bilibili_mod, "get_bilibili_poll_interval"
    )
    await verify_shutdown_stops_poll_before_client(
        x_rss_mod, "get_x_rss_poll_interval"
    )
    await verify_shutdown_stops_poll_before_client(
        bilibili_mod, "get_bilibili_poll_interval"
    )
    x_rss_mod._poll_stopping = False
    bilibili_mod._poll_stopping = False
    await verify_offline_pause_and_reconnect_wake(
        x_rss_mod, "get_x_rss_poll_interval"
    )
    await verify_offline_pause_and_reconnect_wake(
        bilibili_mod, "get_bilibili_poll_interval"
    )
    await verify_x_single_post_request_shutdown()
    await verify_bilibili_manual_post_request_shutdown()
    print("PASS - X/Bilibili RSS 启动去重、关闭收尾和重连状态均正常")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
