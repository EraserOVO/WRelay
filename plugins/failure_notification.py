"""向已授权的 QQ 私聊用户发送转发失败提醒。"""
import time

from nonebot import logger

from plugins.config import (
    get_failure_notification_config,
    get_failure_notification_recipients,
)


_recent_failures = {}


def _notification_key(channel_id, message_id, failed_groups):
    return (
        str(channel_id or "-"),
        str(message_id or "-"),
        tuple(sorted(str(group) for group in failed_groups)),
    )


async def notify_delivery_failure(qq_bot, failed_groups, channel_id="", message_id=""):
    """仅在最终投递失败后通知；同一失败在冷却期内只提醒一次。"""
    config = get_failure_notification_config()
    recipients = get_failure_notification_recipients()
    if not config["enabled"] or not recipients or not failed_groups:
        return

    now = time.monotonic()
    key = _notification_key(channel_id, message_id, failed_groups)
    previous = _recent_failures.get(key)
    if previous is not None and now - previous < config["cooldown_seconds"]:
        return
    _recent_failures[key] = now

    cutoff = now - config["cooldown_seconds"]
    for old_key, timestamp in tuple(_recent_failures.items()):
        if timestamp < cutoff:
            _recent_failures.pop(old_key, None)

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
        try:
            await qq_bot.send_to_c2c(openid=openid, message=message)
        except Exception as exc:
            logger.warning("发送失败通知失败: {} {}", openid, exc)
