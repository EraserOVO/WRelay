import asyncio
import time

from nonebot import logger

from plugins.json_io import (
    load_json,
    run_in_thread_to_completion,
    update_json,
)
from plugins.paths import data_path


STATS_FILE = data_path("stats.json")


# =====================================================
# 转发统计（B4）
#
# 单一职责：累计转发成功/失败条数 + 当日条数。
# 只在 relay.py 内自增，进程内单点写，故用内存缓存；
# 管理面板直接读 data/stats.json 展示。
# =====================================================

_stats = None
_stats_lock = asyncio.Lock()
_stats_write_error = ""


def _safe_counter(value):
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _load():

    global _stats

    if _stats is None:

        data = load_json(
            STATS_FILE,
            default={}
        )

        _stats = data if isinstance(data, dict) else {}

    return _stats


def _write_stats_snapshot(path, snapshot):
    """Replace counters under the JSON lock without overwriting unreadable files."""
    def replace(data):
        data.clear()
        data.update(snapshot)
        return True

    update_json(path, replace, default={}, indent=2)


async def _persist_snapshot(snapshot):
    """Stats persistence failures must not interrupt message delivery or retries."""
    global _stats_write_error
    try:
        await run_in_thread_to_completion(
            _write_stats_snapshot,
            STATS_FILE,
            snapshot,
        )
    except Exception as exc:
        message = "{}: {}".format(STATS_FILE, exc)
        if message != _stats_write_error:
            logger.warning(
                "统计文件写入失败，保留原文件并继续使用进程内计数: {}",
                exc,
            )
        _stats_write_error = message
    else:
        _stats_write_error = ""


def get_stats():
    """返回统计 dict（面板直接读文件也行，这里供插件内使用）"""
    return dict(_load())


async def record(ok):
    """转发一条消息后记录统计（ok = 至少一个群送达）"""
    async with _stats_lock:
        data = await run_in_thread_to_completion(_load)

        today = time.strftime("%Y-%m-%d")
        data["total_forwarded"] = _safe_counter(data.get("total_forwarded"))
        data["total_failed"] = _safe_counter(data.get("total_failed"))

        if data.get("today") != today:
            data["today"] = today
            data["today_forwarded"] = 0
            data["today_failed"] = 0
        else:
            data["today_forwarded"] = _safe_counter(data.get("today_forwarded"))
            data["today_failed"] = _safe_counter(data.get("today_failed"))

        if ok:
            data["total_forwarded"] += 1
            data["today_forwarded"] += 1
        else:
            data["total_failed"] += 1
            data["today_failed"] += 1

        snapshot = dict(data)
        await _persist_snapshot(snapshot)
