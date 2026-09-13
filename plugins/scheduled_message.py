"""QQ 定时消息投递。

配置保存于 settings.json 的 scheduled_message：启用后按每条消息自己的
间隔或日历计划，把消息发送到显式选中的 QQ 接收群。配置实时读取，面板
或白名单私聊修改后无需重启机器人。
"""

import asyncio
import calendar
from datetime import datetime, time, timedelta

from nonebot import get_driver, logger
from nonebot.adapters.qq import Bot as QQBot

from plugins.config import (
    get_active_groups,
    get_scheduled_message_config,
)
from plugins.media import make_text
from plugins.sender import send_relay_message


_loop_task = None
_loop_started = False


def _scheduled_time(value):
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except (AttributeError, TypeError, ValueError):
        return time(9, 0)


def _parse_datetime(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _next_interval_at(now, message):
    """按基准时间和日/时/分间隔计算下一个严格晚于 now 的时间。"""
    try:
        days = int(message.get("interval_days") or 0)
        hours = int(message.get("interval_hours") or 0)
        minutes = int(message.get("interval_minutes") or 0)
    except (TypeError, ValueError):
        days, hours, minutes = 0, 0, 1
    delta = timedelta(days=max(0, days), hours=max(0, hours), minutes=max(0, minutes))
    if delta.total_seconds() <= 0:
        delta = timedelta(minutes=1)
    anchor = _parse_datetime(message.get("anchor_at")) or now
    if anchor > now:
        return anchor
    elapsed = now - anchor
    steps = int(elapsed.total_seconds() // delta.total_seconds()) + 1
    return anchor + delta * steps


def _next_calendar_at(now, message):
    """寻找下一次周/月/年日历计划；不存在的日期跳过。"""
    target_time = _scheduled_time(message.get("send_time", "09:00"))
    calendar_type = message.get("calendar_type")
    if calendar_type == "weekly":
        weekdays = set(message.get("weekdays") or [])
        for offset in range(8):
            candidate_date = now.date() + timedelta(days=offset)
            if candidate_date.weekday() not in weekdays:
                continue
            candidate = datetime.combine(candidate_date, target_time)
            if candidate > now:
                return candidate
    elif calendar_type == "monthly":
        try:
            day = int(message.get("day_of_month") or 1)
        except (TypeError, ValueError):
            day = 1
        for offset in range(0, 367):
            candidate_date = now.date() + timedelta(days=offset)
            if candidate_date.day != day:
                continue
            candidate = datetime.combine(candidate_date, target_time)
            if candidate > now:
                return candidate
    elif calendar_type == "yearly":
        try:
            month = int(message.get("month") or 1)
            day = int(message.get("day") or 1)
        except (TypeError, ValueError):
            month, day = 1, 1
        for offset in range(0, 366 * 8):
            candidate_date = now.date() + timedelta(days=offset)
            if candidate_date.month != month or candidate_date.day != day:
                continue
            candidate = datetime.combine(candidate_date, target_time)
            if candidate > now:
                return candidate
    return now + timedelta(minutes=1)


def next_scheduled_at(now, message):
    """计算单条消息下一次发送时间。"""
    if message.get("schedule_type") == "calendar":
        return _next_calendar_at(now, message)
    return _next_interval_at(now, message)


async def _scheduled_message_loop():
    """每秒检查配置，分别处理每条消息的下一次发送时间。"""
    last_config = None
    next_send_at = {}

    while True:
        try:
            config = get_scheduled_message_config()
            enabled = config["enabled"]
            target_groups = set(config["target_groups"])
            messages = config.get("messages") or []
            message_signature = tuple(
                tuple(sorted((str(key), repr(value)) for key, value in message.items()))
                for message in messages
            )
            signature = (
                enabled,
                tuple(sorted(target_groups)),
                message_signature,
            )

            if signature != last_config:
                last_config = signature
                next_send_at = {}

            now = datetime.now()
            active_keys = set()
            if enabled and target_groups:
                for index, message in enumerate(messages):
                    key = str(message.get("id") or "message-" + str(index + 1)) + ":" + str(index)
                    active_keys.add(key)
                    if key not in next_send_at:
                        next_send_at[key] = next_scheduled_at(now, message)
                    if now < next_send_at[key]:
                        continue
                    groups = [
                        group for group in get_active_groups()
                        if group in target_groups
                    ]
                    if groups:
                        ok_map = await send_relay_message(
                            [make_text(message["content"])],
                            [],
                            groups=groups,
                        )
                        if ok_map and all(ok_map.values()):
                            logger.info("定时消息发送成功（{} 个群）", len(ok_map))
                        elif ok_map:
                            logger.warning("定时消息部分发送失败: {}", ok_map)
                        else:
                            logger.warning("定时消息发送失败：QQ Bot 未连接")
                    else:
                        logger.info("定时消息跳过：没有可用的目标 QQ 接收群")
                    next_send_at[key] = next_scheduled_at(datetime.now(), message)
            next_send_at = {
                key: value for key, value in next_send_at.items()
                if key in active_keys
            }

            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("定时消息循环异常")
            await asyncio.sleep(1)


@get_driver().on_bot_connect
async def _start_scheduled_message_loop(bot):
    """QQ Bot 连接后启动定时消息循环（仅启动一次）。"""
    global _loop_task, _loop_started
    if not isinstance(bot, QQBot):
        return
    if _loop_task is not None and not _loop_task.done():
        return
    _loop_started = True
    _loop_task = asyncio.create_task(_scheduled_message_loop())
    logger.info("定时消息循环已启动")


@get_driver().on_shutdown
async def _stop_scheduled_message_loop():
    global _loop_task, _loop_started
    task = _loop_task
    _loop_task = None
    _loop_started = False
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
