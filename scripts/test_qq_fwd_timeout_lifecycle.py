"""Verify QQ manual relay timeout cleanup starts once and stops cleanly."""

import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import qq_fwd


async def verify_sweep_rechecks_pending_under_member_lock():
    key = ("timeout-race-group", "timeout-race-member")
    previous_entry = qq_fwd.get_pending(*key)
    now = asyncio.get_running_loop().time()
    qq_fwd._pending[key] = {
        "created_ts": now - 60,
        "target": {"openid": "old-target", "name": "Old target"},
    }
    member_lock = qq_fwd._get_pending_lock(*key)
    await member_lock.acquire()
    collected = asyncio.Event()
    original_collect = qq_fwd.collect_expired

    def signal_collected(now_ts, timeout=None):
        expired = original_collect(now_ts, timeout=timeout)
        if key in expired:
            collected.set()
        return expired

    cleaner = None
    try:
        with (
            mock.patch.object(qq_fwd, "get_qq_fwd_select_timeout", return_value=10),
            mock.patch.object(qq_fwd, "collect_expired", side_effect=signal_collected),
        ):
            cleaner = asyncio.create_task(qq_fwd.expire_pendings(now))
            await asyncio.wait_for(collected.wait(), timeout=1)
            await asyncio.sleep(0)
            assert not cleaner.done(), "超时清扫没有等待同成员的 pending 锁"

            qq_fwd.set_pending(
                key[0],
                key[1],
                {"openid": "new-target", "name": "New target"},
            )
            refreshed_entry = qq_fwd.get_pending(*key)
            member_lock.release()
            await cleaner
            assert qq_fwd.get_pending(*key) is refreshed_entry
            assert refreshed_entry["target"]["openid"] == "new-target"
        print("PASS - 超时清扫在成员锁内复查，保留并发续设的 pending")
    finally:
        if member_lock.locked():
            member_lock.release()
        if cleaner is not None and not cleaner.done():
            cleaner.cancel()
            await asyncio.gather(cleaner, return_exceptions=True)
        if previous_entry is None:
            qq_fwd.clear_pending(*key)
        else:
            qq_fwd._pending[key] = previous_entry


async def main():
    await verify_sweep_rechecks_pending_under_member_lock()

    class TestQQBot:
        pass

    previous_task = qq_fwd._timeout_task
    previous_started = qq_fwd._timeout_loop_started
    previous_stopping = qq_fwd._timeout_stopping
    loop_started = asyncio.Event()
    cancellation_seen = asyncio.Event()
    release_cancelled_loop = asyncio.Event()

    async def wait_for_shutdown_race():
        loop_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await release_cancelled_loop.wait()
            raise

    qq_fwd._timeout_task = None
    qq_fwd._timeout_loop_started = False
    qq_fwd._timeout_stopping = False
    stopper = None
    try:
        with mock.patch.object(qq_fwd, "QQBot", TestQQBot), mock.patch.object(
            qq_fwd,
            "_timeout_loop",
            side_effect=wait_for_shutdown_race,
        ):
            bot = TestQQBot()
            await qq_fwd._start_timeout_loop(bot)
            first_task = qq_fwd._timeout_task
            assert first_task is not None and not first_task.done()
            assert qq_fwd._timeout_loop_started is True
            await asyncio.wait_for(loop_started.wait(), timeout=1)

            await qq_fwd._start_timeout_loop(bot)
            assert qq_fwd._timeout_task is first_task

            stopper = asyncio.create_task(qq_fwd._stop_timeout_loop())
            await asyncio.wait_for(cancellation_seen.wait(), timeout=1)
            assert qq_fwd._timeout_stopping is True
            assert qq_fwd._timeout_task is None
            assert qq_fwd._timeout_loop_started is False

            await qq_fwd._start_timeout_loop(bot)
            assert qq_fwd._timeout_task is None, "关闭期间重连重启了超时清扫任务"

            release_cancelled_loop.set()
            await stopper
            assert first_task.done()
            assert qq_fwd._timeout_task is None
        print("PASS - QQ 转发超时清扫任务去重正常，关闭期间重连不会重启")
    finally:
        release_cancelled_loop.set()
        if stopper is not None and not stopper.done():
            stopper.cancel()
            await asyncio.gather(stopper, return_exceptions=True)
        task = qq_fwd._timeout_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        qq_fwd._timeout_task = previous_task
        qq_fwd._timeout_loop_started = previous_started
        qq_fwd._timeout_stopping = previous_stopping


if __name__ == "__main__":
    asyncio.run(main())
