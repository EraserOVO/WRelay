"""X → QQ 的限速和积压保护纯逻辑。"""

from __future__ import annotations

import time


DEFAULT_X_PROTECTION = {
    "min_send_interval": 5,
    "hourly_limit": 300,
    "backlog_limit": 100,
    "overflow_policy": "latest",
}

VALID_OVERFLOW_POLICIES = {"latest", "oldest"}


def normalize_x_protection_config(value):
    """归一化面板配置；默认节奏明显宽于 Bilibili 的保护值。"""

    value = value if isinstance(value, dict) else {}
    try:
        interval = int(value.get(
            "min_send_interval",
            DEFAULT_X_PROTECTION["min_send_interval"],
        ))
    except (OverflowError, TypeError, ValueError):
        interval = DEFAULT_X_PROTECTION["min_send_interval"]
    interval = max(0, min(interval, 86400))

    def bounded_int(key, minimum, maximum):
        try:
            result = int(value.get(key, DEFAULT_X_PROTECTION[key]))
        except (OverflowError, TypeError, ValueError):
            return DEFAULT_X_PROTECTION[key]
        if result < minimum or result > maximum:
            return DEFAULT_X_PROTECTION[key]
        return result

    policy = value.get("overflow_policy")
    if policy not in VALID_OVERFLOW_POLICIES:
        policy = DEFAULT_X_PROTECTION["overflow_policy"]
    return {
        "min_send_interval": interval,
        "hourly_limit": bounded_int("hourly_limit", 1, 10000),
        "backlog_limit": bounded_int("backlog_limit", 1, 1000),
        "overflow_policy": policy,
    }


def choose_overflow(items, limit, policy):
    """按旧→新顺序的条目裁剪至上限，返回保留项和丢弃项。"""

    items = list(items or [])
    limit = max(1, int(limit))
    if len(items) <= limit:
        return items, []
    if policy == "oldest":
        return items[:limit], items[limit:]
    return items[-limit:], items[:-limit]


def prune_sent_timestamps(values, now=None, window_seconds=3600):
    """保留一小时窗口中的成功投递时间戳。"""

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
    """返回下一次成功投递前仍需等待的秒数。"""

    now = float(now)
    sent = prune_sent_timestamps(sent_timestamps, now)
    waits = []
    if sent and min_send_interval > 0:
        waits.append(max(0.0, float(min_send_interval) - (now - sent[-1])))
    if hourly_limit > 0 and len(sent) >= hourly_limit:
        waits.append(max(0.0, 3600.0 - (now - sent[0])))
    return max(waits or [0.0])
