"""验证 plugins/retry.py 延迟重试 + 在途判重 + 只进不退游标。

覆盖（对应需求）：
1. M1 失败 → M2 成功 → M1 延迟重试成功，M1 不丢
2. retry 与 backfill 并发处理同一条消息不会重复发送
3. 游标只进不退（不回退到已过去的失败消息）
4. 多群部分失败只重试失败群
5. 重试全部失败后，现有 backfill 仍能按游标兜底
6. 正常成功消息无额外重试
7. 被其他发送路径占用的目标群不消耗重试预算
8. 其他群耗尽时仍保留并发发送中的目标群
9. 新欠账可唤醒等待中的重试循环，且完成后清理任务状态

用法：.venv/Scripts/python.exe scripts/test_retry_recovery.py
"""
import asyncio
import copy
import json
import tempfile
import time
import unittest.mock as mock
from pathlib import Path

import nonebot

nonebot.init()

from plugins import dedup
from plugins import retry as retry_mod


class FakeStore:
    """替身：load 返回最新副本；update 应用变更（模拟锁内读改写）"""

    def __init__(self, initial=None):
        self.state = copy.deepcopy(initial) if initial is not None else {}

    def load(self):
        return copy.deepcopy(self.state)

    async def update(self, mutate):
        last = copy.deepcopy(self.state)
        changed = mutate(last)
        if changed:
            self.state = last
        return changed


class FakeSender:
    """替身：按 (channel, message) 脚本化保底结果，可模拟延迟"""

    def __init__(self, results=None, delay=0.0):
        self.results = results or {}
        self.delay = delay
        self.calls = []  # (channel_id, message_id, groups)

    async def __call__(
        self,
        text_parts,
        media_items,
        groups=None,
        channel_id="",
        message_id="",
    ):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append((channel_id, message_id, list(groups or [])))
        seq = self.results.get((channel_id, message_id))
        if seq:
            scripted = seq.pop(0)
            return {g: scripted.get(g, True) for g in (groups or [])}
        return {g: True for g in (groups or [])}


async def fake_prepare_message(
    message,
    channel_id,
    message_id,
    source_label="自动转发来自",
    filter_config=None,
):
    return (["占位"], [])


class Ctx:
    """打补丁上下文：FakeStore + FakeSender + build_parts，
    关闭自动拉起重试循环，由测试手动驱动 _process_due"""

    def __init__(self, store_initial, sender_results=None, sender_delay=0.0):
        self.store = FakeStore(store_initial)
        self.sender = FakeSender(sender_results, sender_delay)
        self._retry_tempdir = tempfile.TemporaryDirectory(prefix="wrelay_retry_test_")
        self._retry_file = retry_mod.RETRY_FILE
        self._pending_load_error = retry_mod._pending_load_error
        self._last_error = retry_mod._last_error
        self._patch = mock.patch.multiple(
            retry_mod,
            load_last_messages=self.store.load,
            update_last_messages=self.store.update,
            send_relay_message=self.sender,
            prepare_discord_message=fake_prepare_message,
            RETRY_FILE=str(Path(self._retry_tempdir.name) / "retry_pending.json"),
        )

    def __enter__(self):
        retry_mod._LOOP_AUTO_START = False
        retry_mod._pending.clear()
        retry_mod._inflight.clear()
        retry_mod._pending_load_error = ""
        retry_mod._last_error = ""
        self._patch.start()
        retry_mod._LOOP_AUTO_START = False
        return self

    def __exit__(self, *exc):
        retry_mod._pending.clear()
        retry_mod._inflight.clear()
        retry_mod._LOOP_AUTO_START = True
        self._patch.stop()
        retry_mod._pending_load_error = self._pending_load_error
        retry_mod._last_error = self._last_error
        self._retry_tempdir.cleanup()


