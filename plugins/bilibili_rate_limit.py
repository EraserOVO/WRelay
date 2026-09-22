"""Bilibili → QQ 的限速、积压和筛选配置纯逻辑。

本模块不读写文件，也不调用 QQ/RSSHub。它只负责把管理面板输入归一化，
以及给投递层提供稳定的限流和积压裁剪规则，便于单元测试和后续扩展。
"""

from __future__ import annotations

import time


DEFAULT_PROTECTION = {
    "min_send_interval": 60,
    "hourly_limit": 10,
    "backlog_limit": 10,
    "overflow_policy": "latest",
}

OVERFLOW_LATEST = "latest"
OVERFLOW_OLDEST = "oldest"
VALID_OVERFLOW_POLICIES = {OVERFLOW_LATEST, OVERFLOW_OLDEST}


def _positive_int(value, default, minimum=1, maximum=100000):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    if value < minimum or value > maximum:
        return default
    return value


def normalize_protection_config(value):
    """归一化管理面板中的 Bilibili 保护配置。"""

    value = value if isinstance(value, dict) else {}
    try:
        interval = int(value.get("min_send_interval", DEFAULT_PROTECTION["min_send_interval"]))
    except (TypeError, ValueError):
        interval = DEFAULT_PROTECTION["min_send_interval"]
    interval = max(0, min(interval, 86400))
    return {
        "min_send_interval": interval,
        "hourly_limit": _positive_int(
            value.get("hourly_limit"),
            DEFAULT_PROTECTION["hourly_limit"],
            minimum=1,
            maximum=10000,
        ),
        "backlog_limit": _positive_int(
            value.get("backlog_limit"),
            DEFAULT_PROTECTION["backlog_limit"],
            minimum=1,
            maximum=1000,
        ),
        "overflow_policy": (
            value.get("overflow_policy")
            if value.get("overflow_policy") in VALID_OVERFLOW_POLICIES
            else DEFAULT_PROTECTION["overflow_policy"]
        ),
    }


def normalize_string_list(value):
    """归一化面板输入的逗号/换行分隔字符串列表。"""

    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        item = str(item).strip()
        if item and item not in result:
            result.append(item)
    return result


def choose_overflow(items, limit, policy):
    """返回 ``(保留项, 丢弃项)``，items 需要按旧→新排列。

    latest：保留最新的 limit 项，丢弃更旧的项；
    oldest：保留最旧的 limit 项，丢弃更新的项。
    """

    items = list(items or [])
    limit = max(1, int(limit))
    if len(items) <= limit:
        return items, []
    if policy == OVERFLOW_OLDEST:
        return items[:limit], items[limit:]
    return items[-limit:], items[:-limit]


def prune_sent_timestamps(values, now=None, window_seconds=3600):
    """只保留时间窗口内的成功投递时间戳。"""

    now = time.time() if now is None else float(now)
    result = []
    for value in values or []:
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= now - timestamp < window_seconds:
            result.append(timestamp)
    return sorted(result)


def rate_wait_seconds(sent_timestamps, now, min_send_interval, hourly_limit):
    """返回还需等待的秒数；返回 0 表示现在允许发送一条。"""

    now = float(now)
    sent = prune_sent_timestamps(sent_timestamps, now)
    waits = []
    if sent and min_send_interval > 0:
        waits.append(max(0.0, float(min_send_interval) - (now - sent[-1])))
    if hourly_limit > 0 and len(sent) >= hourly_limit:
        waits.append(max(0.0, 3600.0 - (now - sent[0])))
    return max(waits or [0.0])
