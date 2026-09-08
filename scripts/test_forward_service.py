"""验证 Discord 自动转发共用的渲染/翻译准备流程。"""
import asyncio
import unittest.mock as mock
from types import SimpleNamespace

import nonebot

nonebot.init()

from plugins import forward_service


async def main():
    build_calls = []
    translate_calls = []

    async def fake_build(message, source_label, enabled_types):
        build_calls.append((message, source_label, enabled_types))
        return ["header", "body"], ["media"], True

    async def fake_translate(parts, cache_key, skip_leading_parts):
        translate_calls.append((parts, cache_key, skip_leading_parts))
        return ["translated"]

    with mock.patch.object(forward_service, "build_parts", side_effect=fake_build), \
            mock.patch.object(forward_service, "translate_text_parts", side_effect=fake_translate):
        result = await forward_service.prepare_discord_message(
            {"content": "Hello"},
            "C1",
            "M1",
            source_label="自动补发来自",
            filter_config={"filter_message_types": ["文字"]},
        )

    assert result == (["translated"], ["media"]), result
    assert build_calls[0][1] == "自动补发来自"
    assert translate_calls == [(
        ["header", "body"],
        ("discord", "C1", "M1"),
        1,
    )]
    print("PASS - Discord 实时/补发/重试共用消息准备流程")

    # 队列按频道串行消费，且处理完成后归还容量。
    relay = __import__("plugins.relay", fromlist=["relay"])
    order = []

    async def fake_process(_bot, event):
        order.append(event.id)

    with mock.patch.object(relay, "_process_event", side_effect=fake_process):
        await relay._enqueue_relay_event("C1", object(), SimpleNamespace(id="M1"))
        await relay._enqueue_relay_event("C1", object(), SimpleNamespace(id="M2"))
        while relay._relay_tasks:
            await asyncio.sleep(0)

    assert order == ["M1", "M2"], order
    assert not relay._relay_queues
    print("PASS - 实时消息按频道有序入队并在处理后释放容量")


if __name__ == "__main__":
    asyncio.run(main())
