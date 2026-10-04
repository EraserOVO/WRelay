import asyncio
import copy
import json
import math
import time

from nonebot import get_driver, logger

from plugins.dedup import (
    apply_success,
    apply_skipped,
    normalize_channel_map,
)
from plugins.forward_service import load_channel_filter, prepare_discord_message
from plugins.history import (
    load_last_messages,
    update_last_messages,
)
from plugins.delivery_dedup import (
    delivered_groups as get_cross_source_delivered_groups,
    record_delivered_groups as save_cross_source_deliveries,
)
from plugins.sender import send_relay_message
from plugins.translation import translate_text_parts
from plugins.json_io import atomic_write_json, run_in_thread_to_completion
from plugins.paths import data_path


# =====================================================
# 转发失败延迟重试（欠账队列 + 在途注册表）
#
# 目标：发送失败的消息不再因后续消息成功而永久丢失。
#   - pending 队列记录"谁还欠哪条消息"（与成功游标解耦）；
#     失败群入队，任何一路径成功送达都会清除对应欠账（_purge_delivered）
#   - in-flight 注册表保证同一 (频道, 消息, 群) 同时只有一个发送者；
#     X/Bilibili RSS 可用平台投稿 ID 把判重范围扩到跨来源的同一目标群
#   - 队列持久化为 data/retry_pending.json（只存规范化消息，不缓存媒体字节），
#     进程重启后恢复未完成条目；耗尽后仍由 backfill 按低游标兜底
# =====================================================

RETRY_BACKOFFS = (30.0, 60.0, 120.0)  # 第 1/2/3 次重试前等待秒数
MAX_RETRY_ATTEMPTS = 3
MAX_TOTAL_PENDING = 10
MAX_PER_CHANNEL_PENDING = 5
RETRY_FILE = data_path("retry_pending.json")

# 测试可关闭：登记失败时自动拉起频道重试循环，改由测试手动驱动
_LOOP_AUTO_START = True

_pending = {}        # channel_id -> {message_id: _RetryEntry}
_inflight = set()    # {(channel_id, message_id, group_openid)}
_retry_loops = set()  # 已拉起的频道重试循环（防重复拉起）
_retry_tasks = set()  # 持有任务引用防 GC
_retry_wakeups = {}  # channel_id -> asyncio.Event，通知队列变化
_retry_stopping = False
_pending_save_lock = asyncio.Lock()
_pending_load_lock = asyncio.Lock()
_last_error = ""
_pending_load_error = ""
_RETRY_FILE_MISSING = object()


class _RetryEntry:
    """一条待重试消息：内容重建所需的规范化 dict + 欠账群"""

    __slots__ = ("message", "source_label", "groups", "attempts", "next_try")

    def __init__(self, message, source_label, groups, next_try):
        self.message = message
        self.source_label = source_label
        self.groups = set(groups)
        self.attempts = 0
        self.next_try = next_try


class SendResult(dict):
    """Per-group delivery map with metadata for suppressed and in-flight work."""

    def __init__(
        self,
        values=(),
        *,
        sent_groups=(),
        deduplicated_groups=(),
        busy_groups=(),
    ):
        super().__init__(values)
        self.sent_groups = frozenset(sent_groups)
        self.deduplicated_groups = frozenset(deduplicated_groups)
        self.busy_groups = frozenset(busy_groups)


