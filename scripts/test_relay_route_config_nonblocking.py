"""Verify live Discord relay route reads do not block the event loop."""
import asyncio
import time
from unittest import mock

import nonebot

nonebot.init()

from plugins import relay


class FakeEvent:
    channel_id = "C1"


class FakeBot:
    pass


async def verify_nonblocking(awaitable_factory, patch_target, result):
    def slow_getter(_channel_id):
        time.sleep(0.25)
        return result

    with mock.patch.object(relay, patch_target, side_effect=slow_getter):
        task = asyncio.create_task(awaitable_factory())
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        await task
    assert elapsed < 0.15, "{} 同步路由读取阻塞事件循环: {:.3f}s".format(
        patch_target, elapsed
    )


async def main():
    with mock.patch.object(relay, "DiscordBot", FakeBot), \
            mock.patch.object(relay, "GuildMessageCreateEvent", FakeEvent):
        await verify_nonblocking(
            lambda: relay._process_event(FakeBot(), FakeEvent()),
            "get_discord_relay_config",
            None,
        )
    await verify_nonblocking(
        lambda: relay._load_active_groups("C1"),
        "get_groups_for_channel",
        ["G1"],
    )
    print("PASS - Discord 实时路由及发送前群列表慢读取不阻塞事件循环")


if __name__ == "__main__":
    asyncio.run(main())