async def test_m1_fail_m2_ok_m1_retry_not_lost():
    # M1(101) 对 B 失败 → 入队；M2(102) 成功推进 B 到 102；
    # 延迟重试补上 M1（B 收到），游标保持 102 不回退，队列清空
    initial = {"C1": {"A": "100", "B": "100"}}
    # M1：实时发送 A 成功/B 失败；重试再投时成功
    results = {("C1", "101"): [{"B": False}, {}]}
    with Ctx(initial, results) as ctx:
        ok1 = await retry_mod.send_and_record(
            "C1", "101", ["p"], [], ["A", "B"]
        )
        assert ok1 == {"A": True, "B": False}, ok1

        failed = [g for g, ok in ok1.items() if not ok]
        created = retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            failed, "自动转发来自",
        )
        assert created is True
        assert retry_mod._pending["C1"]["101"].groups == {"B"}

        ok2 = await retry_mod.send_and_record("C1", "102", ["p"], [], ["B"])
        assert ok2 == {"B": True}, ok2
        assert ctx.store.state["C1"]["B"] == "102"

        # 驱动重试
        retry_mod._pending["C1"]["101"].next_try = 0
        await retry_mod._process_due("C1")

        assert "101" not in retry_mod._pending.get("C1", {}), retry_mod._pending
        # B 收到过 M1（延迟补上），且游标不回退到 101
        assert ("C1", "101", ["B"]) in ctx.sender.calls, ctx.sender.calls
        assert ctx.store.state["C1"]["B"] == "102", ctx.store.state
        print("[OK] M1 失败→M2 成功→M1 延迟重试补上，游标保持 102")


async def test_retry_backfill_concurrent_no_duplicate():
    # retry 与 backfill 同一时刻处理 (C1,101,B)：只发一次
    initial = {"C1": {"B": "100"}}
    with Ctx(initial, sender_delay=0.05) as ctx:
        async def retry_side():
            return await retry_mod.send_and_record(
                "C1", "101", ["p"], [], ["B"]
            )

        async def backfill_side():
            return await retry_mod.send_and_record(
                "C1", "101", ["p"], [], ["B"]
            )

        results = await asyncio.gather(retry_side(), backfill_side())

        sent = [
            c for c in ctx.sender.calls
            if c[0] == "C1" and c[1] == "101"
        ]
        assert len(sent) == 1, ctx.sender.calls
        ok_values = sorted({m["B"] for m in results})
        assert ok_values == [False, True], results
        assert ctx.store.state["C1"]["B"] == "101", ctx.store.state
        print("[OK] 并发 retry/backfill 同一消息只发送一次")


async def test_cursor_never_regresses():
    cm = {"B": "102"}
    changed = dedup.apply_success(cm, {"B": True}, "101")
    assert cm["B"] == "102" and changed is False
    changed2 = dedup.apply_success(cm, {"B": True}, "103")
    assert cm["B"] == "103" and changed2 is True
    print("[OK] 游标只进不退（101 不回退，103 正常推进）")


async def test_skipped_message_advances_cursor():
    cm = {"B": "100"}
    changed = dedup.apply_skipped(cm, ["B"], "101")
    assert changed is True
    assert cm["B"] == "101"
    changed_again = dedup.apply_skipped(cm, ["B"], "100")
    assert changed_again is False
    assert cm["B"] == "101"
    print("[OK] 无可发送内容的消息推进游标且不回退")


async def test_only_failed_groups_retried():
    initial = {"C1": {"A": "100", "B": "100"}}
    results = {("C1", "101"): [{"B": False}, {}]}
    with Ctx(initial, results) as ctx:
        ok1 = await retry_mod.send_and_record(
            "C1", "101", ["p"], [], ["A", "B"]
        )
        assert ok1 == {"A": True, "B": False}, ok1

        failed = [g for g, ok in ok1.items() if not ok]
        retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            failed, "自动转发来自",
        )
        assert retry_mod._pending["C1"]["101"].groups == {"B"}

        retry_mod._pending["C1"]["101"].next_try = 0
        await retry_mod._process_due("C1")

        # 重试只发给失败的 B（单群调用，且不含 A）
        retry_calls = [
            c for c in ctx.sender.calls
            if c[0] == "C1" and c[1] == "101" and len(c[2]) == 1
        ]
        assert retry_calls and retry_calls[-1][2] == ["B"], ctx.sender.calls
        assert ctx.store.state["C1"]["A"] == "101"
        assert ctx.store.state["C1"]["B"] == "101"
        print("[OK] 多群部分失败只重试失败群 B（不影响 A）")


