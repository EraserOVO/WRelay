"""验证 Bilibili 持久化队列的损坏恢复与原文件保护。"""

import asyncio
import json
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili


def test_bad_cursor_does_not_block_other_items():
    original_state = bilibili._protection_runtime_state
    original_error = bilibili._protection_load_error
    bilibili._protection_runtime_state = None
    bilibili._protection_load_error = ""
    raw_state = {
        "queues": {
            "source": {
                "items": [
                    {
                        "id": "bad-post",
                        "cursor_id": "not-a-number",
                        "post": {"id": "bad-post", "cursor_id": "not-a-number"},
                    },
                    {
                        "id": "good-post",
                        "cursor_id": "123",
                        "post": {"id": "good-post", "cursor_id": "123"},
                    },
                ],
                "deferred_skips": [
                    {"id": "bad-skip", "cursor_id": "-1"},
                    {"id": "good-skip", "cursor_id": "124"},
                ],
            },
        },
    }
    try:
        with mock.patch.object(bilibili, "_read_protection_state", return_value=raw_state):
            recovered = bilibili._load_protection_state()
        bucket = recovered["queues"]["source"]
        assert [item["id"] for item in bucket["items"]] == ["good-post"]
        assert [item["id"] for item in bucket["deferred_skips"]] == ["good-skip"]
    finally:
        bilibili._protection_runtime_state = original_state
        bilibili._protection_load_error = original_error
    print("PASS - 损坏游标只丢弃对应 Bilibili 队列项")


async def test_bad_file_pauses_delivery_without_overwrite():
    original_state = bilibili._protection_runtime_state
    original_error = bilibili._protection_load_error
    bilibili._protection_runtime_state = None
    bilibili._protection_load_error = ""
    parse_error = json.JSONDecodeError("invalid JSON", "{", 1)
    valid_state = {
        "queues": {
            "source": {
                "items": [
                    {
                        "id": "recovered-post",
                        "cursor_id": "125",
                        "post": {"id": "recovered-post", "cursor_id": "125"},
                    }
                ],
                "deferred_skips": [],
            }
        }
    }
    try:
        with mock.patch.object(
            bilibili,
            "_read_protection_state",
            side_effect=[parse_error, parse_error, valid_state],
        ), mock.patch.object(bilibili, "atomic_write_json") as write_file, \
                mock.patch.object(bilibili, "get_bilibili_groups_for_source", return_value=["group"]), \
                mock.patch.object(bilibili, "_ensure_baseline", new=mock.AsyncMock()) as baseline, \
                mock.patch.object(bilibili.logger, "error") as log_error:
            fallback = bilibili._load_protection_state()
            assert fallback["queues"] == {}
            assert bilibili._protection_runtime_state is None
            assert bilibili._protection_load_error

            try:
                await bilibili._send_posts(
                    {"id": "source", "name": "Source"},
                    [{"id": "post", "cursor_id": "123"}],
                )
            except RuntimeError as exc:
                assert "积压队列读取失败" in str(exc), exc
            else:
                raise AssertionError("corrupt protection state must pause delivery")

            bilibili._save_protection_state(fallback)
            write_file.assert_not_called()
            baseline.assert_not_awaited()
            log_error.assert_called_once()

            recovered = bilibili._load_protection_state()
            assert "source" in recovered["queues"]
            assert not bilibili._protection_load_error
            assert bilibili._protection_runtime_state is recovered
        print("PASS - 队列文件损坏时暂停投递并保留原文件，修复后恢复加载")
    finally:
        bilibili._protection_runtime_state = original_state
        bilibili._protection_load_error = original_error


