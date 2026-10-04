"""近期消息的本地投递追踪。

追踪仅服务于管理面板展示：记录最终投递状态、目标群和简短正文摘要，
不参与路由、去重、补发或重试决策。相同来源消息的后续重试会合并到
同一条记录，面板始终展示该消息当前的最终状态。
"""

import time
import uuid

from plugins.json_io import load_json, run_in_thread_to_completion, update_json
from plugins.paths import data_path


TRACE_FILE = data_path("message_trace.json")
TRACE_LIMIT = 200
PREVIEW_LIMIT = 120


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _source_info(channel_id):
    """从既有发送上下文推断来源类型；仅用于面板文案。"""
    value = str(channel_id or "").strip()
    if value.startswith("x_rss:"):
        return "x_rss", value.removeprefix("x_rss:")
    if value.startswith("bilibili:"):
        return "bilibili", value.removeprefix("bilibili:")
    if value:
        return "discord", value
    return "other", ""


def _preview_text(text_parts):
    """提取文本段的短摘要，避免把完整消息和媒体链接重复写入追踪文件。"""
    chunks = []
    for part in text_parts or ():
        data = getattr(part, "data", None)
        if not isinstance(data, dict) and isinstance(part, dict):
            data = part.get("data")
        if not isinstance(data, dict):
            continue
        text = data.get("text")
        if text:
            chunks.append(str(text))
    preview = " ".join(" ".join(chunks).split())
    if len(preview) > PREVIEW_LIMIT:
        return preview[: PREVIEW_LIMIT - 1] + "…"
    return preview


def _summarize(entry):
    groups = entry.get("groups") or []
    total = len(groups)
    delivered = sum(1 for group in groups if group.get("ok"))
    entry["target_count"] = total
    entry["delivered_count"] = delivered

    note = str(entry.get("note") or "")
    if note == "QQ Bot 未连接":
        entry["result"] = "not_sent"
    elif note == "没有可用目标群":
        entry["result"] = "skipped"
    elif total and delivered == total:
        entry["result"] = "success"
        entry["note"] = ""
    elif delivered:
        entry["result"] = "partial"
    else:
        entry["result"] = "failed"


def _record_delivery_sync(
    channel_id,
    message_id,
    text_parts,
    media_items,
    groups,
    ok_map,
    note,
):
    now = int(time.time())
    order = time.time_ns()
    channel = str(channel_id or "").strip()
    message = str(message_id or "").strip()
    source_kind, source_id = _source_info(channel)
    target_ids = list(dict.fromkeys(str(group) for group in (groups or ()) if str(group)))
    result_map = {str(group): bool(ok) for group, ok in (ok_map or {}).items()}
    preview = _preview_text(text_parts)
    media_count = len(media_items or ())
    trace_key = "message:%s:%s" % (channel, message) if message else "event:" + uuid.uuid4().hex

    def mutate(entries):
        if not isinstance(entries, list):
            entries = []
        else:
            entries[:] = [item for item in entries if isinstance(item, dict)]

        entry = next(
            (item for item in entries if item.get("trace_key") == trace_key),
            None,
        )
        if entry is None:
            entry = {
                "trace_key": trace_key,
                "first_at": now,
                "attempts": 0,
                "groups": [],
                "channel_id": channel,
                "message_id": message,
                "source_kind": source_kind,
                "source_id": source_id,
                "preview": preview,
                "media_count": media_count,
            }
            entries.append(entry)
        else:
            entry["first_at"] = _safe_int(entry.get("first_at"), now)

        entry["last_at"] = now
        entry["trace_order"] = order
        entry["attempts"] = max(0, _safe_int(entry.get("attempts"))) + 1
        entry["channel_id"] = channel
        entry["message_id"] = message
        entry["source_kind"] = source_kind
        entry["source_id"] = source_id
        if preview:
            entry["preview"] = preview
        entry["media_count"] = max(0, _safe_int(entry.get("media_count")), media_count)
        entry["note"] = str(note or "")

        group_state = {
            str(item.get("id")): bool(item.get("ok"))
            for item in entry.get("groups") or []
            if isinstance(item, dict) and item.get("id")
        }
        for group in target_ids:
            group_state[group] = result_map.get(group, False)
        entry["groups"] = [
            {"id": group, "ok": group_state[group]}
            for group in group_state
        ]
        _summarize(entry)

        entries.sort(
            key=lambda item: (
                _safe_int(item.get("last_at")),
                _safe_int(item.get("trace_order")),
            ),
            reverse=True,
        )
        del entries[TRACE_LIMIT:]
        return entries

    update_json(TRACE_FILE, mutate, default=[], indent=2)


async def record_delivery(
    channel_id,
    message_id,
    text_parts,
    media_items,
    groups,
    ok_map,
    note="",
):
    """异步记录一次投递结果，写入失败时由调用方自行降级处理。"""
    await run_in_thread_to_completion(
        _record_delivery_sync,
        channel_id,
        message_id,
        text_parts,
        media_items,
        groups,
        ok_map,
        note,
    )


def get_delivery_traces(limit=50):
    """返回最近记录，限制在 1–200 条；不暴露内部 trace_key。"""
    try:
        limit = max(1, min(int(limit), TRACE_LIMIT))
    except (TypeError, ValueError):
        limit = 50
    entries = load_json(TRACE_FILE, default=[])
    if not isinstance(entries, list):
        return []

    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        item["first_at"] = _safe_int(item.get("first_at"))
        item["last_at"] = _safe_int(item.get("last_at"))
        item["attempts"] = max(0, _safe_int(item.get("attempts")))
        item["media_count"] = max(0, _safe_int(item.get("media_count")))
        item["trace_order"] = _safe_int(item.get("trace_order"))
        item["groups"] = [
            {"id": str(group.get("id") or ""), "ok": bool(group.get("ok"))}
            for group in item.get("groups") or []
            if isinstance(group, dict) and group.get("id")
        ]
        _summarize(item)
        cleaned.append(item)
    cleaned.sort(
        key=lambda item: (
            _safe_int(item.get("last_at")),
            _safe_int(item.get("trace_order")),
        ),
        reverse=True,
    )
    for item in cleaned:
        item.pop("trace_key", None)
        item.pop("trace_order", None)
    return cleaned[:limit]