async def test_busy_retry_groups_do_not_consume_attempts():
    """其他发送路径占用目标群时，重试次数保持不变。"""
    initial = {"C1": {"B": "100"}}
    with Ctx(initial) as ctx:
        retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            ["B"], "自动转发来自",
        )
        entry = retry_mod._pending["C1"]["101"]
        entry.attempts = retry_mod.MAX_RETRY_ATTEMPTS - 1
        entry.next_try = 0
        retry_mod._inflight.add(("C1", "101", "B"))

        before = time.monotonic()
        await retry_mod._process_due("C1")

        assert retry_mod._pending["C1"]["101"] is entry
        assert entry.groups == {"B"}
        assert entry.attempts == retry_mod.MAX_RETRY_ATTEMPTS - 1
        assert entry.next_try > before
        assert ctx.sender.calls == [], ctx.sender.calls
        print("[OK] 并发占用的重试目标不消耗次数且保留队列")


async def test_busy_groups_survive_other_groups_retry_exhaustion():
    """真实失败群耗尽时，并发发送中的群继续保留并重试。"""
    initial = {"C1": {"B": "100", "C": "100"}}
    results = {("C1", "101"): [{"C": False}]}
    with Ctx(initial, results) as ctx:
        retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            ["B", "C"], "自动转发来自",
        )
        entry = retry_mod._pending["C1"]["101"]
        entry.attempts = retry_mod.MAX_RETRY_ATTEMPTS - 1
        entry.next_try = 0
        retry_mod._inflight.add(("C1", "101", "B"))

        await retry_mod._process_due("C1")

        assert retry_mod._pending["C1"]["101"] is entry
        assert entry.groups == {"B"}
        assert entry.attempts == 0
        assert entry.next_try > time.monotonic()
        assert ctx.sender.calls == [("C1", "101", ["C"])], ctx.sender.calls
        print("[OK] 真实失败群耗尽后，并发占用群继续保留重试")


async def test_exhaustion_then_backfill_fallback():
    # 实时 + 3 次重试全部失败 → 队列条目移除、失败群游标不动；
    # backfill 仍能按低游标选中 B 并送达推进
    initial = {"C1": {"B": "100"}}
    results = {("C1", "101"): [{"B": False}] * 4}
    with Ctx(initial, results) as ctx:
        ok1 = await retry_mod.send_and_record("C1", "101", ["p"], [], ["B"])
        assert ok1 == {"B": False}, ok1

        retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            ["B"], "自动转发来自",
        )

        for _ in range(3):
            entry = retry_mod._pending.get("C1", {}).get("101")
            if not entry:
                break
            entry.next_try = 0
            await retry_mod._process_due("C1")

        assert "101" not in retry_mod._pending.get("C1", {}), retry_mod._pending
        assert ctx.store.state["C1"]["B"] == "100", ctx.store.state

        # backfill 兜底：低游标选中 B，送达后推进
        targets = dedup.select_target_groups(
            dedup.normalize_channel_map(copy.deepcopy(ctx.store.state), "C1"),
            ["B"],
            "101",
        )
        assert targets == ["B"], targets
        ok2 = await retry_mod.send_and_record("C1", "101", ["p"], [], targets)
        assert ok2 == {"B": True}, ok2
        assert ctx.store.state["C1"]["B"] == "101", ctx.store.state
        print("[OK] 重试耗尽后 backfill 兜底成功，游标推进到 101")


async def test_normal_success_no_retry():
    initial = {"C1": {"B": "100"}}
    with Ctx(initial) as ctx:
        ok_map = await retry_mod.send_and_record(
            "C1", "101", ["p"], [], ["B"]
        )
        assert ok_map == {"B": True}, ok_map
        assert retry_mod._pending == {}, retry_mod._pending
        assert len(ctx.sender.calls) == 1
        assert ctx.store.state["C1"]["B"] == "101"
        print("[OK] 正常成功无额外重试，仅发送一次")