async def test_invalid_source_state_pauses_polling():
    original_error = bilibili._state_load_error
    bilibili._state_load_error = ""
    parse_error = json.JSONDecodeError("invalid JSON", "{", 1)
    valid_state = {"sources": {"source": {"active_post_types": ["专栏"]}}}
    source = {
        "id": "source",
        "name": "Source",
        "feeds": [{"type": "专栏", "url": "https://example.invalid/feed", "state_key": "source"}],
    }
    try:
        with mock.patch.object(
            bilibili,
            "_read_state",
            side_effect=[parse_error, parse_error, valid_state],
        ), mock.patch.object(bilibili, "_fetch_feed", new=mock.AsyncMock()) as fetch_feed, \
                mock.patch.object(bilibili.logger, "error") as log_error:
            try:
                bilibili._load_state()
            except RuntimeError as exc:
                assert "来源状态读取失败" in str(exc), exc
            else:
                raise AssertionError("invalid source state must not load as empty")

            try:
                await bilibili._poll_source(source)
            except RuntimeError as exc:
                assert "来源状态读取失败" in str(exc), exc
            else:
                raise AssertionError("invalid source state must pause polling")

            fetch_feed.assert_not_awaited()
            log_error.assert_called_once()
            recovered = bilibili._load_state()
            assert recovered["sources"]["source"]["active_post_types"] == ["专栏"]
            assert not bilibili._state_load_error

        bilibili._state_load_error = ""
        with mock.patch.object(bilibili, "_read_state", return_value={"sources": []}):
            try:
                bilibili._load_state()
            except RuntimeError as exc:
                assert "sources 必须是对象" in str(exc), exc
            else:
                raise AssertionError("invalid source map must not load as empty")
        print("PASS - 来源状态损坏时不重建基线，修复后恢复轮询")
    finally:
        bilibili._state_load_error = original_error


async def test_malformed_source_record_recovers_with_feed_baseline():
    source_id = "source"
    state_key = source_id + "::article"
    url = "https://example.invalid/article-feed"
    source_state = {"sources": {source_id: "damaged record"}}
    source = {
        "id": source_id,
        "name": "Source",
        "feeds": [{"type": "专栏", "url": url, "state_key": state_key}],
    }
    posts = [{"id": "old-article", "cursor_id": "123", "title": "old"}]
    original_error = bilibili._state_load_error
    bilibili._state_load_error = ""
    try:
        with mock.patch.object(bilibili, "_read_state", return_value=source_state), \
                mock.patch.object(bilibili, "_save_state"), \
                mock.patch.object(
                    bilibili,
                    "_fetch_feed",
                    new=mock.AsyncMock(return_value=(b"<feed/>", "fresh-etag", None)),
                ) as fetch_feed, \
                mock.patch.object(bilibili, "parse_feed", return_value=posts), \
                mock.patch.object(
                    bilibili,
                    "_send_posts",
                    new=mock.AsyncMock(return_value=(0, 0, 0)),
                ) as send_posts, \
                mock.patch.object(bilibili.logger, "warning") as log_warning:
            result = await bilibili._poll_source(source)

        fetch_feed.assert_awaited_once_with(url, None)
        send_posts.assert_awaited_once_with(source, [])
        assert source_state["sources"][source_id]["active_post_types"] == ["专栏"]
        feed_state = source_state["sources"][state_key]
        assert feed_state["baseline_cursor_id"] == "123"
        assert feed_state["feed_initialized"] is True
        assert result["result"] == "ok"
        log_warning.assert_called_once()
        print("PASS - 单来源状态条目损坏时重建专栏基线")
    finally:
        bilibili._state_load_error = original_error


async def test_source_state_write_failure_aborts_feed():
    source_state = {"sources": {"source": {"active_post_types": ["视频"]}}}
    source = {
        "id": "source",
        "name": "Source",
        "feeds": [{"type": "专栏", "url": "https://example.invalid/feed", "state_key": "source"}],
    }
    posts = [{"id": "post", "cursor_id": "123"}]
    with mock.patch.object(bilibili, "_read_state", return_value=source_state), \
            mock.patch.object(
                bilibili,
                "_fetch_feed",
                new=mock.AsyncMock(return_value=("<feed/>", "etag", None)),
            ), mock.patch.object(bilibili, "parse_feed", return_value=posts), \
            mock.patch.object(
                bilibili,
                "atomic_write_json",
                side_effect=[None, OSError("simulated state write failure")],
            ) as write_state, mock.patch.object(bilibili, "_send_posts", new=mock.AsyncMock()) as send_posts:
        try:
            await bilibili._poll_source(source)
        except OSError as exc:
            assert "simulated state write failure" in str(exc)
        else:
            raise AssertionError("source state write failure must abort the poll")

        assert write_state.call_count == 2, write_state.call_count
        send_posts.assert_not_awaited()
    print("PASS - 来源基线写入失败时不继续处理 feed 投稿")


async def main():
    test_bad_cursor_does_not_block_other_items()
    await test_bad_file_pauses_delivery_without_overwrite()
    await test_invalid_source_state_pauses_polling()
    await test_malformed_source_record_recovers_with_feed_baseline()
    await test_source_state_write_failure_aborts_feed()


if __name__ == "__main__":
    asyncio.run(main())
