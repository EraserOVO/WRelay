"""向已授权的 QQ 私聊用户发送转发失败提醒。"""
import asyncio
from collections import OrderedDict
import time

from nonebot import logger

from plugins.config import get_failure_notification_snapshot


RECENT_FAILURE_LIMIT = 5000
_recent_failures = OrderedDict()
_notification_locks = {}
_notification_lock_users = {}


def _prune_recent_failures(now, cooldown_seconds):
    """按时间顺序移除过期项；每个条目只在过期时被清理一次。"""
    cutoff = now - cooldown_seconds
    while _recent_failures:
        oldest_key, timestamp = next(iter(_recent_failures.items()))
        if timestamp >= cutoff:
            break
        _recent_failures.popitem(last=False)


def _remember_failure(key, now):
    _recent_failures[key] = now
    _recent_failures.move_to_end(key)
    while len(_recent_failures) > RECENT_FAILURE_LIMIT:
        _recent_failures.popitem(last=False)


def _notification_key(channel_id, message_id, failed_groups):
    return (
        str(channel_id or "-"),
        str(message_id or "-"),
        tuple(sorted(str(group) for group in failed_groups)),
    )


async def _notify_recipient(qq_bot, openid, key, message, cooldown_seconds):
    lock = _notification_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _notification_locks[key] = lock
        _notification_lock_users[key] = 0
    _notification_lock_users[key] += 1
    try:
        async with lock:
            previous = _recent_failures.get(key)
            now = time.monotonic()
            if previous is not None and now - previous < cooldown_seconds:
                return
            try:
                await qq_bot.send_to_c2c(openid=openid, message=message)
            except Exception as exc:
                logger.warning("发送失败通知失败: {} {}", openid, exc)
            else:
                # A failed private message must not consume this recipient's
                # cooldown; a later callback can retry it.
                _remember_failure(key, time.monotonic())
    finally:
        users = _notification_lock_users.get(key, 1) - 1
        if users <= 0:
            _notification_lock_users.pop(key, None)
            if _notification_locks.get(key) is lock:
                _notification_locks.pop(key, None)
        else:
            _notification_lock_users[key] = users


async def notify_delivery_failure(qq_bot, failed_groups, channel_id="", message_id=""):
    """仅在最终投递失败后通知；同一失败在冷却期内只提醒一次。"""
    config, recipients = await asyncio.to_thread(get_failure_notification_snapshot)
    if not config["enabled"] or not recipients or not failed_groups:
        return

    now = time.monotonic()
    _prune_recent_failures(now, config["cooldown_seconds"])
    key_prefix = _notification_key(channel_id, message_id, failed_groups)

    message = (
        "【WRelay 转发失败】\n"
        "来源：{}\n"
        "消息：{}\n"
        "未送达群：{}\n"
        "已完成自动重试，请在管理面板日志中查看详情。"
    ).format(
        channel_id or "手动/定时消息",
        message_id or "-",
        "、".join(str(group) for group in failed_groups),
    )
    for openid in recipients:
        key = (*key_prefix, str(openid))
        await _notify_recipient(
            qq_bot,
            openid,
            key,
            message,
            config["cooldown_seconds"],
        )