def _read_pending():
    try:
        with open(RETRY_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return _RETRY_FILE_MISSING


def _pending_snapshot():
    entries = []
    for channel_id, channel_entries in _pending.items():
        for message_id, entry in channel_entries.items():
            entries.append({
                "channel_id": str(channel_id),
                "message_id": str(message_id),
                "message": copy.deepcopy(entry.message),
                "source_label": entry.source_label,
                "groups": sorted(entry.groups),
                "attempts": entry.attempts,
                # monotonic 只在当前进程有效，跨进程改存 wall-clock 时间。
                "next_try_at": time.time() + max(0.0, entry.next_try - time.monotonic()),
            })
    return {"version": 1, "last_error": _last_error, "entries": entries}


def _save_pending_snapshot(snapshot):
    """Write an event-loop snapshot to disk; failures leave memory retryable."""
    if _pending_load_error:
        return False
    try:
        atomic_write_json(
            RETRY_FILE,
            snapshot,
            indent=2,
        )
        return True
    except Exception as exc:
        logger.warning("保存延迟重试队列失败: {}", exc)
        return False


def _save_pending():
    """把待重试队列同步写入磁盘；供同步兼容调用和测试使用。"""
    if _pending_load_error:
        return False
    return _save_pending_snapshot(_pending_snapshot())


async def _save_pending_async():
    """Serialize queue changes in order, writing the captured snapshot off-loop."""
    if _pending_load_error:
        return False
    async with _pending_save_lock:
        snapshot = _pending_snapshot()
        return await run_in_thread_to_completion(_save_pending_snapshot, snapshot)


def _record_pending_load_error(exc):
    global _last_error, _pending_load_error
    error = "延迟重试队列读取失败：{}".format(exc)
    if error != _pending_load_error:
        logger.error("{}；保留原文件并将新欠账交给离线补发", error)
    _pending_load_error = error
    _last_error = error
    return False


def _restore_pending_data(data):
    """Apply a decoded retry queue; malformed individual entries are skipped."""
    global _last_error, _pending_load_error

    if data is _RETRY_FILE_MISSING:
        data = {"entries": []}
    elif not isinstance(data, dict):
        error = "延迟重试队列格式无效：顶层 JSON 必须是对象"
        if error != _pending_load_error:
            logger.error("{}；保留原文件并将新欠账交给离线补发", error)
        _pending_load_error = error
        _last_error = error
        return False
    if not isinstance(data.get("entries"), list):
        error = "延迟重试队列格式无效：entries 必须是列表"
        if error != _pending_load_error:
            logger.error("{}；保留原文件并将新欠账交给离线补发", error)
        _pending_load_error = error
        _last_error = error
        return False

    _pending_load_error = ""
    _last_error = str(data.get("last_error") or "")
    raw_entries = data.get("entries")
    loaded_count = 0
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        channel_id = raw.get("channel_id")
        message_id = raw.get("message_id")
        message = raw.get("message")
        raw_groups = raw.get("groups")
        if (
            not isinstance(channel_id, str)
            or not channel_id.strip()
            or not isinstance(message_id, str)
            or not message_id.strip()
        ):
            continue
        channel_id = channel_id.strip()
        message_id = message_id.strip()
        if not isinstance(raw_groups, list) or not raw_groups:
            continue
        if any(not isinstance(group, str) or not group.strip() for group in raw_groups):
            continue
        groups = list(dict.fromkeys(raw_groups))
        if not isinstance(message, dict) or not groups:
            continue
        if loaded_count >= MAX_TOTAL_PENDING:
            break
        if len(_pending.get(channel_id) or {}) >= MAX_PER_CHANNEL_PENDING:
            continue
        attempts = raw.get("attempts", 0)
        if (
            type(attempts) is not int
            or attempts < 0
            or attempts >= MAX_RETRY_ATTEMPTS
        ):
            continue
        try:
            next_try_at = float(raw.get("next_try_at"))
            if not math.isfinite(next_try_at):
                continue
            remaining_delay = min(
                RETRY_BACKOFFS[-1],
                max(0.0, next_try_at - time.time()),
            )
        except (OverflowError, TypeError, ValueError):
            continue
        entry = _RetryEntry(
            message,
            str(raw.get("source_label") or "自动转发来自"),
            groups,
            time.monotonic() + remaining_delay,
        )
        entry.attempts = attempts
        _pending.setdefault(channel_id, {})[message_id] = entry
        loaded_count += 1
    return True


def _load_pending():
    """Restore persisted retries synchronously for import/compatibility paths."""
    try:
        data = _read_pending()
    except (OSError, ValueError) as exc:
        return _record_pending_load_error(exc)
    return _restore_pending_data(data)


async def _load_pending_async():
    """Read retry JSON off-loop, then apply the bounded queue on the loop."""
    try:
        data = await run_in_thread_to_completion(_read_pending)
    except (OSError, ValueError) as exc:
        return _record_pending_load_error(exc)
    return _restore_pending_data(data)


_load_pending()


def _claim(channel_id, message_id, group_openid):
    """在途占用（同步、无 await，天然原子）"""
    key = (channel_id, message_id, group_openid)
    if key in _inflight:
        return False
    _inflight.add(key)
    return True


def _release(channel_id, message_id, group_openid):
    _inflight.discard((channel_id, message_id, group_openid))


def _remove_entry(channel_id, message_id):
    entries = _pending.get(channel_id)
    if not entries:
        return
    entries.pop(message_id, None)
    if not entries:
        _pending.pop(channel_id, None)
    wakeup = _retry_wakeups.get(channel_id)
    if wakeup is not None:
        wakeup.set()


def _purge_delivered_from_memory(channel_id, message_id, success_groups):
    entry = (_pending.get(channel_id) or {}).get(message_id)
    if not entry:
        return False
    for group in success_groups:
        entry.groups.discard(group)
    if not entry.groups:
        _remove_entry(channel_id, message_id)
    return True


def _purge_delivered(channel_id, message_id, success_groups):
    """某群已收到该消息（无论哪条路径送达），清除其欠账。"""
    if _purge_delivered_from_memory(channel_id, message_id, success_groups):
        _save_pending()


async def _purge_delivered_async(channel_id, message_id, success_groups):
    if _purge_delivered_from_memory(channel_id, message_id, success_groups):
        await _save_pending_async()


def _register_retry(
    channel_id,
    message_id,
    message,
    failed_groups,
    source_label="自动转发来自",
    retry_load=True,
):
    """Update in-memory retry state and return (created, changed, channel)."""
    global _last_error
    groups = [g for g in failed_groups if g]
    if not groups:
        return False, False, None

    if _pending_load_error:
        if retry_load:
            _load_pending()
        if _pending_load_error:
            return False, False, None

    entry = (_pending.get(channel_id) or {}).get(message_id)

    if entry:
        entry.groups.update(groups)
        return False, True, channel_id

    total = sum(len(ch) for ch in _pending.values())
    per_channel = len(_pending.get(channel_id) or {})
    if total >= MAX_TOTAL_PENDING or per_channel >= MAX_PER_CHANNEL_PENDING:
        _last_error = "重试队列已满，消息将由离线补发兜底"
        logger.warning(
            "延迟重试队列已满，丢弃 频道{} 消息{}（等待重连补发）",
            channel_id,
            message_id,
        )
        return False, False, None

    _pending.setdefault(channel_id, {})[message_id] = _RetryEntry(
        message,
        source_label,
        groups,
        time.monotonic() + RETRY_BACKOFFS[0],
    )
    return True, True, channel_id


def schedule_retry(
    channel_id,
    message_id,
    message,
    failed_groups,
    source_label="自动转发来自",
):
    """Synchronously register a retry; retained for synchronous callers/tests."""
    created, changed, loop_channel = _register_retry(
        channel_id,
        message_id,
        message,
        failed_groups,
        source_label,
    )
    if changed:
        _save_pending()
    if loop_channel is not None and _LOOP_AUTO_START:
        _ensure_loop(loop_channel)
    return created


async def schedule_retry_async(
    channel_id,
    message_id,
    message,
    failed_groups,
    source_label="自动转发来自",
):
    """Register a retry and await its durable queue snapshot off-loop."""
    if _pending_load_error:
        async with _pending_load_lock:
            if _pending_load_error:
                await _load_pending_async()

    created, changed, loop_channel = _register_retry(
        channel_id,
        message_id,
        message,
        failed_groups,
        source_label,
        retry_load=False,
    )
    if changed:
        await _save_pending_async()
    if loop_channel is not None and _LOOP_AUTO_START:
        _ensure_loop(loop_channel)
    return created


def _ensure_loop(channel_id):
    if not _LOOP_AUTO_START or _retry_stopping:
        return
    wakeup = _retry_wakeups.setdefault(channel_id, asyncio.Event())
    if channel_id in _retry_loops:
        wakeup.set()
        return
    _retry_loops.add(channel_id)
    task = asyncio.create_task(_loop_wrapper(channel_id))
    _retry_tasks.add(task)
    task.add_done_callback(_retry_tasks.discard)


@get_driver().on_startup
async def _resume_pending_retries():
    """Bot 启动后恢复持久化队列的重试循环。"""
    for channel_id in list(_pending):
        _ensure_loop(channel_id)


@get_driver().on_shutdown
async def _stop_retry_loops():
    """Stop retry workers while leaving durable pending entries for restart."""
    global _retry_stopping
    _retry_stopping = True
    tasks = list(_retry_tasks)
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _retry_tasks.clear()
    _retry_loops.clear()
    _retry_wakeups.clear()


async def _loop_wrapper(channel_id):
    try:
        await _channel_retry_loop(channel_id)
    finally:
        _retry_loops.discard(channel_id)
        _retry_wakeups.pop(channel_id, None)


async def _channel_retry_loop(channel_id):
    wakeup = _retry_wakeups.setdefault(channel_id, asyncio.Event())
    while _pending.get(channel_id):
        try:
            await _process_due(channel_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            global _last_error
            logger.exception(
                "延迟重试频道循环异常，保留队列并按退避继续: {}",
                channel_id,
            )
            _last_error = "频道{} 重试处理异常，队列已保留并安排再次尝试".format(
                channel_id
            )
            retry_at = time.monotonic() + RETRY_BACKOFFS[0]
            for entry in (_pending.get(channel_id) or {}).values():
                if entry.next_try <= time.monotonic():
                    entry.next_try = retry_at
            await _save_pending_async()
        if not _pending.get(channel_id):
            break

        # 只等待最近一条欠账到期，新增欠账或已有欠账被清除时由 Event 提前唤醒。
        wakeup.clear()
        entries = _pending.get(channel_id) or {}
        if not entries:
            break
        delay = min(
            max(0.0, entry.next_try - time.monotonic())
            for entry in entries.values()
        )
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


async def _update_retry_after_send(
    channel_id,
    message_id,
    entries,
    requested_groups,
    ok_map,
):
    """根据逐群投递结果更新队列、重试次数与下次退避时间。"""
    entry = entries.get(message_id)
    if entry is None:
        logger.success(
            "延迟重试成功已补发 频道{} 消息{}",
            channel_id,
            message_id,
        )
        return

    busy_groups = set(getattr(ok_map, "busy_groups", ()))
    deduplicated_groups = set(
        getattr(ok_map, "deduplicated_groups", ())
    )
    pending_groups = set(entry.groups)
    pending_attempted_groups = pending_groups & (
        requested_groups - busy_groups - deduplicated_groups
    )
    if busy_groups and not pending_attempted_groups:
        entry.next_try = time.monotonic() + RETRY_BACKOFFS[
            min(entry.attempts, len(RETRY_BACKOFFS) - 1)
        ]
        logger.debug(
            "延迟重试目标群均在其他路径发送中，保留重试次数 频道{} 消息{} 群{}",
            channel_id,
            message_id,
            sorted(pending_groups),
        )
        await _save_pending_async()
        return

    entry.attempts += 1

    if entry.attempts >= MAX_RETRY_ATTEMPTS:
        still_busy = pending_groups & busy_groups
        if still_busy:
            exhausted_groups = pending_groups - still_busy
            entry.groups = still_busy
            entry.attempts = 0
            entry.next_try = time.monotonic() + RETRY_BACKOFFS[0]
            logger.error(
                "延迟重试耗尽群{}；并发发送中的群{}保留队列 频道{} 消息{}",
                sorted(exhausted_groups),
                sorted(still_busy),
                channel_id,
                message_id,
            )
            await _save_pending_async()
            return
        logger.error(
            "延迟重试已达上限({}次)放弃，等待重连补发 频道{} 消息{} 群{}",
            MAX_RETRY_ATTEMPTS,
            channel_id,
            message_id,
            sorted(entry.groups),
        )
        _remove_entry(channel_id, message_id)
        await _save_pending_async()
        return

    # 发送耗时不应抵扣本次发送完成后的退避间隔。
    entry.next_try = time.monotonic() + RETRY_BACKOFFS[entry.attempts]
    await _save_pending_async()


async def _process_due(channel_id):
    """处理该频道所有到期的待重试条目（重试循环与测试共用）"""
    entries = _pending.get(channel_id)
    if not entries:
        return

    global _last_error
    now = time.monotonic()

    for message_id in list(entries):
        entry = entries.get(message_id)
        if (
            entry is None
            or not entry.groups
            or entry.next_try > now
        ):
            continue
        requested_groups = set(entry.groups)

        logger.info(
            "延迟重试发送(第{}次) 频道{} 消息{} 群{}",
            entry.attempts + 1,
            channel_id,
            message_id,
            sorted(entry.groups),
        )

        try:
            # 消息类型筛选：与实时转发/补发共用同一渲染逻辑，
            # 按内容组件过滤（重试时读当前配置，改开关立即生效）
            rendered = await prepare_discord_message(
                entry.message,
                channel_id,
                message_id,
                source_label=entry.source_label,
                filter_config=await load_channel_filter(channel_id),
            )
        except Exception:
            _last_error = f"频道{channel_id} 消息{message_id} 内容重建失败"
            logger.warning(
                "延迟重试内容重建失败，放弃 频道{} 消息{}",
                channel_id,
                message_id,
            )
            _remove_entry(channel_id, message_id)
            await _save_pending_async()
            continue

        if rendered is None:
            logger.warning(
                "延迟重试无可发送内容，放弃 频道{} 消息{}",
                channel_id,
                message_id,
            )
            await update_last_messages(
                lambda last: apply_skipped(
                    normalize_channel_map(last, channel_id),
                    list(entry.groups),
                    message_id,
                )
            )
            _remove_entry(channel_id, message_id)
            await _save_pending_async()
            continue

        text_parts, media_items = rendered

        try:
            ok_map = await send_and_record(
                channel_id,
                message_id,
                text_parts,
                media_items,
                list(entry.groups),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "延迟重试发送异常，按失败处理 频道{} 消息{}",
                channel_id,
                message_id,
            )
            ok_map = {group: False for group in entry.groups}

        # send_and_record may already have removed fully delivered entries.
        await _update_retry_after_send(
            channel_id,
            message_id,
            entries,
            requested_groups,
            ok_map,
        )


def get_retry_status():
    """返回供健康接口/管理面板展示的重试队列摘要，不包含消息正文。"""
    pending_entries = sum(len(entries) for entries in _pending.values())
    pending_groups = sum(
        len(entry.groups)
        for entries in _pending.values()
        for entry in entries.values()
    )
    return {
        "pending_entries": pending_entries,
        "pending_groups": pending_groups,
        "channels": len(_pending),
        "max_entries": MAX_TOTAL_PENDING,
        "last_error": _last_error,
    }


async def send_and_record(
    channel_id,
    message_id,
    text_parts,
    media_items,
    groups,
    idempotency_key=None,
):
    """统一发送入口（relay / backfill / retry 共用）：
    在途判重 → 发送 → 记录来源游标和可选的跨来源幂等键 → 清除欠账。

    返回 {group_openid: 是否送达}。同一 (频道, 消息, 群) 同时只允许
    一个发送者：占用失败（他人正在发送）的群记为未送达，由调用方
    登记延迟重试，成功路径的 purge 会清除对应欠账。X/Bilibili RSS 可额外
    传入平台投稿 ID，避免同一内容由多个来源命中同一群时重复发送。"""
    idempotency_key = str(idempotency_key or "").strip() or None
    # Claim before the sender's final defensive dedupe so repeated targets
    # cannot make the same group look busy against its first claim.
    groups = list(dict.fromkeys(groups))
    deduplicated_groups = (
        await get_cross_source_delivered_groups(idempotency_key, groups)
        if idempotency_key
        else set()
    )
    ok_map = {
        group: group in deduplicated_groups
        for group in groups
    }
    claim_channel = (
        "__delivery_dedup__" if idempotency_key else channel_id
    )
    claim_message = idempotency_key or message_id
    claimable = []
    busy_groups = set()
    for group in groups:
        if group in deduplicated_groups:
            continue
        if _claim(claim_channel, claim_message, group):
            claimable.append(group)
        else:
            busy_groups.add(group)

    if not claimable:
        if deduplicated_groups:
            await update_last_messages(
                lambda last: apply_success(
                    normalize_channel_map(last, channel_id),
                    {group: True for group in deduplicated_groups},
                    message_id,
                )
            )
            await _purge_delivered_async(
                channel_id,
                message_id,
                deduplicated_groups,
            )
        return SendResult(
            ok_map,
            deduplicated_groups=deduplicated_groups,
            busy_groups=busy_groups,
        )

    try:
        sent_map = await send_relay_message(
            text_parts,
            media_items,
            claimable,
            channel_id=channel_id,
            message_id=message_id,
        )
        for group in claimable:
            ok_map[group] = bool(sent_map.get(group))

        success_groups = [
            group
            for group in claimable
            if ok_map[group]
        ]

        if success_groups:
            if idempotency_key:
                try:
                    await save_cross_source_deliveries(
                        idempotency_key,
                        success_groups,
                    )
                except Exception as exc:
                    logger.warning(
                        "跨来源投递去重记录保存失败，来源游标仍将正常更新: {}: {}",
                        idempotency_key,
                        exc,
                    )
            await update_last_messages(
                lambda last: apply_success(
                    normalize_channel_map(last, channel_id),
                    {
                        group: True
                        for group in (*success_groups, *deduplicated_groups)
                    },
                    message_id,
                )
            )
            await _purge_delivered_async(
                channel_id,
                message_id,
                [*success_groups, *deduplicated_groups],
            )
        elif deduplicated_groups:
            await update_last_messages(
                lambda last: apply_success(
                    normalize_channel_map(last, channel_id),
                    {group: True for group in deduplicated_groups},
                    message_id,
                )
            )
            await _purge_delivered_async(
                channel_id,
                message_id,
                deduplicated_groups,
            )

        return SendResult(
            ok_map,
            sent_groups=success_groups,
            deduplicated_groups=deduplicated_groups,
            busy_groups=busy_groups,
        )

    finally:
        for group in claimable:
            _release(claim_channel, claim_message, group)
