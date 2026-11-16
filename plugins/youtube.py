"""YouTube 频道 → QQ 自动转发（第一期：标题、封面与链接）。

来源只支持频道（频道 ID 或 ``@handle``），配置 Data API Key 时通过官方
YouTube Data API 拉取并以官方 ``feeds/videos.xml`` 备用；不解析视频本体，
不做关键词/搜索来源。
投递复用 translation/retry/sender，并沿用 Bilibili 的基线、限速与积压
保护，不改变 Discord、X、Bilibili 或 QQ→QQ 的路由与配置。
"""

from __future__ import annotations

import asyncio
import copy
import email.utils
import json
import os
import threading
import time

import httpx

from nonebot import get_app, get_bots, get_driver, logger
from plugins import personal_qq
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
from plugins.proxy_guard import loopback_http_proxy, system_loopback_http_proxy
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
    parse_api_playlist_items,
    parse_channel_videos_page,
    parse_video_page_post,
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
# YouTube RSS occasionally returns a transient 404 for valid channel feeds.
FEED_404_RETRY_DELAYS = (1, 2)
# YouTube 官方 RSS 没有公开频率说明；多个来源共用同一客户端，保持间隔。
FEED_REQUEST_GAP = 2
YOUTUBE_POLL_CONCURRENCY = 3
DEFAULT_YOUTUBE_POLL_INTERVAL = 300
YOUTUBE_DATA_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_DATA_API_REQUEST_GAP = 1
YOUTUBE_API_MAX_PAGES = 20
YOUTUBE_API_SERVER_COOLDOWN = 30
YOUTUBE_API_MAX_SERVER_COOLDOWN = 900
YOUTUBE_API_RATE_LIMIT_COOLDOWN = 300
YOUTUBE_API_CONFIG_COOLDOWN = 3600
YOUTUBE_API_QUOTA_COOLDOWN = 21600
YOUTUBE_WEB_FALLBACK_COOLDOWN = 900
YOUTUBE_RSS_404_COOLDOWN = 900
YOUTUBE_STATE_FILE = data_path("youtube.json")
YOUTUBE_PROTECTION_FILE = data_path("youtube_protection.json")

_feed_client = None
_feed_proxy = None
_feed_clients = {}
_feed_proxy_override = None
_feed_proxy_override_source = None
_feed_request_tasks = set()
_feed_stopping = False
_state_file_lock = threading.Lock()
_protection_file_lock = threading.Lock()
_protection_state_lock = threading.RLock()
_protection_lock = asyncio.Lock()
_feed_request_lock = asyncio.Lock()
_last_feed_request_ts = 0.0
_feed_cooldown_until = 0.0
_web_fallback_cooldown_until = 0.0
_youtube_rss_source_cooldowns = {}
_youtube_api_request_lock = asyncio.Lock()
_last_youtube_api_request_ts = 0.0
_youtube_api_cooldown_until = 0.0
_youtube_api_cooldown_reason = ""
_youtube_api_cooldown_rate_limited = False
_youtube_api_failure_count = 0
_posts_cache = {}
_video_page_cache = {}
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


class YoutubeDataAPIError(RuntimeError):
    """YouTube Data API 请求失败，携带可用于退避与降级的状态信息。"""

    def __init__(self, message, status_code=None, reason="", rate_limited=False):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.rate_limited = bool(rate_limited)


def _short_error(error, limit=160):
    return " ".join(str(error or "").split())[:limit]


def _http_response_diagnostics(response):
    """Return a bounded summary that helps distinguish YouTube from proxy errors."""
    url = _short_error(getattr(response, "url", ""), 300) or "未知"
    server = _short_error(response.headers.get("server"), 100) or "未知"
    via = _short_error(response.headers.get("via"), 100) or "无"
    content_type = _short_error(response.headers.get("content-type"), 100) or "未知"
    try:
        snippet = response.content[:1024].decode(
            response.encoding or "utf-8", "replace"
        )
        snippet = _short_error(snippet, 240) or "空"
    except Exception:
        snippet = "不可读取"
    return "url=%s server=%s via=%s content-type=%s body=%s" % (
        url,
        server,
        via,
        content_type,
        snippet,
    )


