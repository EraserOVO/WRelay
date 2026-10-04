import asyncio
import json
import os

from plugins.json_io import atomic_write_json, run_in_thread_to_completion
from plugins.paths import data_path


LAST_MESSAGE_FILE = data_path("discord_last.json")


# =====================================================
# Discord 消息 ID 持久化
#
# 单一职责：读写 data/discord_last.json，供转发去重使用。
# 高频转发时每条消息都读盘没有必要，这里做内存缓存，
# 通过文件 mtime + 大小校验感知外部修改（本文件只有本进程写，
# 缓存与磁盘始终一致；面板等进程不碰这个文件）。
# =====================================================

_last = None
_last_mtime = None
_last_size = None


def _valid_cursor_id(value):
    if type(value) is int:
        value = str(value)
    elif not isinstance(value, str):
        return False
    return (
        len(value) <= 32
        and value.isascii()
        and value.isdecimal()
    )


def _validate_cursor_data(data):
    """校验旧版单游标和新版按群游标，避免坏值进入 int 比较。"""
    for channel_id, channel_data in data.items():
        if isinstance(channel_data, dict):
            for group_id, cursor_id in channel_data.items():
                if cursor_id is None or cursor_id == "":
                    continue
                if not _valid_cursor_id(cursor_id):
                    raise RuntimeError(
                        "Discord 去重游标文件格式无效，频道 {} 群 {} 的游标必须是至多 32 位的十进制 ID；"
                        "已中止游标更新以保留原文件".format(channel_id, group_id)
                    )
            continue
        if channel_data is None or channel_data == "":
            continue
        if not _valid_cursor_id(channel_data):
            raise RuntimeError(
                "Discord 去重游标文件格式无效，频道 {} 的旧版游标必须是至多 32 位的十进制 ID；"
                "已中止游标更新以保留原文件".format(channel_id)
            )


def load_last_messages():

    global _last, _last_mtime, _last_size

    try:
        st = os.stat(LAST_MESSAGE_FILE)
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime = size = None

    if (
        _last is not None
        and mtime == _last_mtime
        and size == _last_size
    ):
        return _last

    try:
        with open(LAST_MESSAGE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "Discord 去重游标文件读取失败，已中止游标更新以保留原文件: {}".format(exc)
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "Discord 去重游标文件格式无效，顶层 JSON 必须是对象；已中止游标更新以保留原文件"
        )
    _validate_cursor_data(data)

    _last = data
    _last_mtime, _last_size = mtime, size

    return _last


def save_last_messages(data):

    atomic_write_json(
        LAST_MESSAGE_FILE,
        data,
        indent=4
    )

    global _last, _last_mtime, _last_size

    _last = data

    try:
        st = os.stat(LAST_MESSAGE_FILE)
        _last_mtime, _last_size = st.st_mtime, st.st_size
    except OSError:
        pass


_save_lock = asyncio.Lock()


async def load_last_messages_async(reader=None):
    """Read the shared cursor without blocking asyncio or racing its writers."""
    if reader is None:
        reader = load_last_messages
    async with _save_lock:
        return await run_in_thread_to_completion(reader)


async def update_last_messages(mutate):
    """并发安全的"应用变更并落盘"：锁内基于最新状态调用
    mutate(最新 last_messages)（mutate 必须同步，不得在其中 await），
    返回是否产生变更。

    避免多任务各自持有旧副本互相覆盖推进；业务层锁，
    不塞进通用 json_io.py。"""
    async with _save_lock:
        last_messages = await run_in_thread_to_completion(load_last_messages)
        changed = mutate(last_messages)
        if changed:
            await run_in_thread_to_completion(save_last_messages, last_messages)
        return changed