async def test_retry_backoff_schedule():
    """初次失败和每次重试失败后按 30/60/120 秒退避。"""
    initial = {"C1": {"B": "100"}}
    failures = [{"B": False}, {"B": False}, {"B": False}]
    with Ctx(initial, {("C1", "101"): failures}) as ctx, \
            mock.patch.object(retry_mod, "_save_pending"):
        retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            ["B"], "自动转发来自",
        )
        entry = retry_mod._pending["C1"]["101"]
        assert abs((entry.next_try - time.monotonic()) - 30) < 0.1

        expected_delays = (60, 120)
        for expected in expected_delays:
            entry.next_try = 0
            before = time.monotonic()
            await retry_mod._process_due("C1")
            actual = entry.next_try - before
            assert abs(actual - expected) < 0.1, (actual, expected)

        entry.next_try = 0
        await retry_mod._process_due("C1")
        assert "101" not in retry_mod._pending.get("C1", {})
        assert len(ctx.sender.calls) == 3
        print("[OK] 失败后重试间隔为 30/60/120 秒，第三次重试后耗尽")


async def test_retry_backoff_starts_after_slow_attempt():
    """慢发送失败后，下一次退避仍从失败完成时开始。"""
    initial = {"C1": {"B": "100"}}
    failures = [{"B": False}]
    with Ctx(initial, {("C1", "101"): failures}, sender_delay=0.05) as ctx, \
            mock.patch.object(retry_mod, "_save_pending_async", new=mock.AsyncMock()), \
            mock.patch.object(retry_mod, "RETRY_BACKOFFS", (0.01, 0.04, 0.08)):
        retry_mod.schedule_retry(
            "C1", "101", {"channel_id": "C1", "content": "M1"},
            ["B"], "自动转发来自",
        )
        entry = retry_mod._pending["C1"]["101"]
        entry.next_try = 0

        await retry_mod._process_due("C1")

        remaining = entry.next_try - time.monotonic()
        assert remaining > 0.02, remaining
        assert len(ctx.sender.calls) == 1
        print("[OK] 慢发送失败后仍保留完整的下一次退避间隔")


async def test_pending_recovery_respects_total_cap():
    """恢复旧队列时全局条目数也不能超过当前容量。"""
    saved_pending = retry_mod._pending
    retry_mod._pending = {}
    raw_entries = [
        {
            "channel_id": "C{}".format(channel),
            "message_id": "M{}".format(message),
            "message": {"channel_id": "C{}".format(channel)},
            "groups": ["B"],
            "attempts": 0,
            "next_try_at": 0,
        }
        for channel in range(4)
        for message in range(5)
    ]
    try:
        with mock.patch.object(
            retry_mod,
            "_read_pending",
            return_value={"entries": raw_entries},
        ), mock.patch.object(retry_mod, "MAX_TOTAL_PENDING", 2):
            assert retry_mod._load_pending()
        loaded_count = sum(len(entries) for entries in retry_mod._pending.values())
        assert loaded_count == 2, retry_mod._pending
        print("[OK] 重启恢复应用全局重试队列容量上限")
    finally:
        retry_mod._pending = saved_pending


async def test_nonfinite_retry_deadlines_are_skipped():
    """单条非有限/溢出截止时间不能恢复成永久等待的欠账。"""
    saved_pending = retry_mod._pending
    saved_load_error = retry_mod._pending_load_error
    saved_last_error = retry_mod._last_error
    retry_mod._pending = {}
    retry_mod._pending_load_error = ""
    retry_mod._last_error = ""

    def entry(message_id, deadline):
        return {
            "channel_id": "C1",
            "message_id": message_id,
            "message": {"channel_id": "C1", "content": message_id},
            "groups": ["B"],
            "attempts": 0,
            "next_try_at": deadline,
        }

    raw_entries = [
        entry("valid", 0),
        entry("infinite", float("inf")),
        entry("not-a-number", float("nan")),
        entry("overflow", 10 ** 10000),
        entry("far-future", 1e300),
    ]
    try:
        with mock.patch.object(
            retry_mod, "_read_pending", return_value={"entries": raw_entries}
        ):
            assert retry_mod._load_pending()
        assert set(retry_mod._pending["C1"]) == {"valid", "far-future"}, retry_mod._pending
        remaining = (
            retry_mod._pending["C1"]["far-future"].next_try - time.monotonic()
        )
        assert 119.9 <= remaining <= 120.0, remaining
        assert json.loads('{"deadline": Infinity}')["deadline"] == float("inf")
        print("[OK] 非有限重试期限隔离，超远截止时间限制到最大退避")
    finally:
        retry_mod._pending = saved_pending
        retry_mod._pending_load_error = saved_load_error
        retry_mod._last_error = saved_last_error