def _describe_exception(exc):
    details = []
    current = exc
    seen = set()
    while current is not None and id(current) not in seen and len(details) < 4:
        seen.add(id(current))
        detail = _short_error(current, 140)
        if detail and detail not in details:
            details.append(detail)
        current = current.__cause__ or current.__context__
    return "%s: %s" % (
        type(exc).__name__,
        " ← ".join(details) if details else "无详细说明",
    )


def _read_discord_proxy():
    try:
        proxy = getattr(get_driver().config, "discord_proxy", None)
    except Exception:
        return None
    if isinstance(proxy, str):
        proxy = proxy.strip()
        return proxy or None
    return None


def _read_youtube_proxy():
    proxy = str(os.environ.get("YOUTUBE_PROXY") or "").strip()
    return proxy or _read_discord_proxy()


def _effective_feed_proxy():
    global _feed_proxy_override, _feed_proxy_override_source
    configured = _read_youtube_proxy()
    if _feed_proxy_override is not None:
        if configured == _feed_proxy_override_source:
            return _feed_proxy_override
        # 运行时代理配置发生变化后，丢弃上一次连接错误时选出的备用代理。
        _feed_proxy_override = None
        _feed_proxy_override_source = None
    return configured


def _get_feed_client(proxy=None, explicit_proxy=False):
    global _feed_client, _feed_proxy
    if not explicit_proxy:
        proxy = _effective_feed_proxy()
    client = _feed_clients.get(proxy)
    if client is None or client.is_closed:
        # 保留各代理的客户端，切换备用代理时不关闭仍有并发请求在使用的客户端。
        client = httpx.AsyncClient(
            proxy=proxy,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": "WRelay YouTube RSS"},
        )
        _feed_clients[proxy] = client
    _feed_client = client
    _feed_proxy = proxy
    return client


def _same_proxy_endpoint(left, right):
    if left == right:
        return True
    left_endpoint = loopback_http_proxy(left)
    right_endpoint = loopback_http_proxy(right)
    return left_endpoint is not None and left_endpoint == right_endpoint


async def _youtube_get(*args, **kwargs):
    """Track YouTube requests so shutdown can reap one-shot calls."""
    global _feed_proxy_override, _feed_proxy_override_source
    if _feed_stopping:
        raise asyncio.CancelledError("YouTube RSS HTTP client is shutting down")
    task = asyncio.current_task()
    if task is not None:
        _feed_request_tasks.add(task)
    try:
        configured_proxy = _read_youtube_proxy()
        primary_proxy = _effective_feed_proxy()
        client = _get_feed_client(proxy=primary_proxy, explicit_proxy=True)
        try:
            return await client.get(*args, **kwargs)
        except (httpx.ConnectError, httpx.ProxyError) as primary_error:
            # 只对本机/未显式指定的代理配置回退，避免覆盖用户指定的远程代理。
            if configured_proxy and loopback_http_proxy(configured_proxy) is None:
                raise
            system_proxy = system_loopback_http_proxy()
            if not system_proxy or _same_proxy_endpoint(system_proxy, primary_proxy):
                raise
            try:
                response = await _get_feed_client(
                    proxy=system_proxy,
                    explicit_proxy=True,
                ).get(*args, **kwargs)
            except Exception as retry_error:
                raise RuntimeError(
                    "默认连接路径失败；Windows 系统代理重试失败: %s"
                    % _describe_exception(retry_error)
                ) from retry_error
            _feed_proxy_override = system_proxy
            _feed_proxy_override_source = configured_proxy
            logger.warning("YouTube 请求通过 Windows 当前系统代理重试成功")
            return response
    finally:
        if task is not None:
            _feed_request_tasks.discard(task)


