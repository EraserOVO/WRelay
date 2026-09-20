"""消息追踪：持久化合并与 sender 收口调用的针对性测试。"""

import asyncio
import tempfile
from pathlib import Path

import nonebot

nonebot.init()

from plugins import message_trace
from plugins import sender


async def test_trace_merges_retry():
    old_path = message_trace.TRACE_FILE
    with tempfile.TemporaryDirectory() as directory:
        message_trace.TRACE_FILE = str(Path(directory) / "message_trace.json")
        try:
            await message_trace.record_delivery(
                "x_rss:IGN", "100", [{"data": {"text": "第一条投递内容"}}], [],
                ["G1", "G2"], {"G1": True, "G2": False},
            )
            await message_trace.record_delivery(
                "x_rss:IGN", "100", [], [], ["G2"], {"G2": True},
            )
            entries = message_trace.get_delivery_traces(50)
            assert len(entries) == 1
            entry = entries[0]
            assert entry["result"] == "success"
            assert entry["attempts"] == 2
            assert entry["delivered_count"] == 2
            assert entry["target_count"] == 2
            assert entry["preview"] == "第一条投递内容"
            assert entry["source_kind"] == "x_rss"

            await message_trace.record_delivery(
                "", "", [], [], ["G3"], {}, note="QQ Bot 未连接",
            )
            entries = message_trace.get_delivery_traces(50)
            assert len(entries) == 2
            unsent = next(entry for entry in entries if entry["result"] == "not_sent")
            assert unsent["note"] == "QQ Bot 未连接"
        finally:
            message_trace.TRACE_FILE = old_path


async def test_sender_records_final_result():
    original = {
        "get_qq_bot": sender.get_qq_bot,
        "send_media_items": sender.send_media_items,
        "record_delivery": sender.record_delivery,
    }
    captured = []

    async def fake_media(_bot, groups, _items, **_kwargs):
        return {group: True for group in groups}

    async def capture(*args, **kwargs):
        captured.append((args, kwargs))

    try:
        sender.get_qq_bot = lambda: object()
        sender.send_media_items = fake_media
        sender.record_delivery = capture
        result = await sender.send_relay_message(
            [], [], groups=["G1"], channel_id="C1", message_id="M1",
        )
        assert result == {"G1": True}
        assert len(captured) == 1
        args, kwargs = captured[0]
        assert args[0:2] == ("C1", "M1")
        assert args[4] == ["G1"]
        assert args[5] == {"G1": True}
        assert kwargs.get("note", "") == ""
    finally:
        sender.get_qq_bot = original["get_qq_bot"]
        sender.send_media_items = original["send_media_items"]
        sender.record_delivery = original["record_delivery"]


async def main():
    await test_trace_merges_retry()
    await test_sender_records_final_result()
    print("PASS - 消息追踪记录、重试合并与发送收口")


if __name__ == "__main__":
    asyncio.run(main())
