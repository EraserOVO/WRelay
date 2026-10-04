"""消息追踪：持久化合并与 sender 收口调用的针对性测试。"""

import asyncio
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

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


async def test_malformed_trace_row_does_not_block_history():
    old_path = message_trace.TRACE_FILE
    with tempfile.TemporaryDirectory() as directory:
        message_trace.TRACE_FILE = str(Path(directory) / "message_trace.json")
        malformed = [
            {
                "trace_key": "message:C1:101",
                "channel_id": "C1",
                "message_id": "101",
                "first_at": "bad",
                "last_at": {},
                "trace_order": "bad",
                "attempts": "bad",
                "media_count": "bad",
                "groups": [{"id": "G1", "ok": False}, "invalid-group"],
            },
            {
                "trace_key": "stale",
                "last_at": [],
                "trace_order": None,
                "attempts": -1,
                "media_count": "not-a-number",
                "groups": "invalid-groups",
            },
            "invalid-entry",
        ]
        Path(message_trace.TRACE_FILE).write_text(
            json.dumps(malformed),
            encoding="utf-8",
        )
        try:
            entries = message_trace.get_delivery_traces(50)
            assert len(entries) == 2
            malformed_entry = next(entry for entry in entries if entry.get("message_id") == "101")
            assert malformed_entry["attempts"] == 0
            assert malformed_entry["groups"] == [{"id": "G1", "ok": False}]

            await message_trace.record_delivery(
                "C1", "101", [{"data": {"text": "恢复后的内容"}}], [],
                ["G1"], {"G1": True},
            )
            entries = message_trace.get_delivery_traces(50)
            updated = next(entry for entry in entries if entry.get("message_id") == "101")
            assert updated["attempts"] == 1
            assert updated["result"] == "success"
            assert updated["first_at"] > 0
            assert len(entries) == 2
        finally:
            message_trace.TRACE_FILE = old_path


async def test_cancelled_trace_write_waits_for_persistence():
    old_path = message_trace.TRACE_FILE
    with tempfile.TemporaryDirectory() as directory:
        message_trace.TRACE_FILE = str(Path(directory) / "message_trace.json")
        update_started = threading.Event()
        release_update = threading.Event()
        original_update = message_trace.update_json

        def slow_update(*args, **kwargs):
            update_started.set()
            if not release_update.wait(2):
                raise AssertionError("测试未及时释放消息追踪写入")
            return original_update(*args, **kwargs)

        task = None
        try:
            with patch.object(message_trace, "update_json", side_effect=slow_update):
                task = asyncio.create_task(message_trace.record_delivery(
                    "C1", "cancelled-write", [{"data": {"text": "persist me"}}],
                    [], ["G1"], {"G1": True},
                ))
                started = await asyncio.wait_for(
                    asyncio.to_thread(update_started.wait, 1),
                    timeout=1,
                )
                assert started
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done(), "取消不应早于追踪写入完成"
                loop_tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(loop_tick.set)
                await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
                release_update.set()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("取消应在追踪写入完成后传播")
            entries = message_trace.get_delivery_traces(50)
            assert len(entries) == 1
            assert entries[0]["message_id"] == "cancelled-write"
            assert entries[0]["preview"] == "persist me"
        finally:
            release_update.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)
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
    await test_malformed_trace_row_does_not_block_history()
    await test_cancelled_trace_write_waits_for_persistence()
    await test_sender_records_final_result()
    print("PASS - 消息追踪记录、取消安全持久化、重试合并与发送收口")


if __name__ == "__main__":
    asyncio.run(main())
