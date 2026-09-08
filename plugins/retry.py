import asyncio
import time

from nonebot import get_driver, logger

from plugins.config import get_channel_filter
from plugins.dedup import (
    apply_success,
    apply_skipped,
    normalize_channel_map,
)
from plugins.fetch import build_parts
from plugins.filter import get_enabled_message_types
from plugins.history import (
    load_last_messages,
    update_last_messages,
)
from plugins.sender import send_relay_message
from plugins.json_io import atomic_write_json, load_json
from plugins.paths import data_path


# =====================================================
# 转发失败延迟重试（欠账队列 + 在途注册表）
#
# 目标：发送失败的消息不再因后续消息成功而永久丢失。
#   - pending 队列记录"谁还欠哪条消息"（与成功游标解耦）；
#     失败群入队，任何一路径成功送达都会清除对应欠账（_purge_delivered）
#   - in-flight 注册表保证同一 (频道, 消息, 群) 同时只有一个发送者，
#     retry / relay / backfill 统一走 send_and_record 判重后发送
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
_last_error = ""


class _RetryEntry:
    """一条待重试消息：内容重建所需的规范化 dict + 欠账群"""

    __slots__ = ("message", "source_label", "groups", "attempts", "next_try")

    def __init__(self, message, source_label, groups, next_try):
        self.message = message
        self.source_label = source_label
        self.groups = set(groups)
        self.attempts = 0
        self.next_try = next_try


def _save_pending():
    """把待重试队列写入磁盘；失败不影响内存重试。"""
    entries = []
    for channel_id, channel_entries in _pending.items():
        for message_id, entry in channel_entries.items():
            entries.append({
                "channel_id": str(channel_id),
                "message_id": str(message_id),
                "message": entry.message,
                "source_label": entry.source_label,
                "groups": sorted(entry.groups),
                "attempts": entry.attempts,
                # monotonic 只在当前进程有效，跨进程改存 wall-clock 时间。
                "next_try_at": time.time() + max(0.0, entry.next_try - time.monotonic()),
            })
    try:
        atomic_write_json(
            RETRY_FILE,
            {"version": 1, "last_error": _last_error, "entries": entries},
            indent=2,
        )
    except Exception as exc:
        logger.warning("保存延迟重试队列失败: {}", exc)


def _load_pending():
    """恢复上次进程留下的待重试条目；损坏条目单独跳过。"""
    global _last_error
    data = load_json(RETRY_FILE, default=None)
    if not isinstance(data, dict):
        return
    _last_error = str(data.get("last_error") or "")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        return
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        channel_id = str(raw.get("channel_id") or "")
        message_id = str(raw.get("message_id") or "")
        message = raw.get("message")
        groups = [str(g) for g in raw.get("groups") or [] if str(g)]
        if not channel_id or not message_id or not isinstance(message, dict) or not groups:
            continue
        if len(_pending) >= MAX_TOTAL_PENDING:
            break
        if len(_pending.get(channel_id) or {}) >= MAX_PER_CHANNEL_PENDING:
            continue
        try:
            next_try_at = float(raw.get("next_try_at"))
            attempts = max(0, int(raw.get("attempts") or 0))
        except (TypeError, ValueError):
            continue
        entry = _RetryEntry(
            message,
            str(raw.get("source_label") or "自动转发来自"),
            groups,
            time.monotonic() + max(0.0, next_try_at - time.time()),
        )
        entry.attempts = attempts
        _pending.setdefault(channel_id, {})[message_id] = entry


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


def _purge_delivered(channel_id, message_id, success_groups):
    """某群已收到该消息（无论哪条路径送达），清除其欠账"""
    entry = (_pending.get(channel_id) or {}).get(message_id)
    if not entry:
        return
    for group in success_groups:
        entry.groups.discard(group)
    if not entry.groups:
        _remove_entry(channel_id, message_id)
    _save_pending()


