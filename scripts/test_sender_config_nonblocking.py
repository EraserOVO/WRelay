"""验证 QQ 发送路由从一致的配置快照读取且不阻塞事件循环。"""
import asyncio
import time
from contextlib import ExitStack
from unittest import mock

import nonebot

nonebot.init()

from plugins import config, sender
from plugins.media import make_text


class FakeBot:
    self_id = "bot-a"


def _slow_getter(value, ticks, label):
    def getter(*_args, **_kwargs):
        before = ticks[0]
        time.sleep(0.06)
        assert ticks[0] > before, "{} 配置读取阻塞了事件循环".format(label)
        return value
    return getter


async def _run_with_heartbeat(operation):
    ticks = [0]
    stop = asyncio.Event()

    async def heartbeat():
        while not stop.is_set():
            ticks[0] += 1
            await asyncio.sleep(0.005)

    task = asyncio.create_task(heartbeat())
    try:
        await operation(ticks)
    finally:
        stop.set()
        await task


def _patch_send_path(bot):
    return (
        mock.patch.object(sender, "get_qq_bot", return_value=bot),
        mock.patch.object(sender, "get_qq_bots", return_value={"bot-a": bot}),
        mock.patch.object(
            sender,
            "send_text_parts",
            new=mock.AsyncMock(return_value={"group-a": True}),
        ),
        mock.patch.object(
            sender,
            "send_media_items",
            new=mock.AsyncMock(return_value={"group-a": True}),
        ),
        mock.patch.object(sender, "_record_trace", new=mock.AsyncMock()),
    )


async def main():
    data = {
        "qq_group_openids": [
            {"openid": "group-a", "enabled": True, "bot_id": "bot-a"},
            {"openid": "group-a", "enabled": True, "bot_id": "bot-b"},
            {"openid": "group-b", "enabled": False, "bot_id": "bot-b"},
        ]
    }
    loads = []
    with mock.patch.object(
        config, "_load_settings", side_effect=lambda: loads.append(1) or data
    ):
        groups, routes = config.get_active_group_routing_snapshot()
    assert loads == [1], loads
    assert groups == ["group-a"], groups
    assert routes == {"group-a": "bot-a"}, routes

    bot = FakeBot()

    async def check_default_route(ticks):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                sender,
                "get_active_group_routing_snapshot",
                side_effect=_slow_getter(
                    (["group-a"], {"group-a": "bot-a"}),
                    ticks,
                    "默认群和 Bot 绑定快照",
                ),
            ))
            stack.enter_context(mock.patch.object(
                sender,
                "get_group_bot_ids",
                side_effect=AssertionError("默认路由应复用同一份快照"),
            ))
            for patcher in _patch_send_path(bot):
                stack.enter_context(patcher)
            result = await sender.send_relay_message(
                [make_text("hello")], [], groups=None
            )
        assert result == {"group-a": True}, result

    await _run_with_heartbeat(check_default_route)

    async def check_explicit_route(ticks):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                sender,
                "get_group_bot_ids",
                side_effect=_slow_getter(
                    {"group-a": "bot-a"}, ticks, "显式目标群 Bot 绑定"
                ),
            ))
            for patcher in _patch_send_path(bot):
                stack.enter_context(patcher)
            result = await sender.send_relay_message(
                [make_text("hello")], [], groups=["group-a"]
            )
        assert result == {"group-a": True}, result

    await _run_with_heartbeat(check_explicit_route)
    print("PASS - QQ 默认路由单次快照与发送配置慢读不阻塞事件循环")


if __name__ == "__main__":
    asyncio.run(main())
