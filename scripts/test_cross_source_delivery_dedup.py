"""Verify cross-source RSS deliveries dedupe by native post ID and QQ group."""

import asyncio
import tempfile
from pathlib import Path
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili, delivery_dedup, retry, x_rss


def _set_database(path):
    delivery_dedup.DELIVERY_DEDUP_DB_FILE = str(path)
    with delivery_dedup._volatile_lock:
        delivery_dedup._volatile_deliveries.clear()
    retry._inflight.clear()


def _cursor_writer(last):
    async def update(mutate):
        return mutate(last)
    return update


async def _exercise_shared_send_gate(tmp):
    _set_database(Path(tmp) / "shared.sqlite3")
    cursors = {}
    sender = mock.AsyncMock(side_effect=[
        {"G1": True},
        {"G2": True},
        {"G1": False},
        {"G1": True},
    ])
    with mock.patch.object(retry, "send_relay_message", new=sender), \
            mock.patch.object(
                retry,
                "update_last_messages",
                new=_cursor_writer(cursors),
            ), mock.patch.object(
                retry,
                "_purge_delivered_async",
                new=mock.AsyncMock(),
            ):
        first = await retry.send_and_record(
            "x_rss:source-a", "100", ["text"], [], ["G1"],
            idempotency_key="x:100",
        )
        second = await retry.send_and_record(
            "x_rss:source-b", "100", ["text"], [], ["G1", "G2"],
            idempotency_key="x:100",
        )
        assert first["G1"] is True
        assert first.sent_groups == {"G1"}
        assert second["G1"] is True and second["G2"] is True
        assert second.deduplicated_groups == {"G1"}
        assert second.sent_groups == {"G2"}
        assert sender.await_args_list[1].args[2] == ["G2"]

        failed = await retry.send_and_record(
            "bilibili:source-a", "200", ["text"], [], ["G1"],
            idempotency_key="bilibili:av-failed",
        )
        retried = await retry.send_and_record(
            "bilibili:source-b", "200", ["text"], [], ["G1"],
            idempotency_key="bilibili:av-failed",
        )
        assert failed["G1"] is False
        assert retried["G1"] is True
        assert retried.sent_groups == {"G1"}
        assert sender.await_count == 4

    assert await delivery_dedup.delivered_groups("x:100", ["G1", "G2"]) == {"G1", "G2"}
    print("PASS - 跨来源按平台投稿 ID 和 QQ 群去重，失败群仍可重试")


async def _exercise_concurrent_sources(tmp):
    _set_database(Path(tmp) / "concurrent.sqlite3")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []
    cursors = {}

    async def slow_sender(_text, _media, groups, **_kwargs):
        calls.append(list(groups))
        started.set()
        await release.wait()
        return {group: True for group in groups}

    with mock.patch.object(retry, "send_relay_message", new=slow_sender), \
            mock.patch.object(retry, "update_last_messages", new=_cursor_writer(cursors)), \
            mock.patch.object(retry, "_purge_delivered_async", new=mock.AsyncMock()):
        first_task = asyncio.create_task(retry.send_and_record(
            "x_rss:source-a", "150", ["text"], [], ["G1"],
            idempotency_key="x:150",
        ))
        await started.wait()
        concurrent = await retry.send_and_record(
            "x_rss:source-b", "150", ["text"], [], ["G1"],
            idempotency_key="x:150",
        )
        assert concurrent["G1"] is False
        assert concurrent.busy_groups == {"G1"}
        release.set()
        first = await first_task
        later = await retry.send_and_record(
            "x_rss:source-b", "150", ["text"], [], ["G1"],
            idempotency_key="x:150",
        )

    assert first.sent_groups == {"G1"}
    assert later.deduplicated_groups == {"G1"}
    assert calls == [["G1"]]
    print("PASS - 同一投稿并发轮询时只允许一个来源进入发送")


