"""Verify async backfill entry points offload settings reads."""
import asyncio
import time
from unittest import mock

import nonebot

nonebot.init()

from plugins import backfill, backfill_api


async def verify_nonblocking(call, module, getter_name, result, expected):
    def slow_getter():
        time.sleep(0.25)
        return result

    with mock.patch.object(module, getter_name, side_effect=slow_getter):
        task = asyncio.create_task(call())
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        response = await task
    assert response == expected, response
    assert elapsed < 0.15, "{} 配置读取阻塞事件循环: {:.3f}s".format(
        getter_name, elapsed
    )


async def main():
    empty_routing = {"channels": {}, "groups_by_channel": {}}
    await verify_nonblocking(
        backfill_api.api_backfill_pending,
        backfill_api,
        "get_backfill_routing_snapshot",
        empty_routing,
        {"ok": True, "total": 0, "channels": {}},
    )
    await verify_nonblocking(
        backfill_api.api_backfill_clear,
        backfill_api,
        "get_backfill_routing_snapshot",
        empty_routing,
        {"ok": True, "cleared": 0},
    )
    await verify_nonblocking(
        backfill_api.api_backfill_run,
        backfill_api,
        "get_backfill_enabled",
        False,
        {"ok": False, "msg": "补发功能已关闭"},
    )
    await verify_nonblocking(
        backfill_api.api_channel_names,
        backfill_api,
        "get_all_channels",
        {},
        {"ok": True, "names": {}, "guild_ids": {}},
    )

    previous_running = backfill._backfill_running
    previous_rerun = backfill._backfill_rerun_requested
    backfill._backfill_running = False
    backfill._backfill_rerun_requested = False
    try:
        await verify_nonblocking(
            backfill._backfill_missed,
            backfill,
            "get_backfill_runtime_snapshot",
            {
                "enabled": False,
                "limit": 10,
                "channels": {},
                "groups_by_channel": {},
            },
            None,
        )
        assert backfill._backfill_running is False
    finally:
        backfill._backfill_running = previous_running
        backfill._backfill_rerun_requested = previous_rerun

    print("PASS - 启动补发及管理 API 的配置慢读取不阻塞事件循环")


if __name__ == "__main__":
    asyncio.run(main())
