"""验证 QQ 群手动转发路由配置不阻塞事件循环且 pending 按序消费。"""
import asyncio
import time
from unittest import mock

import nonebot

nonebot.init()

from plugins import config, qq_fwd
from plugins.media import make_text


class FakeBot:
    def __init__(self):
        self.replies = []

    async def send(self, _event, message):
        self.replies.append(message)


class FakeEvent:
    group_openid = "source-group"
    author = type("Author", (), {"member_openid": "member-1"})()
    attachments = []
    id = "event-1"

    def __init__(self, text):
        self.text = text

    def get_plaintext(self):
        return self.text


def _slow(value, ticks, label):
    def getter(*_args, **_kwargs):
        before = ticks[0]
        time.sleep(0.06)
        assert ticks[0] > before, "{} 配置读取阻塞了事件循环".format(label)
        return value
    return getter


async def _with_heartbeat(operation):
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


async def main():
    settings = {
        "qq_group_openids": [
            {"openid": "source-group", "enabled": True, "name": "来源群"},
            {"openid": "target-group", "enabled": True, "name": "目标群"},
        ],
        "qq_forwarding_groups": [{
            "id": "qq-group-1",
            "name": "群转发组",
            "enabled": True,
            "groups": ["source-group", "target-group"],
        }],
    }
    settings_loads = []
    with mock.patch.object(
        config,
        "_load_settings",
        side_effect=lambda: settings_loads.append(1) or settings,
    ):
        entries, related = config.get_qq_fwd_routing_snapshot("source-group")
    assert settings_loads == [1], settings_loads
    assert [item["openid"] for item in entries] == ["source-group", "target-group"]
    assert related == {"target-group"}, related

    async def check_list_lookup(ticks):
        bot = FakeBot()
        event = FakeEvent("relay list")
        with mock.patch.object(
            qq_fwd,
            "get_qq_fwd_routing_snapshot",
            side_effect=_slow((entries, related), ticks, "群转发候选快照"),
        ):
            await qq_fwd._cmd_relay_list(bot, event)
        assert bot.replies == ["【Relay群列表】\n1. 目标群"], bot.replies

    await _with_heartbeat(check_list_lookup)

    async def check_forward_validation(ticks):
        qq_fwd._pending.clear()
        qq_fwd.set_pending(
            "source-group", "member-1", {"openid": "target-group", "name": "目标群"}
        )
        bot = FakeBot()
        event = FakeEvent("hello")
        with (
            mock.patch.object(
                qq_fwd,
                "get_qq_fwd_select_timeout",
                side_effect=_slow(60, ticks, "pending 超时"),
            ),
            mock.patch.object(
                qq_fwd,
                "get_qq_fwd_routing_snapshot",
                side_effect=_slow((entries, related), ticks, "发送前目标校验"),
            ),
            mock.patch.object(
                qq_fwd,
                "translate_text_parts",
                new=mock.AsyncMock(return_value=[make_text("hello")]),
            ),
            mock.patch.object(
                qq_fwd,
                "send_forward_to_group",
                new=mock.AsyncMock(return_value={"target-group": True}),
            ),
        ):
            await qq_fwd._try_forward_next(bot, event, "hello")
        assert bot.replies == ["转发成功"], bot.replies
        assert not qq_fwd.has_pending("source-group", "member-1")

    await _with_heartbeat(check_forward_validation)

    async def check_expiry_lookup(ticks):
        qq_fwd._pending.clear()
        qq_fwd.set_pending(
            "source-group", "member-1", {"openid": "target-group", "name": "目标群"}
        )
        entry = qq_fwd.get_pending("source-group", "member-1")
        with mock.patch.object(
            qq_fwd,
            "get_qq_fwd_select_timeout",
            side_effect=_slow(10, ticks, "超时清扫配置"),
        ):
            await qq_fwd.expire_pendings(entry["created_ts"] + 20)
        assert not qq_fwd.has_pending("source-group", "member-1")

    await _with_heartbeat(check_expiry_lookup)

    qq_fwd._pending.clear()
    first_route_started = asyncio.Event()
    loop = asyncio.get_running_loop()
    route_calls = 0

    def slow_first_route(_group):
        nonlocal route_calls
        route_calls += 1
        if route_calls == 1:
            loop.call_soon_threadsafe(first_route_started.set)
            time.sleep(0.12)
        return entries, related

    bot = FakeBot()
    with (
        mock.patch.object(qq_fwd, "QQBot", FakeBot),
        mock.patch.object(qq_fwd, "GroupMessageCreateEvent", FakeEvent),
        mock.patch.object(
            qq_fwd,
            "get_qq_fwd_routing_snapshot",
            side_effect=slow_first_route,
        ),
        mock.patch.object(qq_fwd, "get_qq_fwd_select_timeout", return_value=60),
        mock.patch.object(
            qq_fwd,
            "translate_text_parts",
            new=mock.AsyncMock(return_value=[make_text("hello")]),
        ),
        mock.patch.object(
            qq_fwd,
            "send_forward_to_group",
            new=mock.AsyncMock(return_value={"target-group": True}),
        ),
    ):
        selection = asyncio.create_task(qq_fwd.handle_fwd(bot, FakeEvent("relay 1")))
        await asyncio.wait_for(first_route_started.wait(), timeout=1)
        content = asyncio.create_task(qq_fwd.handle_fwd(bot, FakeEvent("hello")))
        await asyncio.gather(selection, content)
    assert bot.replies == ["转发成功"], bot.replies
    assert not qq_fwd.has_pending("source-group", "member-1")
    print("PASS - QQ 群转发单次配置快照、慢读调度与 pending 顺序")


if __name__ == "__main__":
    asyncio.run(main())
