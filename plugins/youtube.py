"""YouTube 频道 → QQ 自动转发（第一期：标题、封面与链接）。

来源只支持频道（频道 ID 或 ``@handle``），通过 YouTube 官方
``feeds/videos.xml`` 拉取；不解析视频本体，不做关键词/搜索来源。
投递复用 translation/retry/sender，并沿用 Bilibili 的基线、限速与积压
保护，不改变 Discord、X、Bilibili 或 QQ→QQ 的路由与配置。
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
import time

import httpx

from nonebot import get_app, get_bots, get_driver, logger
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

from plugins.config import (
    get_youtube_groups_for_source,
    get_youtube_poll_interval,
    get_youtube_polling_snapshot,
    get_youtube_protection,
    get_youtube_source_delivery_config,
    get_youtube_source_filter,
    get_youtube_status_snapshot,
)
from plugins.dedup import (
    apply_baseline,
    apply_skipped,
    normalize_channel_map,
    select_target_groups,
)
from plugins.filter import (
    YOUTUBE_MESSAGE_TYPES,
    check_youtube_video_filter,
    get_enabled_message_types,
)
from plugins.history import (
    load_last_messages,
    load_last_messages_async,
    update_last_messages,
)
from plugins.json_io import atomic_write_json, run_in_thread_to_completion
from plugins.media import fetch_bytes_many, make_media_link, make_text
from plugins.paths import data_path
from plugins.retry import send_and_record
from plugins.rss_polling import read_rss_poll_interval, run_rss_poll_loop
from plugins.stats import record
from plugins.translation import translate_text_parts
from plugins.youtube_parser import (
    CHANNEL_ID_RE,
    channel_feed_url,
    channel_page_url,
    parse_channel_page,
    parse_feed,
)
from plugins.bilibili_rate_limit import (
    choose_overflow,
    normalize_protection_config,
    prune_sent_timestamps,
    rate_wait_seconds,
)


FEED_TIMEOUT = 30
FEED_MAX_ATTEMPTS = 4
FEED_RETRY_DELAYS = (2, 5, 15)
# YouTube 官方 RSS 没有公开频率说明；多个来源共用同一客户端，保持间隔。
FEED_REQUEST_GAP = 2
YOUTUBE_POLL_CONCURRENCY = 3
DEFAULT_YOUTUBE_POLL_INTERVAL = 300
YOUTUBE_STATE_FILE = data_path("youtube.json")
YOUTUBE_PROTECTION_FILE = data_path("youtube_protection.json")

_feed_client = None
_feed_proxy = None
_feed_request_tasks = set()
_feed_stopping = False
_state_file_lock = threading.Lock()
_protection_file_lock = threading.Lock()
_protection_state_lock = threading.RLock()
_protection_lock = asyncio.Lock()
_feed_request_lock = asyncio.Lock()
_last_feed_request_ts = 0.0
_posts_cache = {}
_protection_runtime_state = None
_protection_load_error = ""
_PROTECTION_STATE_MISSING = object()
_STATE_MISSING = object()
_state_load_error = ""

_runtime_status = {
    "loop_started": False,
    "last_poll_ts": 0,
    "last_result": "",
    "last_error": "",
    "last_new_count": 0,
    "last_sent_count": 0,
    "last_failed_count": 0,
}


def runtime_status():
    return dict(_runtime_status)


class YoutubeProtectionError(RuntimeError):
    """本地投递保护状态不可用，不能继续投递或推进游标。"""


def _short_error(error, limit=160):
    return " ".join(str(error or "").split())[:limit]


def _read_discord_proxy():
    try:
        proxy = getattr(get_driver().config, "discord_proxy", None)
    except Exception:
        return None
    if isinstance(proxy, str):
        proxy = proxy.strip()
        return proxy or None
    return None


def _get_feed_client():
    global _feed_client, _feed_proxy
    proxy = _read_discord_proxy()
    if _feed_client is None or proxy != _feed_proxy:
        if _feed_client is not None:
            try:
                asyncio.get_running_loop().create_task(_feed_client.aclose())
            except RuntimeError:
                pass
        # YouTube 在部分网络下需要与 Discord 相同的代理；trust_env=False
        # 避免系统环境代理覆盖显式配置。
        _feed_client = httpx.AsyncClient(
            proxy=proxy,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": "WRelay YouTube RSS"},
        )
        _feed_proxy = proxy
    return _feed_client


async def _youtube_get(*args, **kwargs):
    """Track YouTube requests so shutdown can reap one-shot calls."""
    if _feed_stopping:
        raise asyncio.CancelledError("YouTube RSS HTTP client is shutting down")
    task = asyncio.current_task()
    if task is not None:
        _feed_request_tasks.add(task)
    try:
        return await _get_feed_client().get(*args, **kwargs)
    finally:
        if task is not None:
            _feed_request_tasks.discard(task)


@get_driver().on_shutdown
async def _close_feed_client():
    global _feed_client, _poll_task, _loop_started, _poll_stopping, _feed_stopping
    _feed_stopping = True
    _poll_stopping = True
    current_task = asyncio.current_task()
    pending_tasks = set()
    poll_task = _poll_task
    if poll_task is not None and not poll_task.done():
        pending_tasks.add(poll_task)
    pending_tasks.update(
        task for task in _feed_request_tasks
        if task is not current_task and not task.done()
    )
    for task in pending_tasks:
        task.cancel()
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    _feed_request_tasks.clear()
    if _poll_task is poll_task:
        _poll_task = None
    _loop_started = False
    _runtime_status["loop_started"] = False
    if _feed_client is not None:
        await _feed_client.aclose()
        _feed_client = None


def _read_state():
    try:
        with open(YOUTUBE_STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return _STATE_MISSING


def _load_state():
    global _state_load_error
    try:
        data = _read_state()
    except (OSError, ValueError) as exc:
        error = "YouTube 来源状态读取失败：{}".format(_short_error(exc))
        if error != _state_load_error:
            logger.error("{}；已暂停来源轮询以保留原基线", error)
        _state_load_error = error
        raise RuntimeError(error) from exc

    if data is _STATE_MISSING:
        data = {"sources": {}}
    elif not isinstance(data, dict):
        error = "YouTube 来源状态格式无效：顶层 JSON 必须是对象"
        if error != _state_load_error:
            logger.error("{}；已暂停来源轮询以保留原基线", error)
        _state_load_error = error
        raise RuntimeError(error)
    elif not isinstance(data.get("sources"), dict):
        error = "YouTube 来源状态格式无效：sources 必须是对象"
        if error != _state_load_error:
            logger.error("{}；已暂停来源轮询以保留原基线", error)
        _state_load_error = error
        raise RuntimeError(error)

    _state_load_error = ""
    return data


async def _load_state_async():
    """Read RSS metadata state without blocking the asyncio event loop."""
    return await asyncio.to_thread(_load_state)


def _save_state(data):
    atomic_write_json(YOUTUBE_STATE_FILE, data, indent=2)


def _valid_cursor_id(value):
    """恢复持久化队列时仅接受解析器生成的 ASCII 十进制游标。"""
    return bool(value) and value.isascii() and value.isdecimal()


def _read_protection_state():
    try:
        with open(YOUTUBE_PROTECTION_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return _PROTECTION_STATE_MISSING


def _load_protection_state():
    # Every production async caller uses the thread-pool wrapper below, so
    # serializing cold loads here cannot hold up the event loop.
    with _protection_state_lock:
        return _load_protection_state_locked()


def _load_protection_state_locked():
    """加载限速队列；损坏条目单独丢弃，不影响 RSS 主状态。"""

    global _protection_runtime_state, _protection_load_error
    with _protection_state_lock:
        cached_state = _protection_runtime_state
    if cached_state is not None:
        return cached_state
    state = {"version": 1, "queues": {}, "sent_at": []}
    try:
        raw = _read_protection_state()
    except (OSError, ValueError) as exc:
        error = "YouTube 积压队列读取失败：{}".format(_short_error(exc))
        with _protection_state_lock:
            if _protection_runtime_state is not None:
                return _protection_runtime_state
            should_log = error != _protection_load_error
            _protection_load_error = error
        if should_log:
            logger.error("{}；已暂停 YouTube 自动投递并保留原文件", error)
        return state

    if raw is _PROTECTION_STATE_MISSING:
        raw = {}
    elif not isinstance(raw, dict):
        error = "YouTube 积压队列格式无效：顶层 JSON 必须是对象"
        with _protection_state_lock:
            if _protection_runtime_state is not None:
                return _protection_runtime_state
            should_log = error != _protection_load_error
            _protection_load_error = error
        if should_log:
            logger.error("{}；已暂停 YouTube 自动投递并保留原文件", error)
        return state

    if isinstance(raw, dict):
        sent_at = raw.get("sent_at")
        if isinstance(sent_at, list):
            state["sent_at"] = prune_sent_timestamps(sent_at, time.time())
        queues = raw.get("queues")
        if isinstance(queues, dict):
            for source_id, bucket in queues.items():
                if not isinstance(bucket, dict):
                    continue
                items = []
                seen = set()
                for item in bucket.get("items") or []:
                    if not isinstance(item, dict) or not isinstance(item.get("post"), dict):
                        continue
                    post = item["post"]
                    post_id = str(item.get("id") or post.get("id") or "").strip()
                    cursor_id = str(item.get("cursor_id") or post.get("cursor_id") or "").strip()
                    if (
                        not post_id
                        or not _valid_cursor_id(cursor_id)
                        or post_id in seen
                    ):
                        continue
                    seen.add(post_id)
                    items.append({"id": post_id, "cursor_id": cursor_id, "post": post})
                deferred = []
                deferred_seen = set()
                for item in bucket.get("deferred_skips") or []:
                    if not isinstance(item, dict):
                        continue
                    post_id = str(item.get("id") or "").strip()
                    cursor_id = str(item.get("cursor_id") or "").strip()
                    if (
                        not post_id
                        or not _valid_cursor_id(cursor_id)
                        or post_id in deferred_seen
                    ):
                        continue
                    deferred_seen.add(post_id)
                    deferred.append({"id": post_id, "cursor_id": cursor_id})
                if items or deferred:
                    state["queues"][str(source_id)] = {
                        "items": sorted(items, key=lambda x: int(x["cursor_id"])),
                        "deferred_skips": sorted(deferred, key=lambda x: int(x["cursor_id"])),
                    }
    with _protection_state_lock:
        if _protection_runtime_state is not None:
            return _protection_runtime_state
        _protection_load_error = ""
        _protection_runtime_state = state
        return state


async def _load_protection_state_async():
    """Read protection queue state without blocking the asyncio event loop."""
    def load_snapshot():
        with _protection_state_lock:
            state = _load_protection_state()
            return state, _protection_load_error

    return await asyncio.to_thread(load_snapshot)


def _save_protection_state(state):
    if _protection_load_error:
        logger.warning("YouTube 积压队列未能安全读取，跳过保存以保留原文件")
        return False
    try:
        with _protection_file_lock:
            atomic_write_json(YOUTUBE_PROTECTION_FILE, state, indent=2)
        return True
    except Exception as exc:
        logger.warning("YouTube 限速队列保存失败: {}", exc)
        return False


async def _save_protection_state_async(state):
    """Persist an isolated snapshot and retain caller locks through cancellation."""
    snapshot = copy.deepcopy(state)
    return await run_in_thread_to_completion(_save_protection_state, snapshot)


def _protection_bucket(state, source_id):
    return state["queues"].setdefault(
        str(source_id),
        {"items": [], "deferred_skips": []},
    )


def _queue_item(post):
    return {
        "id": str(post.get("id") or ""),
        "cursor_id": str(post.get("cursor_id") or ""),
        "post": post,
    }


async def _skip_queue_items(channel_key, active_groups, items):
    """把明确丢弃的条目推进为已处理，避免每次轮询重复出现。"""

    if not items or not active_groups:
        return
    ordered = sorted(items, key=lambda item: int(item["cursor_id"]))

    def mutate(last):
        channel_map = normalize_channel_map(last, channel_key)
        changed = False
        for item in ordered:
            targets = select_target_groups(channel_map, active_groups, item["cursor_id"])
            if targets:
                changed = apply_skipped(channel_map, targets, item["cursor_id"]) or changed
        return changed

    await update_last_messages(mutate)


async def _flush_deferred_skips(source_id, active_groups):
    state, load_error = await _load_protection_state_async()
    if load_error:
        raise RuntimeError(load_error)
    bucket = _protection_bucket(state, source_id)
    if bucket["items"] or not bucket["deferred_skips"]:
        return
    items = list(bucket["deferred_skips"])
    await _skip_queue_items(_source_channel_key(source_id), active_groups, items)
    bucket["deferred_skips"] = []
    await _save_protection_state_async(state)


async def _load_source_delivery_config(source_id):
    return await asyncio.to_thread(get_youtube_source_delivery_config, source_id)


async def _load_source_groups(source_id):
    return await asyncio.to_thread(get_youtube_groups_for_source, source_id)


async def _load_protection_config():
    return await asyncio.to_thread(get_youtube_protection)


async def _load_polling_snapshot():
    return await asyncio.to_thread(get_youtube_polling_snapshot)


async def _remove_queue_item(bucket, item, source_id, active_groups):
    """移除已处理条目，仅在来源队列清空后推进延后跳过记录。"""
    bucket["items"].remove(item)
    if not bucket["items"]:
        await _flush_deferred_skips(source_id, active_groups)


async def _dispatch_one_locked():
    """在全局锁内最多投递一个已入队的 YouTube 视频。"""

    config = normalize_protection_config(await _load_protection_config())
    state, load_error = await _load_protection_state_async()
    if load_error:
        raise RuntimeError(load_error)
    now = time.time()
    pruned = prune_sent_timestamps(state.get("sent_at"), now)
    state["sent_at"] = pruned
    if rate_wait_seconds(
        pruned,
        now,
        config["min_send_interval"],
        config["hourly_limit"],
    ) > 0:
        await _save_protection_state_async(state)
        return 0, 0

    configured_sources, groups_by_source = await _load_polling_snapshot()
    sources = {source["id"]: source for source in configured_sources}
    candidates = []
    for source_id, bucket in state["queues"].items():
        source = sources.get(source_id)
        if not source:
            continue
        for item in bucket["items"]:
            candidates.append((int(item["cursor_id"]), source_id, source, bucket, item))
    candidates.sort(key=lambda value: value[0])

    for _, source_id, source, bucket, item in candidates:
        active_groups = groups_by_source.get(source_id, [])
        if not active_groups:
            continue
        channel_key = _source_channel_key(source_id)
        channel_map = normalize_channel_map(
            await load_last_messages_async(load_last_messages),
            channel_key,
        )
        targets = select_target_groups(channel_map, active_groups, item["cursor_id"])
        if not targets:
            await _remove_queue_item(bucket, item, source_id, active_groups)
            await _save_protection_state_async(state)
            continue

        delivery_config = await _load_source_delivery_config(source_id)
        filter_config = delivery_config["filter"]
        if not check_youtube_video_filter(filter_config, item["post"]):
            await _skip_queue_items(channel_key, active_groups, [item])
            await _remove_queue_item(bucket, item, source_id, active_groups)
            await _save_protection_state_async(state)
            continue

        try:
            enabled_types = get_enabled_message_types(
                filter_config, YOUTUBE_MESSAGE_TYPES
            )
            # 全类型启用时走原有 None 路径，避免筛选改变默认投递格式。
            if enabled_types == set(YOUTUBE_MESSAGE_TYPES):
                enabled_types = None
            rendered = await _render_post(
                item["post"],
                source,
                enabled_types=enabled_types,
            )
            if rendered is None:
                await _skip_queue_items(channel_key, active_groups, [item])
                await _remove_queue_item(bucket, item, source_id, active_groups)
                await _save_protection_state_async(state)
                continue
            text_parts, media_items = rendered
            text_parts = await translate_text_parts(
                text_parts,
                ("youtube", source_id, item["post"]["id"]),
                skip_leading_parts=1,
                config=delivery_config["translation"],
            )
            ok_map = await send_and_record(
                channel_key,
                item["cursor_id"],
                text_parts,
                media_items,
                targets,
                idempotency_key="youtube:%s" % item["id"],
            )
        except Exception as exc:
            logger.warning(
                "YouTube 视频处理失败，保留在限速队列: {} {}: {}",
                source["name"],
                item["post"].get("id"),
                exc,
            )
            return 0, 1

        if not ok_map:
            logger.warning("QQ Bot 未连接，YouTube 视频保留在限速队列: {} {}", source["name"], item["post"]["id"])
            return 0, 0
        if not any(ok_map.values()):
            if getattr(ok_map, "busy_groups", ()):
                return 0, 0
            logger.warning(
                "YouTube 视频全部目标群发送失败，保留在限速队列: {} {}",
                source["name"],
                item["post"]["id"],
            )
            return 0, 1

        if not getattr(ok_map, "sent_groups", ()):
            current_map = normalize_channel_map(
                await load_last_messages_async(load_last_messages),
                channel_key,
            )
            remaining = select_target_groups(
                current_map,
                active_groups,
                item["cursor_id"],
            )
            if not remaining:
                await _remove_queue_item(bucket, item, source_id, active_groups)
            await _save_protection_state_async(state)
            if remaining:
                return 0, 0
            continue

        state["sent_at"].append(now)
        await record(True)
        current_map = normalize_channel_map(
            await load_last_messages_async(load_last_messages),
            channel_key,
        )
        remaining = select_target_groups(current_map, active_groups, item["cursor_id"])
        if not remaining:
            await _remove_queue_item(bucket, item, source_id, active_groups)
        await _save_protection_state_async(state)
        return 1, 0
    await _save_protection_state_async(state)
    return 0, 0


def _update_source_state(
    source_id,
    etag=None,
    clear_etag=False,
    source_url=None,
    channel_id=None,
    channel_title=None,
):
    with _state_file_lock:
        state = _load_state()
        source_state = state["sources"].get(source_id)
        if not isinstance(source_state, dict):
            source_state = {}
            state["sources"][source_id] = source_state
        source_state["last_poll_ts"] = int(time.time())
        if source_url is not None:
            source_state["url"] = source_url
        if clear_etag:
            source_state.pop("etag", None)
        elif etag:
            source_state["etag"] = etag
        if channel_id:
            source_state["channel_id"] = str(channel_id)
        if channel_title:
            source_state["channel_title"] = str(channel_title)
        _save_state(state)


async def _update_source_state_async(*args, **kwargs):
    """Update and persist RSS metadata outside the asyncio event loop."""
    return await asyncio.to_thread(_update_source_state, *args, **kwargs)


async def _resolve_channel_id(source):
    """把 @handle 解析为频道 ID；已缓存时直接复用。"""
    value = str(source.get("value") or "").strip()
    if CHANNEL_ID_RE.fullmatch(value):
        return value
    source_state = (await _load_state_async())["sources"].get(source["id"], {})
    cached = str((source_state or {}).get("channel_id") or "").strip()
    if CHANNEL_ID_RE.fullmatch(cached):
        return cached

    response = await _youtube_get(
        channel_page_url(value),
        timeout=FEED_TIMEOUT,
        headers={"Accept": "text/html"},
    )
    response.raise_for_status()
    info = parse_channel_page(response.text)
    channel_id = str(info.get("channel_id") or "").strip()
    if not CHANNEL_ID_RE.fullmatch(channel_id):
        raise RuntimeError("无法从 YouTube 频道主页解析频道 ID")
    await _update_source_state_async(
        source["id"],
        channel_id=channel_id,
        channel_title=str(info.get("title") or "").strip(),
    )
    return channel_id


async def _fetch_feed(url, etag=None):
    global _last_feed_request_ts
    headers = {"Accept-Encoding": "identity"}
    if isinstance(etag, str) and etag:
        headers["If-None-Match"] = etag.removeprefix("W/")
    last_error = ""
    for attempt in range(FEED_MAX_ATTEMPTS):
        try:
            async with _feed_request_lock:
                elapsed = time.monotonic() - _last_feed_request_ts
                if elapsed < FEED_REQUEST_GAP:
                    await asyncio.sleep(FEED_REQUEST_GAP - elapsed)
                try:
                    response = await _youtube_get(
                        url,
                        headers=headers,
                        timeout=FEED_TIMEOUT,
                    )
                finally:
                    _last_feed_request_ts = time.monotonic()
        except Exception as exc:
            last_error = "请求异常: %s" % exc
            retriable = True
        else:
            if response.status_code == 304:
                return None, etag, None
            if response.status_code == 200 and response.content:
                return response.content, response.headers.get("etag"), None
            last_error = "HTTP %s" % response.status_code
            if response.status_code == 404:
                last_error += "（频道不存在或已关闭 RSS）"
            retriable = response.status_code == 429 or response.status_code >= 500

        if not retriable or attempt + 1 >= FEED_MAX_ATTEMPTS:
            break
        delay = FEED_RETRY_DELAYS[min(attempt, len(FEED_RETRY_DELAYS) - 1)]
        logger.warning(
            "YouTube RSS 拉取临时失败，将在 {} 秒后重试（{}/{}）: {}",
            delay,
            attempt + 1,
            FEED_MAX_ATTEMPTS,
            last_error,
        )
        await asyncio.sleep(delay)
    return None, None, last_error


def _source_channel_key(source_id):
    return "youtube:%s" % source_id


async def _ensure_baseline(channel_key, groups, latest_id):
    def mutate(last):
        channel_map = normalize_channel_map(last, channel_key)
        if not channel_map:
            return apply_baseline(channel_map, groups, latest_id)
        missing = [
            group for group in groups
            if not (channel_map.get(group) or channel_map.get("*"))
        ]
        return apply_baseline(channel_map, missing, latest_id) if missing else False

    return await update_last_messages(mutate)


async def _render_post(
    post,
    source,
    source_label="自动转发来自",
    enabled_types=None,
):
    """视频 dict → (text_parts, media_items) 或 None（无可发送内容）。

    第一期固定「标题 + 链接 + 封面图」，不下载或上传视频本体。
    """
    text_parts = []
    media_items = []

    def type_enabled(kind):
        return enabled_types is None or kind in enabled_types

    source_name = str(
        post.get("author")
        or (source.get("name") if isinstance(source, dict) else "")
        or (source.get("value") if isinstance(source, dict) else "")
        or ""
    ).strip()
    header = (
        "%s YouTube 中[%s]的视频：" % (source_label, source_name)
        if source_name
        else ""
    )
    body_lines = []
    title = str(post.get("title") or "").strip()
    link = str(post.get("link") or "").strip()
    if title and type_enabled("文字"):
        body_lines.append(title)
    if link and type_enabled("链接"):
        body_lines.append(link)

    if header:
        text_parts.append(make_text(header))
    if body_lines:
        text_parts.append(make_text("\n" + "\n".join(body_lines)))

    thumbnail = str(post.get("thumbnail") or "").strip()
    if thumbnail and type_enabled("图片"):
        bytes_map = await fetch_bytes_many([thumbnail])
        data = bytes_map.get(thumbnail)
        if data:
            segment = QQMessageSegment.file_image(data, "youtube-cover.jpg")
        else:
            logger.warning("YouTube 封面下载失败，降级为链接: {}", thumbnail)
            segment = make_media_link("图片", thumbnail)
        media_items.append({"kind": "图片", "url": thumbnail, "segment": segment})

    if not text_parts and not media_items:
        return None
    return text_parts, media_items


async def _send_posts(source, posts):
    if not posts:
        return 0, 0, 0
    source_id = source["id"]
    channel_key = _source_channel_key(source_id)
    active_groups = await _load_source_groups(source_id)
    if not active_groups:
        logger.debug("YouTube 来源无可用目标群，跳过: {}", source["name"])
        return 0, 0, 0

    latest_id = max(posts, key=lambda item: int(item["cursor_id"]))["cursor_id"]
    async with _protection_lock:
        _, load_error = await _load_protection_state_async()
        if load_error:
            raise YoutubeProtectionError(load_error)
        await _ensure_baseline(channel_key, active_groups, latest_id)
        await _flush_deferred_skips(source_id, active_groups)

        state, load_error = await _load_protection_state_async()
        if load_error:
            raise YoutubeProtectionError(load_error)
        bucket = _protection_bucket(state, source_id)
        queued_ids = {item["id"] for item in bucket["items"]}
        deferred_ids = {item["id"] for item in bucket["deferred_skips"]}
        filter_config = await asyncio.to_thread(
            get_youtube_source_filter,
            source_id,
        )
        new_count = 0
        additions = []

        for post in posts:
            post_id = str(post.get("id") or "")
            cursor_id = str(post.get("cursor_id") or "")
            if not post_id or not cursor_id or post_id in queued_ids or post_id in deferred_ids:
                continue
            channel_map = normalize_channel_map(
                await load_last_messages_async(load_last_messages),
                channel_key,
            )
            targets = select_target_groups(channel_map, active_groups, cursor_id)
            if not targets:
                continue
            if not check_youtube_video_filter(filter_config, post):
                await _skip_queue_items(channel_key, active_groups, [{"id": post_id, "cursor_id": cursor_id}])
                continue
            additions.append(_queue_item(post))
            queued_ids.add(post_id)
            new_count += 1

        items = sorted(
            bucket["items"] + additions,
            key=lambda item: int(item["cursor_id"]),
        )
        protection = normalize_protection_config(await _load_protection_config())
        kept, dropped = choose_overflow(
            items,
            protection["backlog_limit"],
            protection["overflow_policy"],
        )
        bucket["items"] = kept
        if dropped:
            if protection["overflow_policy"] == "latest":
                await _skip_queue_items(channel_key, active_groups, dropped)
            else:
                seen = {item["id"] for item in bucket["deferred_skips"]}
                bucket["deferred_skips"].extend(
                    {
                        "id": item["id"],
                        "cursor_id": item["cursor_id"],
                    }
                    for item in dropped
                    if item["id"] not in seen
                )
                bucket["deferred_skips"].sort(key=lambda item: int(item["cursor_id"]))
        if additions or dropped:
            await _save_protection_state_async(state)

        sent_count, failed_count = await _dispatch_one_locked()
        return new_count, sent_count, failed_count


async def _poll_source(source):
    source_id = source["id"]
    channel_id = await _resolve_channel_id(source)
    feed_url = channel_feed_url(channel_id)
    feed_state = (await _load_state_async())["sources"].get(source_id, {})
    if not isinstance(feed_state, dict):
        feed_state = {}
    etag = feed_state.get("etag")
    cached_posts = _posts_cache.get(source_id)

    # ETag 与解析结果绑定在同一 Feed URL；频道变化后必须完整重取。
    if feed_state.get("url") != feed_url:
        etag = None
        cached_posts = None
        _posts_cache.pop(source_id, None)
        await _update_source_state_async(
            source_id,
            clear_etag=True,
            source_url=feed_url,
        )

    body, new_etag, error = await _fetch_feed(feed_url, etag)
    posts = None
    if error is None and body is None:
        if cached_posts is not None:
            posts = cached_posts
        else:
            body, new_etag, error = await _fetch_feed(feed_url, None)

    if error:
        logger.warning(
            "YouTube RSS 拉取失败，本轮跳过该来源: {} ({}): {}",
            source["name"],
            source_id,
            error,
        )
        return {
            "result": "fetch_failed",
            "error": _short_error(error),
            "new": 0,
            "sent": 0,
            "failed": 0,
        }

    if posts is None:
        try:
            posts = parse_feed(body) if body else []
        except Exception as exc:
            logger.warning(
                "YouTube RSS 解析失败，本轮跳过该来源: {} ({}): {}",
                source["name"],
                source_id,
                exc,
            )
            await _update_source_state_async(source_id, clear_etag=True)
            return {
                "result": "parse_failed",
                "error": _short_error(exc),
                "new": 0,
                "sent": 0,
                "failed": 0,
            }

        await _update_source_state_async(
            source_id,
            etag=new_etag,
            clear_etag=not bool(new_etag),
            source_url=feed_url,
        )
        _posts_cache[source_id] = posts

    counts = await _send_posts(source, posts)
    return {
        "result": "ok",
        "error": "",
        "new": counts[0],
        "sent": counts[1],
        "failed": counts[2],
    }


async def _poll_all():
    configured_sources, groups_by_source = await _load_polling_snapshot()
    configured_ids = {str(source.get("id") or "") for source in configured_sources}
    for cached_key in list(_posts_cache):
        if cached_key not in configured_ids:
            _posts_cache.pop(cached_key, None)

    eligible_sources = [
        source
        for source in configured_sources
        if source["enabled"] and groups_by_source.get(source["id"])
    ]
    sources = []
    seen_source_ids = set()
    for source in eligible_sources:
        source_id = str(source.get("id") or "").strip()
        if source_id in seen_source_ids:
            logger.warning("YouTube 配置存在重复来源 ID，跳过后续轮询: {}", source_id)
            continue
        seen_source_ids.add(source_id)
        sources.append(source)
    if not sources:
        _runtime_status["source_health"] = []
        return
    sem = asyncio.Semaphore(YOUTUBE_POLL_CONCURRENCY)

    async def poll_one(source):
        async with sem:
            try:
                return await _poll_source(source)
            except YoutubeProtectionError as exc:
                return {"result": "protection_failed", "error": _short_error(exc), "new": 0, "sent": 0, "failed": 0}
            except Exception as exc:
                logger.exception("YouTube 来源轮询异常: {} ({})", source["name"], source["id"])
                return {"result": "fetch_failed", "error": _short_error(exc), "new": 0, "sent": 0, "failed": 0}

    results = await asyncio.gather(*(poll_one(source) for source in sources))
    previous = {item["id"]: item for item in _runtime_status.get("source_health", [])}
    health = []
    for source, result in zip(sources, results):
        identity = str(source["id"])
        status = result.get("result")
        count = 0 if status == "ok" else (
            previous.get(identity, {}).get("consecutive_failures", 0) + 1
        )
        health.append({"id": identity, "name": source["name"], "consecutive_failures": count})
    _runtime_status["source_health"] = health
    new_total = sent_total = failed_total = 0
    bad_result = ""
    bad_error = ""
    for result in results:
        new_total += result.get("new", 0)
        sent_total += result.get("sent", 0)
        failed_total += result.get("failed", 0)
        status = result.get("result")
        if not bad_result and status not in (None, "ok"):
            bad_result = status
            bad_error = result.get("error") or ""
    _runtime_status.update({
        "last_poll_ts": int(time.time()),
        "last_new_count": new_total,
        "last_sent_count": sent_total,
        "last_failed_count": failed_total,
        "last_result": bad_result or "ok",
        "last_error": bad_error,
    })


async def _poll_loop():
    await run_rss_poll_loop(
        source_name="YouTube RSS",
        has_connected_bot=lambda: any(
            isinstance(bot, QQBot) for bot in get_bots().values()
        ),
        poll_all=_poll_all,
        read_interval=_read_poll_interval,
        logger=logger,
    )


async def _read_poll_interval():
    """Read the poll interval off-loop and keep the worker alive on read errors."""
    return await read_rss_poll_interval(
        get_youtube_poll_interval,
        DEFAULT_YOUTUBE_POLL_INTERVAL,
        source_name="YouTube RSS",
        logger=logger,
    )


_loop_started = False
_poll_task = None
_poll_stopping = False


def _on_poll_task_done(task):
    """Keep the panel state and startup guard aligned with the actual task."""
    global _loop_started, _poll_task
    if _poll_task is task:
        _poll_task = None
        _loop_started = False
        _runtime_status["loop_started"] = False
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.error("YouTube RSS 轮询任务异常退出: {}", error)


def _ensure_poll_loop_started():
    global _loop_started, _poll_task
    if _poll_stopping:
        return
    if _poll_task is not None and not _poll_task.done():
        return
    _loop_started = True
    _runtime_status["loop_started"] = True
    logger.info("YouTube RSS 轮询后台任务已启动；QQ 连接状态每 5 秒检查一次")
    _poll_task = asyncio.create_task(_poll_loop())
    _poll_task.add_done_callback(_on_poll_task_done)


@get_driver().on_startup
async def _start_poll_loop():
    """Start polling at application startup, before QQ connection hooks run."""
    _ensure_poll_loop_started()


_youtube_app = get_app()


@_youtube_app.get("/api/youtube/status")
async def api_youtube_status():
    bots = get_bots()
    status_config = await asyncio.to_thread(get_youtube_status_snapshot)
    sources = status_config["sources"]
    data = runtime_status()
    protection_state, protection_load_error = await _load_protection_state_async()
    data.update({
        "ok": True,
        "running": bool(data.get("loop_started")),
        "qq_connected": any(isinstance(bot, QQBot) for bot in bots.values()),
        "poll_interval": await asyncio.to_thread(get_youtube_poll_interval),
        "sources_total": len(sources),
        "sources_enabled": sum(1 for source in sources if source["enabled"]),
        "forwarding_groups": len(status_config["forwarding_groups"]),
        "protection": status_config["protection"],
        "protection_queue_pending": sum(
            len(bucket.get("items") or [])
            for bucket in (protection_state.get("queues") or {}).values()
            if isinstance(bucket, dict)
        ),
    })
    data["protection_error"] = protection_load_error
    if protection_load_error:
        data["last_result"] = "protection_failed"
        data["last_error"] = protection_load_error
    return data