def _youtube_data_api_key():
    """Read the server-side Data API key loaded from .env.prod."""
    return str(os.environ.get("YOUTUBE_DATA_API_KEY") or "").strip()


def _retry_after_seconds(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except (OverflowError, TypeError, ValueError):
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        return max(0.0, parsed.timestamp() - time.time())
    except (OverflowError, TypeError, ValueError):
        return None


def _api_error_details(response):
    try:
        payload = response.json()
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        error = {}
    errors = error.get("errors")
    reason = ""
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict) and item.get("reason"):
                reason = _short_error(item.get("reason"), 80)
                break
    reason = reason or _short_error(error.get("status"), 80)
    message = _short_error(error.get("message"), 180)
    if not message:
        message = "HTTP %s" % response.status_code
    return reason, message


def _set_youtube_api_cooldown(delay, reason, rate_limited=False):
    global _youtube_api_cooldown_until, _youtube_api_cooldown_reason
    global _youtube_api_cooldown_rate_limited
    delay = max(0.0, float(delay or 0))
    if delay <= 0:
        return
    until = time.monotonic() + delay
    if until >= _youtube_api_cooldown_until:
        _youtube_api_cooldown_until = until
        _youtube_api_cooldown_reason = _short_error(reason, 100)
        _youtube_api_cooldown_rate_limited = bool(rate_limited)


async def _youtube_data_api_get(endpoint, params):
    """Call a public YouTube Data API endpoint without putting its key in URLs."""
    global _last_youtube_api_request_ts, _youtube_api_failure_count
    global _youtube_api_cooldown_until, _youtube_api_cooldown_reason
    global _youtube_api_cooldown_rate_limited
    key = _youtube_data_api_key()
    if not key:
        raise YoutubeDataAPIError("YOUTUBE_DATA_API_KEY 未配置")

    async with _youtube_api_request_lock:
        remaining = _youtube_api_cooldown_until - time.monotonic()
        if remaining > 0:
            reason = _youtube_api_cooldown_reason or "上游请求退避中"
            raise YoutubeDataAPIError(
                "YouTube Data API 退避中（约 %.0f 秒）：%s" % (remaining, reason),
                status_code=429 if _youtube_api_cooldown_rate_limited else 503,
                reason=reason,
                rate_limited=_youtube_api_cooldown_rate_limited,
            )

        elapsed = time.monotonic() - _last_youtube_api_request_ts
        if elapsed < YOUTUBE_DATA_API_REQUEST_GAP:
            await asyncio.sleep(YOUTUBE_DATA_API_REQUEST_GAP - elapsed)
        try:
            response = await _youtube_get(
                "%s/%s" % (YOUTUBE_DATA_API_BASE, endpoint),
                params=params,
                headers={"Accept": "application/json", "X-Goog-Api-Key": key},
                timeout=FEED_TIMEOUT,
            )
        except Exception as exc:
            _last_youtube_api_request_ts = time.monotonic()
            _youtube_api_failure_count += 1
            delay = min(
                YOUTUBE_API_SERVER_COOLDOWN * (2 ** min(_youtube_api_failure_count - 1, 5)),
                YOUTUBE_API_MAX_SERVER_COOLDOWN,
            )
            reason = _describe_exception(exc)
            _set_youtube_api_cooldown(delay, reason)
            raise YoutubeDataAPIError(
                "YouTube Data API 连接失败，退避 %.0f 秒：%s" % (delay, reason),
                reason="connection",
            ) from exc
        finally:
            _last_youtube_api_request_ts = time.monotonic()

        if response.status_code >= 400:
            reason, message = _api_error_details(response)
            retry_after = _retry_after_seconds(response.headers.get("retry-after"))
            lower_reason = reason.lower()
            rate_limited = response.status_code == 429 or lower_reason in {
                "ratelimitexceeded", "userratelimitexceeded",
            }
            if rate_limited:
                delay = retry_after if retry_after is not None else YOUTUBE_API_RATE_LIMIT_COOLDOWN
                _set_youtube_api_cooldown(delay, reason or message, rate_limited=True)
            elif response.status_code >= 500:
                _youtube_api_failure_count += 1
                delay = retry_after if retry_after is not None else min(
                    YOUTUBE_API_SERVER_COOLDOWN * (2 ** min(_youtube_api_failure_count - 1, 5)),
                    YOUTUBE_API_MAX_SERVER_COOLDOWN,
                )
                _set_youtube_api_cooldown(delay, reason or message)
            elif lower_reason in {"quotaexceeded", "dailylimitexceeded"}:
                _set_youtube_api_cooldown(
                    YOUTUBE_API_QUOTA_COOLDOWN, reason or message
                )
            elif lower_reason in {"keyinvalid", "accessnotconfigured", "forbidden"}:
                _set_youtube_api_cooldown(
                    YOUTUBE_API_CONFIG_COOLDOWN, reason or message
                )
            raise YoutubeDataAPIError(
                "YouTube Data API HTTP %s%s：%s" % (
                    response.status_code,
                    " (%s)" % reason if reason else "",
                    message,
                ),
                status_code=response.status_code,
                reason=reason,
                rate_limited=rate_limited,
            )

        try:
            payload = response.json()
        except Exception as exc:
            raise YoutubeDataAPIError(
                "YouTube Data API 返回了无效 JSON：%s" % _short_error(exc)
            ) from exc
        if not isinstance(payload, dict):
            raise YoutubeDataAPIError("YouTube Data API 返回格式无效")
        _youtube_api_failure_count = 0
        _youtube_api_cooldown_until = 0.0
        _youtube_api_cooldown_reason = ""
        _youtube_api_cooldown_rate_limited = False
        return payload