def schedule_retry(
    channel_id,
    message_id,
    message,
    failed_groups,
    source_label="自动转发来自",
):
    """登记失败消息的延迟重试；返回是否新建队列条目。

    已存在条目时只合并失败群（不重复排队）；
    队列满 / 无失败群时返回 False，缺口交给 backfill 兜底。"""
    global _last_error
    groups = [g for g in failed_groups if g]
    if not groups:
        return False

    entry = (_pending.get(channel_id) or {}).get(message_id)

    if entry:
        entry.groups.update(groups)
        _save_pending()
        return False

    total = sum(len(ch) for ch in _pending.values())
    per_channel = len(_pending.get(channel_id) or {})
    if total >= MAX_TOTAL_PENDING or per_channel >= MAX_PER_CHANNEL_PENDING:
        _last_error = "重试队列已满，消息将由离线补发兜底"
        logger.warning(
            "延迟重试队列已满，丢弃 频道{} 消息{}（等待重连补发）",
            channel_id,
            message_id,
        )
        return False

    _pending.setdefault(channel_id, {})[message_id] = _RetryEntry(
        message,
        source_label,
        groups,
        time.monotonic() + RETRY_BACKOFFS[0],
    )
    _save_pending()

    if _LOOP_AUTO_START:
        _ensure_loop(channel_id)

    return True


def _ensure_loop(channel_id):
    if not _LOOP_AUTO_START:
        return
    if channel_id in _retry_loops:
        return
    _retry_loops.add(channel_id)
    _retry_tasks.add(
        asyncio.create_task(_loop_wrapper(channel_id))
    )


@get_driver().on_startup
async def _resume_pending_retries():
    """Bot 启动后恢复持久化队列的重试循环。"""
    for channel_id in list(_pending):
        _ensure_loop(channel_id)


async def _loop_wrapper(channel_id):
    try:
        await _channel_retry_loop(channel_id)
    finally:
        _retry_loops.discard(channel_id)


async def _channel_retry_loop(channel_id):
    while _pending.get(channel_id):
        await _process_due(channel_id)
        if not _pending.get(channel_id):
            break
        await asyncio.sleep(1.0)


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
            text_parts, media_items, has_content = await build_parts(
                entry.message,
                source_label=entry.source_label,
                enabled_types=get_enabled_message_types(
                    get_channel_filter(channel_id)
                ),
            )
        except Exception:
            _last_error = f"频道{channel_id} 消息{message_id} 内容重建失败"
            logger.warning(
                "延迟重试内容重建失败，放弃 频道{} 消息{}",
                channel_id,
                message_id,
            )
            _remove_entry(channel_id, message_id)
            _save_pending()
            continue

        if not has_content:
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
            _save_pending()
            continue

        ok_map = await send_and_record(
            channel_id,
            message_id,
            text_parts,
            media_items,
            list(entry.groups),
        )

        # send_and_record 成功时已 purge：全部送达则条目被移除
        entry = entries.get(message_id)
        if entry is None:
            logger.success(
                "延迟重试成功已补发 频道{} 消息{}",
                channel_id,
                message_id,
            )
            continue

        entry.attempts += 1

        if entry.attempts >= MAX_RETRY_ATTEMPTS:
            logger.error(
                "延迟重试已达上限({}次)放弃，等待重连补发 频道{} 消息{} 群{}",
                MAX_RETRY_ATTEMPTS,
                channel_id,
                message_id,
                sorted(entry.groups),
            )
            _remove_entry(channel_id, message_id)
            _save_pending()
            continue

        entry.next_try = now + RETRY_BACKOFFS[entry.attempts - 1]
        _save_pending()


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
):
    """统一发送入口（relay / backfill / retry 共用）：
    在途判重 → 发送 → 只进不退记录 → 清除欠账。

    返回 {group_openid: 是否送达}。同一 (频道, 消息, 群) 同时只允许
    一个发送者：占用失败（他人正在发送）的群记为未送达，由调用方
    登记延迟重试，成功路径的 purge 会清除对应欠账。"""
    claimable = [
        group
        for group in groups
        if _claim(channel_id, message_id, group)
    ]

    if not claimable:
        return {group: False for group in groups}

    try:
        ok_map = await send_relay_message(
            text_parts,
            media_items,
            claimable,
            channel_id=channel_id,
            message_id=message_id,
        )

        success_groups = [
            group
            for group, ok in ok_map.items()
            if ok
        ]

        if success_groups:
            await update_last_messages(
                lambda last: apply_success(
                    normalize_channel_map(last, channel_id),
                    {group: True for group in success_groups},
                    message_id,
                )
            )
            _purge_delivered(
                channel_id,
                message_id,
                success_groups,
            )

        return ok_map

    finally:
        for group in claimable:
            _release(channel_id, message_id, group)