async def test_malformed_retry_groups_and_ids_are_skipped():
    """单条 groups 或消息身份结构无效时跳过，仍恢复后续有效欠账。"""
    saved_pending = retry_mod._pending
    saved_load_error = retry_mod._pending_load_error
    saved_last_error = retry_mod._last_error
    retry_mod._pending = {}
    retry_mod._pending_load_error = ""
    retry_mod._last_error = ""

    def entry(message_id, groups, channel_id="C1"):
        return {
            "channel_id": channel_id,
            "message_id": message_id,
            "message": {"channel_id": "C1", "content": message_id},
            "groups": groups,
            "attempts": 0,
            "next_try_at": 0,
        }

    raw_entries = [
        entry("integer-groups", 42),
        entry("string-groups", "GROUP"),
        entry("object-groups", {"B": True}),
        entry("mixed-groups", ["B", 7]),
        entry("integer-channel", ["B"], channel_id=42),
        entry("list-channel", ["B"], channel_id=["C1"]),
        entry(123, ["B"]),
        entry(["message"], ["B"]),
        entry("blank-channel", ["B"], channel_id="  "),
        {
            **entry("blank-message", ["B"]),
            "message_id": " \t ",
        },
        entry("valid-groups", ["B", "B"]),
    ]
    try:
        with mock.patch.object(
            retry_mod, "_read_pending", return_value={"entries": raw_entries}
        ):
            assert retry_mod._load_pending()
        assert set(retry_mod._pending) == {"C1"}, retry_mod._pending
        assert set(retry_mod._pending["C1"]) == {"valid-groups"}, retry_mod._pending
        assert retry_mod._pending["C1"]["valid-groups"].groups == {"B"}
        print("[OK] 损坏的 groups/channel_id/message_id 只跳过对应记录")
    finally:
        retry_mod._pending = saved_pending
        retry_mod._pending_load_error = saved_load_error
        retry_mod._last_error = saved_last_error


async def test_invalid_retry_attempt_counts_are_skipped():
    """恢复时只接受范围内整数尝试次数；旧记录缺省仍从零开始。"""
    saved_pending = retry_mod._pending
    saved_load_error = retry_mod._pending_load_error
    saved_last_error = retry_mod._last_error
    retry_mod._pending = {}
    retry_mod._pending_load_error = ""
    retry_mod._last_error = ""

    def entry(message_id, attempts_marker="present", attempts=0):
        item = {
            "channel_id": "C1",
            "message_id": message_id,
            "message": {"channel_id": "C1", "content": message_id},
            "groups": ["B"],
            "next_try_at": 0,
        }
        if attempts_marker == "present":
            item["attempts"] = attempts
        return item

    raw_entries = [
        entry("boolean", attempts=True),
        entry("fraction", attempts=1.5),
        entry("numeric-string", attempts="2"),
        entry("negative", attempts=-1),
        entry("exhausted", attempts=retry_mod.MAX_RETRY_ATTEMPTS),
        entry("zero", attempts=0),
        entry("last-remaining", attempts=retry_mod.MAX_RETRY_ATTEMPTS - 1),
        entry("legacy-default", attempts_marker="missing"),
    ]
    try:
        with mock.patch.object(
            retry_mod, "_read_pending", return_value={"entries": raw_entries}
        ):
            assert retry_mod._load_pending()
        restored = retry_mod._pending["C1"]
        assert set(restored) == {"zero", "last-remaining", "legacy-default"}, restored
        assert restored["zero"].attempts == 0
        assert restored["last-remaining"].attempts == retry_mod.MAX_RETRY_ATTEMPTS - 1
        assert restored["legacy-default"].attempts == 0
        print("[OK] 重试次数只恢复合法范围整数，旧记录缺省按零处理")
    finally:
        retry_mod._pending = saved_pending
        retry_mod._pending_load_error = saved_load_error
        retry_mod._last_error = saved_last_error