async def _exercise_duplicate_targets(tmp):
    _set_database(Path(tmp) / "duplicate-targets.sqlite3")
    sender = mock.AsyncMock(side_effect=[{"G1": False}, {"G1": True}])
    with mock.patch.object(retry, "send_relay_message", new=sender), \
            mock.patch.object(retry, "update_last_messages", new=_cursor_writer({})), \
            mock.patch.object(retry, "_purge_delivered_async", new=mock.AsyncMock()):
        failed = await retry.send_and_record(
            "x_rss:source-a", "175", ["text"], [], ["G1", "G1"],
            idempotency_key="x:175",
        )
        retried = await retry.send_and_record(
            "x_rss:source-a", "175", ["text"], [], ["G1", "G1"],
            idempotency_key="x:175",
        )

    assert failed["G1"] is False
    assert failed.busy_groups == set()
    assert retried["G1"] is True
    assert retried.sent_groups == {"G1"}
    assert retried.busy_groups == set()
    assert [call.args[2] for call in sender.await_args_list] == [["G1"], ["G1"]]
    print("PASS - 重复目标在并发占用前归一化，真实失败仍正确保留重试")


async def _exercise_database_failure_fallback(tmp):
    _set_database(Path(tmp) / "database-failure.sqlite3")
    with mock.patch.object(
        delivery_dedup,
        "_record_delivered_groups",
        side_effect=OSError("simulated ledger write failure"),
    ):
        try:
            await delivery_dedup.record_delivered_groups("x:write-failure", ["G1"])
        except OSError:
            pass
        else:
            raise AssertionError("ledger write failure should remain visible")

    assert await delivery_dedup.delivered_groups("x:write-failure", ["G1"]) == {"G1"}
    with mock.patch.object(
        delivery_dedup,
        "_read_delivered_groups",
        side_effect=OSError("simulated ledger read failure"),
    ):
        assert await delivery_dedup.delivered_groups("x:write-failure", ["G1"]) == {"G1"}
        try:
            await delivery_dedup.delivered_groups(
                "x:write-failure",
                ["G1", "G2"],
            )
        except OSError:
            pass
        else:
            raise AssertionError("unknown groups must fail closed on ledger read error")
    print("PASS - SQLite 记录失败后用有界内存兜底，未知群在读取异常时保持失败关闭")


async def _exercise_volatile_cache_bounds(tmp):
    _set_database(Path(tmp) / "volatile-cache-bounds.sqlite3")
    with mock.patch.object(delivery_dedup, "DELIVERY_DEDUP_MAX_RECORDS", 2):
        delivery_dedup._remember_volatile_deliveries("x:cache-1", ["G1"])
        delivery_dedup._remember_volatile_deliveries("x:cache-2", ["G1"])
        delivery_dedup._remember_volatile_deliveries("x:cache-3", ["G1"])
        with delivery_dedup._volatile_lock:
            assert list(delivery_dedup._volatile_deliveries) == [
                ("x:cache-2", "G1"),
                ("x:cache-3", "G1"),
            ]

    with mock.patch.object(delivery_dedup, "DELIVERY_DEDUP_TTL_SECONDS", 0.001):
        delivery_dedup._remember_volatile_deliveries("x:expired", ["G1"])
        await asyncio.sleep(0.02)
        assert await delivery_dedup.delivered_groups("x:expired", ["G1"]) == set()
    print("PASS - 内存兜底按容量淘汰最旧项并使用 TTL 清理过期项")


async def _exercise_x_sources(tmp):
    _set_database(Path(tmp) / "x.sqlite3")
    cursors = {}
    sender = mock.AsyncMock(return_value={"G1": True})
    with mock.patch.object(x_rss, "_load_source_groups", new=mock.AsyncMock(return_value=["G1"])), \
            mock.patch.object(x_rss, "_ensure_baselines", new=mock.AsyncMock()), \
            mock.patch.object(x_rss, "load_last_messages_async", new=mock.AsyncMock(return_value=cursors)), \
            mock.patch.object(
                x_rss,
                "_load_source_delivery_config",
                new=mock.AsyncMock(return_value={"filter": {}, "translation": {"show_datetime": False}}),
            ), mock.patch.object(x_rss, "check_x_post_filter", return_value=True), \
            mock.patch.object(x_rss, "_render_post", new=mock.AsyncMock(return_value=(["text"], []))), \
            mock.patch.object(x_rss, "translate_text_parts", new=mock.AsyncMock(side_effect=lambda parts, *args, **kwargs: parts)), \
            mock.patch.object(x_rss, "record", new=mock.AsyncMock()), \
            mock.patch.object(retry, "send_relay_message", new=sender), \
            mock.patch.object(
                retry,
                "update_last_messages",
                new=_cursor_writer(cursors),
            ), mock.patch.object(retry, "_purge_delivered_async", new=mock.AsyncMock()):
        post = {"id": "300", "text": "same X post"}
        first = await x_rss._send_posts({"key": "source-a", "name": "source-a"}, [post])
        second = await x_rss._send_posts({"key": "source-b", "name": "source-b"}, [post])

    assert first == (1, 1, 0)
    assert second == (0, 0, 0)
    assert sender.await_count == 1
    print("PASS - X 多来源重叠投稿只向同一 QQ 群发送一次")