@get_driver().on_shutdown
async def _close_feed_client():
    global _feed_client, _feed_proxy, _feed_clients, _feed_proxy_override
    global _feed_proxy_override_source, _poll_task, _loop_started, _poll_stopping, _feed_stopping
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
    clients = list({id(client): client for client in _feed_clients.values()}.values())
    if _feed_client is not None and all(client is not _feed_client for client in clients):
        clients.append(_feed_client)
    if clients:
        await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)
    _feed_clients.clear()
    _feed_client = None
    _feed_proxy = None
    _feed_proxy_override = None
    _feed_proxy_override_source = None


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
    uploads_playlist_id=None,
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
        if uploads_playlist_id:
            source_state["uploads_playlist_id"] = str(uploads_playlist_id)
        _save_state(state)


async def _update_source_state_async(*args, **kwargs):
    """Update and persist RSS metadata outside the asyncio event loop."""
    return await asyncio.to_thread(_update_source_state, *args, **kwargs)


async def _resolve_channel_id(source, force_refresh=False):
    """把 @handle 解析为频道 ID；强制刷新时跳过已有缓存。"""
    value = str(source.get("value") or "").strip()
    if CHANNEL_ID_RE.fullmatch(value):
        return value
    source_state = (await _load_state_async())["sources"].get(source["id"], {})
    cached = str((source_state or {}).get("channel_id") or "").strip()
    if not force_refresh and CHANNEL_ID_RE.fullmatch(cached):
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


