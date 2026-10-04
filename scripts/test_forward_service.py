"""验证 Discord 自动转发共用的渲染/翻译准备流程。"""
import asyncio
import time
import unittest.mock as mock
from types import SimpleNamespace

import nonebot

nonebot.init()

from plugins import forward_service


async def main():
    build_calls = []
    translate_calls = []

    async def fake_build(message, source_label, enabled_types, show_datetime):
        build_calls.append((message, source_label, enabled_types, show_datetime))
        return ["header", "body"], ["media"], True

    async def fake_translate(parts, cache_key, skip_leading_parts, config=None):
        translate_calls.append((parts, cache_key, skip_leading_parts, config))
        return ["translated"]

    with mock.patch.object(forward_service, "build_parts", side_effect=fake_build), \
            mock.patch.object(forward_service, "translate_text_parts", side_effect=fake_translate), \
            mock.patch.object(
                forward_service,
                "get_channel_translation_config",
                return_value={"enabled": False, "show_datetime": False},
            ):
        result = await forward_service.prepare_discord_message(
            {"content": "Hello"},
            "C1",
            "M1",
            source_label="自动补发来自",
            filter_config={"filter_message_types": ["文字"]},
        )

    assert result == (["translated"], ["media"]), result
    assert build_calls[0][1] == "自动补发来自"
    assert build_calls[0][3] is False
    assert translate_calls[0][:3] == (
        ["header", "body"],
        ("discord", "C1", "M1"),
        1,
    )
    assert translate_calls[0][3]["enabled"] is False
    print("PASS - Discord 实时/补发/重试共用消息准备流程")

    # A slow settings fingerprint must not hold up other event-loop tasks.
    def slow_translation_config(_channel_id):
        time.sleep(0.25)
        return {"enabled": False, "show_datetime": True}

    async def simple_build(message, source_label, enabled_types, show_datetime):
        build_calls.append((message, source_label, enabled_types, show_datetime))
        return ["header", "body"], [], True

    with mock.patch.object(
        forward_service,
        "get_channel_translation_config",
        side_effect=slow_translation_config,
    ), mock.patch.object(forward_service, "build_parts", side_effect=simple_build), \
            mock.patch.object(forward_service, "translate_text_parts", side_effect=fake_translate):
        task = asyncio.create_task(forward_service.prepare_discord_message(
            {"content": "Hello"},
            "C1",
            "M2",
            filter_config={"filter_message_types": ["文字"]},
        ))
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        await task
    assert elapsed < 0.15, "翻译配置慢读取阻塞事件循环: {:.3f}s".format(elapsed)
    assert build_calls[-1][3] is True
    print("PASS - Discord 翻译配置慢读取不阻塞事件循环")

    def slow_filter_config(_channel_id):
        time.sleep(0.25)
        return {"filter_message_types": ["文字"]}

    with mock.patch.object(
        forward_service,
        "get_channel_filter",
        side_effect=slow_filter_config,
    ), mock.patch.object(
        forward_service,
        "get_channel_translation_config",
        return_value={"enabled": False, "show_datetime": False},
    ), mock.patch.object(forward_service, "build_parts", side_effect=simple_build), \
            mock.patch.object(forward_service, "translate_text_parts", side_effect=fake_translate):
        task = asyncio.create_task(forward_service.prepare_discord_message(
            {"content": "Hello"},
            "C1",
            "M3",
        ))
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        result = await task
    assert result == (["translated"], [])
    assert elapsed < 0.15, "频道筛选配置慢读取阻塞事件循环: {:.3f}s".format(elapsed)
    print("PASS - Discord 筛选配置慢读取不阻塞事件循环")

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