async def test_corrupt_retry_file_is_preserved():
    """顶层队列损坏时不覆盖原文件，新失败交给离线补发兜底。"""
    saved_pending = retry_mod._pending
    saved_load_error = retry_mod._pending_load_error
    saved_last_error = retry_mod._last_error
    saved_loop_auto_start = retry_mod._LOOP_AUTO_START
    retry_mod._pending = {}
    retry_mod._pending_load_error = ""
    retry_mod._last_error = ""
    retry_mod._LOOP_AUTO_START = False
    raw_entry = {
        "channel_id": "C1",
        "message_id": "101",
        "message": {"channel_id": "C1", "content": "M1"},
        "groups": ["B"],
        "attempts": 0,
        "next_try_at": time.time() + 30,
    }
    parse_error = json.JSONDecodeError("invalid JSON", "{", 1)
    try:
        with mock.patch.object(
            retry_mod,
            "_read_pending",
            side_effect=[parse_error, parse_error, {"entries": [raw_entry]}],
        ), mock.patch.object(retry_mod, "atomic_write_json") as write_file:
            assert not retry_mod._load_pending()
            assert retry_mod._pending_load_error
            assert not retry_mod.schedule_retry(
                "C1", "102", {"channel_id": "C1", "content": "M2"}, ["B"]
            )
            assert retry_mod._pending == {}
            retry_mod._save_pending()
            write_file.assert_not_called()

            assert retry_mod._load_pending()
            assert "101" in retry_mod._pending["C1"]
            assert not retry_mod._pending_load_error
        print("[OK] 损坏重试队列保留原文件，修复后重新加载")
    finally:
        retry_mod._pending = saved_pending
        retry_mod._pending_load_error = saved_load_error
        retry_mod._last_error = saved_last_error
        retry_mod._LOOP_AUTO_START = saved_loop_auto_start


async def test_retry_loop_wakes_on_new_pending():
    """等待较远到期时间时，新欠账应立即唤醒循环。"""
    retry_mod._pending.clear()
    retry_mod._retry_loops.clear()
    retry_mod._retry_tasks.clear()
    retry_mod._retry_wakeups.clear()
    retry_mod._LOOP_AUTO_START = True
    calls = []

    async def fake_process_due(channel_id):
        calls.append(channel_id)
        if len(calls) >= 2:
            retry_mod._pending.pop(channel_id, None)

    future_entry = retry_mod._RetryEntry(
        {"channel_id": "C1", "content": "M1"},
        "自动转发来自",
        ["B"],
        asyncio.get_running_loop().time() + 60,
    )
    retry_mod._pending["C1"] = {"101": future_entry}
    with mock.patch.object(retry_mod, "_process_due", side_effect=fake_process_due), \
            mock.patch.object(retry_mod, "_save_pending"):
        retry_mod._ensure_loop("C1")
        await asyncio.sleep(0.02)
        assert calls == ["C1"], calls

        retry_mod.schedule_retry(
            "C1", "102", {"channel_id": "C1", "content": "M2"}, ["B"]
        )
        await asyncio.wait_for(
            asyncio.gather(*list(retry_mod._retry_tasks)),
            timeout=1,
        )

    assert calls == ["C1", "C1"], calls
    assert retry_mod._retry_loops == set(), retry_mod._retry_loops
    assert retry_mod._retry_tasks == set(), retry_mod._retry_tasks
    assert retry_mod._retry_wakeups == {}, retry_mod._retry_wakeups
    retry_mod._LOOP_AUTO_START = True
    print("[OK] 新欠账唤醒重试循环，完成后清理任务状态")