async def _resolve_channel_uploads(source, force_refresh=False):
    """Resolve and cache a channel's official uploads playlist via Data API."""
    value = str(source.get("value") or "").strip()
    source_state = (await _load_state_async())["sources"].get(source["id"], {})
    if not isinstance(source_state, dict):
        source_state = {}
    cached_channel_id = str(source_state.get("channel_id") or "").strip()
    cached_playlist_id = str(source_state.get("uploads_playlist_id") or "").strip()
    if (
        not force_refresh
        and CHANNEL_ID_RE.fullmatch(cached_channel_id)
        and cached_playlist_id
        and (not CHANNEL_ID_RE.fullmatch(value) or value == cached_channel_id)
    ):
        return cached_channel_id, cached_playlist_id

    params = {"part": "id,snippet,contentDetails", "maxResults": 1}
    if CHANNEL_ID_RE.fullmatch(value):
        params["id"] = value
    else:
        handle = value[1:] if value.startswith("@") else value
        params["forHandle"] = handle
    payload = await _youtube_data_api_get("channels", params)
    items = payload.get("items")
    item = items[0] if isinstance(items, list) and items else None
    if not isinstance(item, dict):
        raise YoutubeDataAPIError(
            "YouTube Data API 未找到频道：%s" % value,
            status_code=404,
            reason="channelNotFound",
        )
    channel_id = str(item.get("id") or "").strip()
    content = item.get("contentDetails")
    related = content.get("relatedPlaylists") if isinstance(content, dict) else None
    if not isinstance(related, dict):
        related = {}
    playlist_id = str(related.get("uploads") or "").strip()
    snippet = item.get("snippet")
    title = str((snippet or {}).get("title") or "").strip()
    if not CHANNEL_ID_RE.fullmatch(channel_id) or not playlist_id:
        raise YoutubeDataAPIError(
            "YouTube Data API 未返回有效频道 ID 或上传列表 ID",
            status_code=404,
            reason="channelNotFound",
        )
    await _update_source_state_async(
        source["id"],
        channel_id=channel_id,
        channel_title=title,
        uploads_playlist_id=playlist_id,
    )
    return channel_id, playlist_id


async def _source_base_cursor(source_id):
    active_groups = await _load_source_groups(source_id)
    if not active_groups:
        return None
    last = await load_last_messages_async(load_last_messages)
    channel_map = normalize_channel_map(last, _source_channel_key(source_id))
    cursors = [
        str(channel_map.get(group) or channel_map.get("*") or "").strip()
        for group in active_groups
    ]
    cursors = [cursor for cursor in cursors if _valid_cursor_id(cursor)]
    return min(cursors, key=int) if cursors else None


async def _fetch_youtube_api_posts(source, channel_id, uploads_playlist_id):
    base_cursor = await _source_base_cursor(source["id"])
    next_page_token = ""
    all_posts = {}
    watermark_reached = base_cursor is None
    for _ in range(YOUTUBE_API_MAX_PAGES):
        params = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": 50,
        }
        if next_page_token:
            params["pageToken"] = next_page_token
        payload = await _youtube_data_api_get("playlistItems", params)
        items = payload.get("items")
        if not isinstance(items, list):
            raise YoutubeDataAPIError("YouTube Data API 上传列表缺少 items")
        page_posts = parse_api_playlist_items(items, channel_id)
        for post in page_posts:
            all_posts[post["id"]] = post
        next_page_token = str(payload.get("nextPageToken") or "")
        if not next_page_token:
            watermark_reached = True
            break
        if base_cursor is None:
            break
        if page_posts and min(int(post["cursor_id"]) for post in page_posts) <= int(base_cursor):
            watermark_reached = True
            break
    if next_page_token and not watermark_reached:
        raise YoutubeDataAPIError(
            "YouTube 上传列表超过单轮回补上限，游标保持不变并将在下轮继续读取",
            reason="catchupLimit",
        )
    return sorted(all_posts.values(), key=lambda post: int(post["cursor_id"]))