async def _exercise_bilibili_sources(tmp):
    _set_database(Path(tmp) / "bilibili.sqlite3")
    cursors = {}
    post = {"id": "av-400", "cursor_id": "400", "title": "same Bilibili post"}
    state = {
        "version": 1,
        "queues": {
            "source-a": {"items": [{"id": post["id"], "cursor_id": "400", "post": dict(post)}], "deferred_skips": []},
            "source-b": {"items": [{"id": post["id"], "cursor_id": "400", "post": dict(post)}], "deferred_skips": []},
        },
        "sent_at": [],
    }
    sources = [
        {"id": "source-a", "name": "source-a"},
        {"id": "source-b", "name": "source-b"},
    ]
    sender = mock.AsyncMock(return_value={"G1": True})
    with mock.patch.object(
        bilibili,
        "_load_protection_config",
        new=mock.AsyncMock(return_value={"min_send_interval": 0, "hourly_limit": 10, "backlog_limit": 10}),
    ), mock.patch.object(
        bilibili,
        "_load_protection_state_async",
        new=mock.AsyncMock(return_value=(state, "")),
    ), mock.patch.object(
        bilibili,
        "_load_polling_snapshot",
        new=mock.AsyncMock(return_value=(sources, {"source-a": ["G1"], "source-b": ["G1"]})),
    ), mock.patch.object(
        bilibili,
        "load_last_messages_async",
        new=mock.AsyncMock(return_value=cursors),
    ), mock.patch.object(
        bilibili,
        "_load_source_delivery_config",
        new=mock.AsyncMock(return_value={"filter": {}, "translation": {}}),
    ), mock.patch.object(
        bilibili,
        "check_bilibili_video_filter",
        return_value=True,
    ), mock.patch.object(
        bilibili,
        "_render_post",
        new=mock.AsyncMock(return_value=(["text"], [])),
    ), mock.patch.object(
        bilibili,
        "translate_text_parts",
        new=mock.AsyncMock(side_effect=lambda parts, *args, **kwargs: parts),
    ), mock.patch.object(
        bilibili,
        "_save_protection_state_async",
        new=mock.AsyncMock(),
    ), mock.patch.object(
        bilibili,
        "_flush_deferred_skips",
        new=mock.AsyncMock(),
    ), mock.patch.object(
        bilibili,
        "record",
        new=mock.AsyncMock(),
    ), mock.patch.object(
        retry,
        "send_relay_message",
        new=sender,
    ), mock.patch.object(
        retry,
        "update_last_messages",
        new=_cursor_writer(cursors),
    ), mock.patch.object(
        retry,
        "_purge_delivered_async",
        new=mock.AsyncMock(),
    ):
        first = await bilibili._dispatch_one_locked()
        second = await bilibili._dispatch_one_locked()

    assert first == (1, 0)
    assert second == (0, 0)
    assert sender.await_count == 1
    assert state["queues"]["source-a"]["items"] == []
    assert state["queues"]["source-b"]["items"] == []
    assert len(state["sent_at"]) == 1
    assert cursors["bilibili:source-b"]["G1"] == "400"
    print("PASS - Bilibili 多来源重叠投稿只发送一次且跳过不消耗限速配额")


async def _run():
    with tempfile.TemporaryDirectory(prefix="wrelay_cross_source_dedup_") as tmp:
        await _exercise_shared_send_gate(tmp)
        await _exercise_concurrent_sources(tmp)
        await _exercise_duplicate_targets(tmp)
        await _exercise_database_failure_fallback(tmp)
        await _exercise_volatile_cache_bounds(tmp)
        await _exercise_x_sources(tmp)
        await _exercise_bilibili_sources(tmp)


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