async def test_retry_send_exception_keeps_loop_alive():
    """发送适配层异常应计为失败并继续按退避处理队列。"""
    retry_mod._pending.clear()
    retry_mod._retry_loops.clear()
    retry_mod._retry_tasks.clear()
    retry_mod._retry_wakeups.clear()
    retry_mod._LOOP_AUTO_START = True
    calls = []

    async def flaky_send(channel_id, message_id, text_parts, media_items, groups):
        calls.append(message_id)
        if len(calls) == 1:
            raise RuntimeError("temporary sender failure")
        retry_mod._remove_entry(channel_id, message_id)
        return {group: True for group in groups}

    try:
        with mock.patch.object(retry_mod, "prepare_discord_message", fake_prepare_message), \
                mock.patch.object(retry_mod, "send_and_record", side_effect=flaky_send), \
                mock.patch.object(retry_mod, "_save_pending"), \
                mock.patch.object(retry_mod, "RETRY_BACKOFFS", (0.01, 0.01, 0.01)):
            retry_mod.schedule_retry(
                "C1", "101", {"channel_id": "C1", "content": "M1"}, ["B"]
            )
            await asyncio.wait_for(
                asyncio.gather(*list(retry_mod._retry_tasks)),
                timeout=1,
            )

        assert calls == ["101", "101"], calls
        assert "C1" not in retry_mod._pending, retry_mod._pending
        assert retry_mod._retry_loops == set(), retry_mod._retry_loops
        assert retry_mod._retry_tasks == set(), retry_mod._retry_tasks
        assert retry_mod._retry_wakeups == {}, retry_mod._retry_wakeups
        print("[OK] 发送异常按失败退避，重试队列工作任务保持运行")
    finally:
        retry_mod._pending.clear()
        retry_mod._retry_loops.clear()
        retry_mod._retry_tasks.clear()
        retry_mod._retry_wakeups.clear()
        retry_mod._LOOP_AUTO_START = True


async def test_existing_retry_restarts_missing_worker():
    """重复登记仍在队列的消息时应补拉缺失的频道任务。"""
    retry_mod._pending.clear()
    retry_mod._retry_loops.clear()
    retry_mod._retry_tasks.clear()
    retry_mod._retry_wakeups.clear()
    retry_mod._LOOP_AUTO_START = True

    async def finish_channel(channel_id):
        retry_mod._pending.pop(channel_id, None)

    future = time.monotonic() + 60
    retry_mod._pending["C1"] = {
        "101": retry_mod._RetryEntry(
            {"channel_id": "C1", "content": "M1"},
            "自动转发来自",
            ["B"],
            future,
        )
    }
    try:
        with mock.patch.object(retry_mod, "_process_due", side_effect=finish_channel), \
                mock.patch.object(retry_mod, "_save_pending"):
            retry_mod.schedule_retry(
                "C1", "101", {"channel_id": "C1", "content": "M1"}, ["B"]
            )
            assert len(retry_mod._retry_tasks) == 1, retry_mod._retry_tasks
            await asyncio.wait_for(
                asyncio.gather(*list(retry_mod._retry_tasks)),
                timeout=1,
            )

        assert "C1" not in retry_mod._pending, retry_mod._pending
        assert retry_mod._retry_loops == set(), retry_mod._retry_loops
        assert retry_mod._retry_tasks == set(), retry_mod._retry_tasks
        assert retry_mod._retry_wakeups == {}, retry_mod._retry_wakeups
        print("[OK] 已有欠账重新登记会拉起缺失的频道重试任务")
    finally:
        retry_mod._pending.clear()
        retry_mod._retry_loops.clear()
        retry_mod._retry_tasks.clear()
        retry_mod._retry_wakeups.clear()
        retry_mod._LOOP_AUTO_START = True


async def test_unexpected_retry_processing_exception_keeps_worker_alive():
    """Unexpected _process_due errors must preserve entries and retry later."""
    retry_mod._pending.clear()
    retry_mod._retry_loops.clear()
    retry_mod._retry_tasks.clear()
    retry_mod._retry_wakeups.clear()
    retry_mod._LOOP_AUTO_START = True
    calls = []

    async def flaky_process(channel_id):
        calls.append(channel_id)
        if len(calls) == 1:
            raise RuntimeError("temporary retry processing failure")
        retry_mod._pending.pop(channel_id, None)

    retry_mod._pending["C1"] = {
        "101": retry_mod._RetryEntry(
            {"channel_id": "C1", "content": "M1"},
            "自动转发来自",
            ["B"],
            time.monotonic(),
        )
    }
    try:
        with mock.patch.object(retry_mod, "_process_due", side_effect=flaky_process), \
                mock.patch.object(retry_mod, "_save_pending"), \
                mock.patch.object(retry_mod, "RETRY_BACKOFFS", (0.01, 0.01, 0.01)):
            retry_mod._ensure_loop("C1")
            await asyncio.wait_for(
                asyncio.gather(*list(retry_mod._retry_tasks)),
                timeout=1,
            )

        assert calls == ["C1", "C1"], calls
        assert "C1" not in retry_mod._pending, retry_mod._pending
        assert retry_mod._retry_loops == set(), retry_mod._retry_loops
        assert retry_mod._retry_tasks == set(), retry_mod._retry_tasks
        assert retry_mod._retry_wakeups == {}, retry_mod._retry_wakeups
        print("[OK] 重试处理异常保留队列并恢复频道 worker")
    finally:
        retry_mod._pending.clear()
        retry_mod._retry_loops.clear()
        retry_mod._retry_tasks.clear()
        retry_mod._retry_wakeups.clear()
        retry_mod._LOOP_AUTO_START = True