async def _fetch_feed(url, etag=None):
    global _last_feed_request_ts, _feed_cooldown_until
    headers = {"Accept-Encoding": "identity"}
    if isinstance(etag, str) and etag:
        headers["If-None-Match"] = etag.removeprefix("W/")
    last_error = ""
    last_status = None
    for attempt in range(FEED_MAX_ATTEMPTS):
        remaining_cooldown = _feed_cooldown_until - time.monotonic()
        if remaining_cooldown > 0:
            return (
                None,
                None,
                "HTTP 429 限流退避中（约 %.0f 秒）" % remaining_cooldown,
                429,
            )
        retry_delay = None
        max_attempts = FEED_MAX_ATTEMPTS
        try:
            async with _feed_request_lock:
                remaining_cooldown = _feed_cooldown_until - time.monotonic()
                if remaining_cooldown > 0:
                    return (
                        None,
                        None,
                        "HTTP 429 限流退避中（约 %.0f 秒）" % remaining_cooldown,
                        429,
                    )
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
            last_error = "请求异常: %s" % _describe_exception(exc)
            last_status = None
            retriable = True
        else:
            last_status = response.status_code
            if response.status_code == 304:
                _feed_cooldown_until = 0.0
                return None, etag, None, 304
            if response.status_code == 200 and response.content:
                _feed_cooldown_until = 0.0
                return response.content, response.headers.get("etag"), None, 200
            last_error = "HTTP %s" % response.status_code
            if response.status_code == 429:
                retry_after = _retry_after_seconds(response.headers.get("retry-after"))
                retry_delay = (
                    retry_after
                    if retry_after is not None
                    else YOUTUBE_API_RATE_LIMIT_COOLDOWN
                )
                _feed_cooldown_until = max(
                    _feed_cooldown_until,
                    time.monotonic() + retry_delay,
                )
                last_error += "（已按 Retry-After/限流退避）"
                retriable = False
            elif response.status_code == 404:
                last_error += "（官方 RSS 地址返回 404；可能是频道 ID、RSS 状态或代理响应问题）"
                max_attempts = min(
                    FEED_MAX_ATTEMPTS, len(FEED_404_RETRY_DELAYS) + 1
                )
                retriable = attempt + 1 < max_attempts
                if retriable:
                    retry_delay = FEED_404_RETRY_DELAYS[attempt]
                else:
                    logger.warning(
                        "YouTube RSS HTTP 404 响应诊断: {}",
                        _http_response_diagnostics(response),
                    )
            else:
                retriable = response.status_code >= 500

        if not retriable or attempt + 1 >= max_attempts:
            break
        delay = retry_delay
        if delay is None:
            delay = FEED_RETRY_DELAYS[min(attempt, len(FEED_RETRY_DELAYS) - 1)]
        logger.warning(
            "YouTube RSS 拉取临时失败，将在 {} 秒后重试（{}/{}）: {}",
            delay,
            attempt + 1,
            max_attempts,
            last_error,
        )
        await asyncio.sleep(delay)
    return None, None, last_error, last_status


