"""QQ 私聊 relay 已消费事件的登记表。

`plugins/command.py` 消费一条私聊消息（relay 指令或 pending 的下一条内容）后
会调用 NoneBot 的 `stop_propagation()`，让低优先级 matcher 不再处理同一事件。
该信号依赖 NoneBot 与 anyio 的异常处理链；在部分运行环境（Nuitka 编译产物）
中它会丢失，导致 `plugins/manage.py` 把同一条消息再按命令处理一次并回复
「未知命令」。这里显式记录已被 relay 消费的事件 ID，供低优先级 matcher 跳过，
作为 `stop_propagation` 的兜底。

只记录事件 ID，不保存消息内容；容量固定，超出后按先进先出淘汰。
"""

from collections import deque


MAX_CONSUMED_EVENTS = 64

_consumed_event_ids = deque(maxlen=MAX_CONSUMED_EVENTS)


def _event_id(event):
    return str(getattr(event, "id", "") or "").strip()


def mark_consumed(event):
    """登记已被 QQ 私聊 relay 消费的事件。"""
    event_id = _event_id(event)
    if not event_id or event_id in _consumed_event_ids:
        return
    _consumed_event_ids.append(event_id)


def is_consumed(event):
    """返回事件是否已被 QQ 私聊 relay 消费。"""
    event_id = _event_id(event)
    return bool(event_id) and event_id in _consumed_event_ids


def clear_consumed():
    """清空登记表（供测试与进程内状态重置使用）。"""
    _consumed_event_ids.clear()