async def test_retry_shutdown_stops_workers_and_preserves_pending():
    saved_stopping = retry_mod._retry_stopping
    saved_pending = retry_mod._pending
    saved_loops = set(retry_mod._retry_loops)
    saved_tasks = set(retry_mod._retry_tasks)
    saved_wakeups = dict(retry_mod._retry_wakeups)
    saved_loop_auto_start = retry_mod._LOOP_AUTO_START
    retry_mod._retry_stopping = False
    retry_mod._pending = {
        "C1": {
            "101": retry_mod._RetryEntry(
                {"channel_id": "C1", "content": "M1"},
                "自动转发来自",
                ["B"],
                time.monotonic() + 60,
            )
        }
    }
    retry_mod._retry_loops.clear()
    retry_mod._retry_tasks.clear()
    retry_mod._retry_wakeups.clear()
    retry_mod._LOOP_AUTO_START = True
    started = asyncio.Event()

    async def observe_worker(channel_id):
        started.set()

    try:
        with mock.patch.object(retry_mod, "_process_due", side_effect=observe_worker):
            retry_mod._ensure_loop("C1")
            await asyncio.wait_for(started.wait(), timeout=1)
            tasks = list(retry_mod._retry_tasks)
            assert len(tasks) == 1

            await retry_mod._stop_retry_loops()

            assert all(task.done() for task in tasks)
            assert retry_mod._retry_tasks == set()
            assert retry_mod._retry_loops == set()
            assert retry_mod._retry_wakeups == {}
            assert "101" in retry_mod._pending["C1"]
            retry_mod._ensure_loop("C1")
            assert retry_mod._retry_tasks == set()
        print("[OK] 关闭时停止重试 worker 并保留待处理队列")
    finally:
        retry_mod._retry_stopping = saved_stopping
        retry_mod._pending = saved_pending
        retry_mod._retry_loops.clear()
        retry_mod._retry_loops.update(saved_loops)
        retry_mod._retry_tasks.clear()
        retry_mod._retry_tasks.update(saved_tasks)
        retry_mod._retry_wakeups.clear()
        retry_mod._retry_wakeups.update(saved_wakeups)
        retry_mod._LOOP_AUTO_START = saved_loop_auto_start


async def main():
    await test_m1_fail_m2_ok_m1_retry_not_lost()
    await test_retry_backfill_concurrent_no_duplicate()
    await test_cursor_never_regresses()
    await test_skipped_message_advances_cursor()
    await test_only_failed_groups_retried()
    await test_busy_retry_groups_do_not_consume_attempts()
    await test_busy_groups_survive_other_groups_retry_exhaustion()
    await test_exhaustion_then_backfill_fallback()
    await test_normal_success_no_retry()
    await test_retry_backoff_schedule()
    await test_retry_backoff_starts_after_slow_attempt()
    await test_pending_recovery_respects_total_cap()
    await test_nonfinite_retry_deadlines_are_skipped()
    await test_malformed_retry_groups_and_ids_are_skipped()
    await test_invalid_retry_attempt_counts_are_skipped()
    await test_corrupt_retry_file_is_preserved()
    await test_retry_loop_wakes_on_new_pending()
    await test_retry_send_exception_keeps_loop_alive()
    await test_existing_retry_restarts_missing_worker()
    await test_unexpected_retry_processing_exception_keeps_worker_alive()
    await test_retry_shutdown_stops_workers_and_preserves_pending()
    print("\n全部通过")


if __name__ == "__main__":
    asyncio.run(main())