async def _fetch_channel_videos_fallback(channel_id):
    """RSS 失效时从频道视频页读取并核实视频发布时间。"""
    response = await _youtube_get(
        channel_page_url(channel_id) + "/videos",
        timeout=FEED_TIMEOUT,
        headers={"Accept-Language": "en-US,en;q=0.9", "User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    videos, author = parse_channel_videos_page(response.text, channel_id)
    sem = asyncio.Semaphore(3)

    async def read_video(video):
        key = (channel_id, video["video_id"])
        if key in _video_page_cache:
            return dict(_video_page_cache[key], title=video["title"], author=author)
        async with sem:
            page = await _youtube_get(
                "https://www.youtube.com/watch?v=" + video["video_id"],
                timeout=FEED_TIMEOUT,
                headers={"Accept-Language": "en-US,en;q=0.9", "User-Agent": "Mozilla/5.0"},
            )
            page.raise_for_status()
            post = parse_video_page_post(page.text, video, channel_id, author)
            _video_page_cache[key] = post
            return post

    posts = await asyncio.gather(*(read_video(video) for video in videos))
    return sorted(posts, key=lambda item: int(item["cursor_id"]))


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


async def _poll_source_rss(source, allow_page_fallback=True):
    global _web_fallback_cooldown_until
    source_id = source["id"]
    cooldown_until = _youtube_rss_source_cooldowns.get(source_id, 0.0)
    remaining = cooldown_until - time.monotonic()
    if remaining > 0:
        return {
            "result": "fetch_failed",
            "provider": "rss",
            "error": "YouTube RSS 404 冷却中（约 %.0f 秒），频道 ID 未确认变化时暂不重复抓取" % remaining,
            "new": 0,
            "sent": 0,
            "failed": 0,
        }
    _youtube_rss_source_cooldowns.pop(source_id, None)
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

    body, new_etag, error, status_code = await _fetch_feed(feed_url, etag)
    posts = None
    if error is None and body is None:
        if cached_posts is not None:
            posts = cached_posts
        else:
            body, new_etag, error, status_code = await _fetch_feed(feed_url, None)

    source_value = str(source.get("value") or "").strip()
    if status_code == 404 and not CHANNEL_ID_RE.fullmatch(source_value):
        try:
            refreshed_channel_id = await _resolve_channel_id(
                source, force_refresh=True
            )
        except Exception as exc:
            refresh_error = _describe_exception(exc)
            logger.warning(
                "YouTube RSS 404 后重新解析频道 handle 失败: {} ({})",
                source_value,
                refresh_error,
            )
            error = "%s；重新解析 %s 失败: %s" % (
                error,
                source_value,
                refresh_error,
            )
        else:
            if refreshed_channel_id == channel_id:
                logger.warning(
                    "YouTube RSS 404 后重新解析 handle，频道 ID 未变化: {} -> {}",
                    source_value,
                    channel_id,
                )
                error = "%s；重新解析后频道 ID 未变化，请核对频道 RSS 与代理响应" % error
            else:
                logger.warning(
                    "YouTube RSS 404 后频道 ID 已更新，将使用新 ID 重试: {} -> {}",
                    channel_id,
                    refreshed_channel_id,
                )
                channel_id = refreshed_channel_id
                feed_url = channel_feed_url(channel_id)
                _posts_cache.pop(source_id, None)
                await _update_source_state_async(
                    source_id,
                    clear_etag=True,
                    source_url=feed_url,
                )
                body, new_etag, error, status_code = await _fetch_feed(
                    feed_url, None
                )

    if error and allow_page_fallback and status_code != 429:
        remaining = _web_fallback_cooldown_until - time.monotonic()
        if remaining > 0:
            error += "；频道视频页后备抓取限流冷却中（约 %.0f 秒）" % remaining
        else:
            try:
                posts = await _fetch_channel_videos_fallback(channel_id)
            except Exception as exc:
                response = getattr(exc, "response", None)
                if getattr(response, "status_code", None) == 429:
                    retry_after = _retry_after_seconds(
                        getattr(response, "headers", {}).get("retry-after")
                    )
                    delay = retry_after if retry_after is not None else YOUTUBE_WEB_FALLBACK_COOLDOWN
                    _web_fallback_cooldown_until = max(
                        _web_fallback_cooldown_until,
                        time.monotonic() + delay,
                    )
                    error += "；频道视频页返回 HTTP 429，后备抓取冷却 %.0f 秒" % delay
                logger.warning("YouTube 频道视频页后备读取失败: {} ({})", source_id, _describe_exception(exc))
                error += "；频道视频页后备读取失败: " + _describe_exception(exc)
            else:
                logger.info("YouTube RSS 不可用，已从频道视频页读取 {} 条视频: {}", len(posts), source_id)
                error = None
                await _update_source_state_async(source_id, clear_etag=True, source_url=feed_url)

    if error:
        if status_code == 404:
            _youtube_rss_source_cooldowns[source_id] = (
                time.monotonic() + YOUTUBE_RSS_404_COOLDOWN
            )
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
    _youtube_rss_source_cooldowns.pop(source_id, None)
    return {
        "result": "ok",
        "provider": "rss",
        "error": "",
        "new": counts[0],
        "sent": counts[1],
        "failed": counts[2],
    }


async def _poll_source_data_api(source):
    channel_id, uploads_playlist_id = await _resolve_channel_uploads(source)
    return await _fetch_youtube_api_posts(
        source,
        channel_id,
        uploads_playlist_id,
    )


async def _poll_source(source):
    if not _youtube_data_api_key():
        result = await _poll_source_rss(source)
        result.setdefault("provider", "rss")
        return result

    try:
        posts = await _poll_source_data_api(source)
    except YoutubeDataAPIError as api_error:
        api_message = _short_error(api_error)
        if api_error.rate_limited:
            logger.warning(
                "YouTube Data API 限流，本轮遵循退避且不再请求 RSS/频道页: {} ({})",
                source["name"],
                api_message,
            )
            return {
                "result": "fetch_failed",
                "provider": "data_api",
                "error": api_message,
                "new": 0,
                "sent": 0,
                "failed": 0,
            }
        logger.warning(
            "YouTube Data API 来源读取失败，尝试 RSS 备用源: {} ({})",
            source["name"],
            api_message,
        )
    except Exception as exc:
        api_message = _short_error(_describe_exception(exc))
        logger.warning(
            "YouTube Data API 来源读取异常，尝试 RSS 备用源: {} ({})",
            source["name"],
            api_message,
        )
    else:
        counts = await _send_posts(source, posts)
        return {
            "result": "ok",
            "provider": "data_api",
            "error": "",
            "new": counts[0],
            "sent": counts[1],
            "failed": counts[2],
        }

    try:
        result = await _poll_source_rss(source)
    except Exception as exc:
        result = {
            "result": "fetch_failed",
            "error": _short_error(_describe_exception(exc)),
            "new": 0,
            "sent": 0,
            "failed": 0,
        }
    result["provider"] = "rss_fallback"
    if result.get("result") != "ok":
        result["error"] = _short_error(
            "Data API：%s；RSS 备用源：%s"
            % (api_message, result.get("error") or result.get("result") or "失败")
        )
    else:
        logger.warning(
            "YouTube Data API 不可用，本轮由 RSS 备用源成功处理: {}",
            source["name"],
        )
    return result


async def _poll_all_profile():
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
        health.append({
            "id": identity,
            "name": source["name"],
            "consecutive_failures": count,
            "provider": result.get("provider", "rss"),
        })
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
        ) or personal_qq.any_connected(),
        poll_all=_poll_all,
        read_interval=lambda: _profile_runtime.interval(_read_poll_interval),
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


from plugins.profile_runtime import RSSProfiles
_profile_runtime = RSSProfiles(globals(), 'youtube')


async def _poll_all():
    return await _profile_runtime.poll(_poll_all_profile)


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


def _youtube_data_api_status():
    return {
        "data_api_enabled": bool(_youtube_data_api_key()),
        "data_api_cooldown_seconds": max(
            0,
            int(_youtube_api_cooldown_until - time.monotonic() + 0.999),
        ),
    }


@_youtube_app.get("/api/youtube/status")
async def api_youtube_status(bot_id: str = ""):
    if bot_id:
        data = await _profile_runtime.status(bot_id)
        data.update(_youtube_data_api_status())
        return data
    if await asyncio.to_thread(_profile_runtime.owners):
        data = await _profile_runtime.status_all()
        data.update(_youtube_data_api_status())
        return data
    bots = get_bots()
    status_config = await asyncio.to_thread(get_youtube_status_snapshot)
    sources = status_config["sources"]
    data = runtime_status()
    protection_state, protection_load_error = await _load_protection_state_async()
    data.update({
        **_youtube_data_api_status(),
        "ok": True,
        "running": bool(data.get("loop_started")),
        "qq_connected": any(isinstance(bot, QQBot) for bot in bots.values()) or personal_qq.any_connected(),
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
