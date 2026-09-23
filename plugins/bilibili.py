"""Bilibili 投稿/标签/关键词 → QQ 自动转发。

链路：RSSHub 拉取 → bilibili_parser 解析 → 来源筛选 → 独立游标去重 →
复用 translation/retry/sender 投递到 QQ。该模块不改变 Discord、X 或 QQ→QQ
的路由和配置。
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx

from nonebot import get_app, get_bots, get_driver, logger
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

from plugins.bilibili_parser import format_post_text, parse_feed
from plugins.config import (
    get_bilibili_forwarding_groups,
    get_bilibili_groups_for_source,
    get_bilibili_poll_interval,
    get_bilibili_protection,
    get_bilibili_rsshub_base,
    get_bilibili_source_filter,
    get_bilibili_source_translation_config,
    get_bilibili_sources,
)
from plugins.dedup import (
    apply_baseline,
    apply_skipped,
    normalize_channel_map,
    select_target_groups,
)
from plugins.filter import check_bilibili_video_filter
from plugins.bilibili_rate_limit import (
    choose_overflow,
    normalize_protection_config,
    prune_sent_timestamps,
    rate_wait_seconds,
)
from plugins.history import load_last_messages, update_last_messages
from plugins.json_io import atomic_write_json, load_json
from plugins.media import fetch_bytes_many, make_media_link, make_text
from plugins.paths import data_path
from plugins.retry import send_and_record
from plugins.stats import record
from plugins.translation import translate_text_parts


FEED_TIMEOUT = 30
# RSSHub 会继续请求 Bilibili 上游；多个来源同时刷新容易触发上游拦截。
FEED_MAX_ATTEMPTS = 4
FEED_RETRY_DELAYS = (2, 5, 15)
FEED_REQUEST_GAP = 2
BILIBILI_POLL_CONCURRENCY = 4
BILIBILI_STATE_FILE = data_path("bilibili.json")
BILIBILI_PROTECTION_FILE = data_path("bilibili_protection.json")

_feed_client = None
_state_file_lock = threading.Lock()
_protection_file_lock = threading.Lock()
_protection_lock = asyncio.Lock()
_feed_request_lock = asyncio.Lock()
_last_feed_request_ts = 0.0
_posts_cache = {}
_protection_runtime_state = None

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


def _short_error(error, limit=160):
    return " ".join(str(error or "").split())[:limit]


def _get_feed_client():
    global _feed_client
    if _feed_client is None:
        # RSSHub 通常是本机/内网服务；Bilibili 源站访问由 RSSHub 自己负责。
        _feed_client = httpx.AsyncClient(
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": "WRelay Bilibili RSS"},
        )
    return _feed_client


@get_driver().on_shutdown
async def _close_feed_client():
    global _feed_client
    if _feed_client is not None:
        await _feed_client.aclose()
        _feed_client = None


def _load_state():
    data = load_json(BILIBILI_STATE_FILE, default={})
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("sources"), dict):
        data["sources"] = {}
    return data


def _save_state(data):
    try:
        atomic_write_json(BILIBILI_STATE_FILE, data, indent=2)
    except Exception as exc:
        # ETag 只影响请求效率，去重游标仍由 history.py 单独保证。
        logger.warning("Bilibili RSS 状态文件写入失败: {}", exc)


def _load_protection_state():
    """加载限速队列；损坏条目单独丢弃，不影响 RSS 主状态。"""

    global _protection_runtime_state
    if _protection_runtime_state is not None:
        return _protection_runtime_state
    raw = load_json(BILIBILI_PROTECTION_FILE, default={})
    state = {"version": 1, "queues": {}, "sent_at": []}
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
                    if not post_id or not cursor_id or post_id in seen:
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
                    if not post_id or not cursor_id or post_id in deferred_seen:
                        continue
                    deferred_seen.add(post_id)
                    deferred.append({"id": post_id, "cursor_id": cursor_id})
                if items or deferred:
                    state["queues"][str(source_id)] = {
                        "items": sorted(items, key=lambda x: int(x["cursor_id"])),
                        "deferred_skips": sorted(deferred, key=lambda x: int(x["cursor_id"])),
                    }
    _protection_runtime_state = state
    return state


def _save_protection_state(state):
    try:
        with _protection_file_lock:
            atomic_write_json(BILIBILI_PROTECTION_FILE, state, indent=2)
    except Exception as exc:
        logger.warning("Bilibili 限速队列保存失败: {}", exc)


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
    state = _load_protection_state()
    bucket = _protection_bucket(state, source_id)
    if bucket["items"] or not bucket["deferred_skips"]:
        return
    items = list(bucket["deferred_skips"])
    await _skip_queue_items(_source_channel_key(source_id), active_groups, items)
    bucket["deferred_skips"] = []
    _save_protection_state(state)


async def _dispatch_one_locked():
    """在全局锁内最多投递一个已入队的 Bilibili 投稿。"""

    config = normalize_protection_config(get_bilibili_protection())
    state = _load_protection_state()
    now = time.time()
    pruned = prune_sent_timestamps(state.get("sent_at"), now)
    state["sent_at"] = pruned
    if rate_wait_seconds(
        pruned,
        now,
        config["min_send_interval"],
        config["hourly_limit"],
    ) > 0:
        _save_protection_state(state)
        return 0, 0

    sources = {source["id"]: source for source in get_bilibili_sources()}
    candidates = []
    for source_id, bucket in state["queues"].items():
        source = sources.get(source_id)
        if not source:
            continue
        for item in bucket["items"]:
            candidates.append((int(item["cursor_id"]), source_id, source, bucket, item))
    candidates.sort(key=lambda value: value[0])

    for _, source_id, source, bucket, item in candidates:
        active_groups = get_bilibili_groups_for_source(source_id)
        if not active_groups:
            continue
        channel_key = _source_channel_key(source_id)
        channel_map = normalize_channel_map(load_last_messages(), channel_key)
        targets = select_target_groups(channel_map, active_groups, item["cursor_id"])
        if not targets:
            bucket["items"].remove(item)
            if not bucket["items"]:
                await _flush_deferred_skips(source_id, active_groups)
            _save_protection_state(state)
            continue

        filter_config = get_bilibili_source_filter(source_id)
        if not check_bilibili_video_filter(filter_config, item["post"]):
            await _skip_queue_items(channel_key, active_groups, [item])
            bucket["items"].remove(item)
            if not bucket["items"]:
                await _flush_deferred_skips(source_id, active_groups)
            _save_protection_state(state)
            continue

        try:
            rendered = await _render_post(item["post"], source)
            if rendered is None:
                await _skip_queue_items(channel_key, active_groups, [item])
                bucket["items"].remove(item)
                if not bucket["items"]:
                    await _flush_deferred_skips(source_id, active_groups)
                _save_protection_state(state)
                continue
            text_parts, media_items = rendered
            text_parts = await translate_text_parts(
                text_parts,
                ("bilibili", source_id, item["post"]["id"]),
                skip_leading_parts=0,
                config=get_bilibili_source_translation_config(source_id),
            )
            ok_map = await send_and_record(
                channel_key,
                item["cursor_id"],
                text_parts,
                media_items,
                targets,
            )
        except Exception as exc:
            logger.warning(
                "Bilibili 投稿处理失败，保留在限速队列: {} {}: {}",
                source["name"],
                item["post"].get("id"),
                exc,
            )
            return 0, 1

        if not ok_map:
            logger.warning("QQ Bot 未连接，Bilibili 投稿保留在限速队列: {} {}", source["name"], item["post"]["id"])
            return 0, 0
        if not any(ok_map.values()):
            logger.warning(
                "Bilibili 投稿全部目标群发送失败，保留在限速队列: {} {}",
                source["name"],
                item["post"]["id"],
            )
            return 0, 1

        state["sent_at"].append(now)
        await record(True)
        current_map = normalize_channel_map(load_last_messages(), channel_key)
        remaining = select_target_groups(current_map, active_groups, item["cursor_id"])
        if not remaining:
            bucket["items"].remove(item)
            if not bucket["items"]:
                await _flush_deferred_skips(source_id, active_groups)
        _save_protection_state(state)
        return 1, 0
    _save_protection_state(state)
    return 0, 0


def _update_source_state(source_id, etag=None, clear_etag=False):
    with _state_file_lock:
        state = _load_state()
        source_state = state["sources"].setdefault(source_id, {})
        source_state["last_poll_ts"] = int(time.time())
        if clear_etag:
            source_state.pop("etag", None)
        elif etag:
            source_state["etag"] = etag
        _save_state(state)


async def _fetch_feed(url, etag=None):
    global _last_feed_request_ts
    headers = {"Accept-Encoding": "identity"}
    if etag:
        headers["If-None-Match"] = etag.removeprefix("W/")
    last_error = ""
    for attempt in range(FEED_MAX_ATTEMPTS):
        try:
            async with _feed_request_lock:
                elapsed = time.monotonic() - _last_feed_request_ts
                if elapsed < FEED_REQUEST_GAP:
                    await asyncio.sleep(FEED_REQUEST_GAP - elapsed)
                try:
                    response = await _get_feed_client().get(
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
            if response.status_code == 503:
                last_error += "（RSSHub 上游可能需要 Chromium 或有效 BILIBILI_COOKIE_1）"
            retriable = response.status_code == 429 or response.status_code >= 500

        if not retriable or attempt + 1 >= FEED_MAX_ATTEMPTS:
            break
        delay = FEED_RETRY_DELAYS[min(attempt, len(FEED_RETRY_DELAYS) - 1)]
        logger.warning(
            "Bilibili RSS 拉取临时失败，将在 {} 秒后重试（{}/{}）: {}",
            delay,
            attempt + 1,
            FEED_MAX_ATTEMPTS,
            last_error,
        )
        await asyncio.sleep(delay)
    return None, None, last_error


def _source_channel_key(source_id):
    return "bilibili:%s" % source_id


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


def _image_filename(index):
    return "bilibili-cover-%s.jpg" % (index + 1)


async def _render_post(post, source):
    text_parts = []
    media_items = []
    text = format_post_text(post, source)
    if text:
        text_parts.append(make_text(text))

    image_urls = [
        item["url"]
        for item in (post.get("media") or [])
        if item.get("kind") == "图片" and item.get("url")
    ][:1]
    if image_urls:
        bytes_map = await fetch_bytes_many(image_urls)
        for index, url in enumerate(image_urls):
            data = bytes_map.get(url)
            if data:
                segment = QQMessageSegment.file_image(data, _image_filename(index))
            else:
                logger.warning("Bilibili 封面下载失败，降级为链接: {}", url)
                segment = make_media_link("图片", url)
            media_items.append({"kind": "图片", "url": url, "segment": segment})

    if not text_parts and not media_items:
        return None
    return text_parts, media_items


async def _send_posts(source, posts):
    if not posts:
        return 0, 0, 0
    source_id = source["id"]
    channel_key = _source_channel_key(source_id)
    active_groups = get_bilibili_groups_for_source(source_id)
    if not active_groups:
        logger.debug("Bilibili 来源无可用目标群，跳过: {}", source["name"])
        return 0, 0, 0

    latest_id = max(posts, key=lambda item: int(item["cursor_id"]))["cursor_id"]
    async with _protection_lock:
        await _ensure_baseline(channel_key, active_groups, latest_id)
        await _flush_deferred_skips(source_id, active_groups)

        state = _load_protection_state()
        bucket = _protection_bucket(state, source_id)
        queued_ids = {item["id"] for item in bucket["items"]}
        deferred_ids = {item["id"] for item in bucket["deferred_skips"]}
        filter_config = get_bilibili_source_filter(source_id)
        new_count = 0
        additions = []

        for post in posts:
            post_id = str(post.get("id") or "")
            cursor_id = str(post.get("cursor_id") or "")
            if not post_id or not cursor_id or post_id in queued_ids or post_id in deferred_ids:
                continue
            channel_map = normalize_channel_map(load_last_messages(), channel_key)
            targets = select_target_groups(channel_map, active_groups, cursor_id)
            if not targets:
                continue
            if not check_bilibili_video_filter(filter_config, post):
                await _skip_queue_items(channel_key, active_groups, [{"id": post_id, "cursor_id": cursor_id}])
                continue
            additions.append(_queue_item(post))
            queued_ids.add(post_id)
            new_count += 1

        items = sorted(
            bucket["items"] + additions,
            key=lambda item: int(item["cursor_id"]),
        )
        protection = normalize_protection_config(get_bilibili_protection())
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
            _save_protection_state(state)

        sent_count, failed_count = await _dispatch_one_locked()
        return new_count, sent_count, failed_count


async def _poll_source(source):
    source_id = source["id"]
    state = _load_state()
    etag = state["sources"].get(source_id, {}).get("etag")
    cached_posts = _posts_cache.get(source_id)
    body, new_etag, error = await _fetch_feed(source["url"], etag)

    if error is None and body is None:
        if cached_posts is not None:
            counts = await _send_posts(source, cached_posts)
            return {"result": "ok", "error": "", "new": counts[0], "sent": counts[1], "failed": counts[2]}
        body, new_etag, error = await _fetch_feed(source["url"], None)

    if error:
        logger.warning("Bilibili RSS 拉取失败，本轮跳过: {} ({}): {}", source["name"], source_id, error)
        return {"result": "fetch_failed", "error": _short_error(error), "new": 0, "sent": 0, "failed": 0}

    try:
        posts = parse_feed(body)
    except Exception as exc:
        logger.warning("Bilibili RSS 解析失败，本轮跳过: {} ({}): {}", source["name"], source_id, exc)
        _update_source_state(source_id, clear_etag=True)
        return {"result": "parse_failed", "error": _short_error(exc), "new": 0, "sent": 0, "failed": 0}

    _update_source_state(source_id, etag=new_etag)
    _posts_cache[source_id] = posts
    counts = await _send_posts(source, posts)
    return {"result": "ok", "error": "", "new": counts[0], "sent": counts[1], "failed": counts[2]}


async def _poll_all():
    sources = [
        source
        for source in get_bilibili_sources()
        if source["enabled"] and get_bilibili_groups_for_source(source["id"])
    ]
    if not sources:
        return
    sem = asyncio.Semaphore(BILIBILI_POLL_CONCURRENCY)

    async def poll_one(source):
        async with sem:
            try:
                return await _poll_source(source)
            except Exception as exc:
                logger.exception("Bilibili 来源轮询异常: {} ({})", source["name"], source["id"])
                return {"result": "fetch_failed", "error": _short_error(exc), "new": 0, "sent": 0, "failed": 0}

    results = await asyncio.gather(*(poll_one(source) for source in sources))
    new_total = sent_total = failed_total = 0
    bad_result = ""
    bad_error = ""
    for result in results:
        new_total += result.get("new", 0)
        sent_total += result.get("sent", 0)
        failed_total += result.get("failed", 0)
        if not bad_result and result.get("result") not in (None, "ok"):
            bad_result = result["result"]
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
    while True:
        try:
            await _poll_all()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Bilibili RSS 轮询异常")
        await asyncio.sleep(get_bilibili_poll_interval())


_loop_started = False


@get_driver().on_bot_connect
async def _start_poll_loop(bot):
    global _loop_started
    if not isinstance(bot, QQBot) or _loop_started:
        return
    _loop_started = True
    _runtime_status["loop_started"] = True
    logger.info("Bilibili RSS 轮询已启动（间隔 {}s）", get_bilibili_poll_interval())
    asyncio.create_task(_poll_loop())


_bilibili_app = get_app()


@_bilibili_app.get("/api/bilibili/status")
async def api_bilibili_status():
    bots = get_bots()
    sources = get_bilibili_sources()
    data = runtime_status()
    protection_state = _load_protection_state()
    protection_config = normalize_protection_config(get_bilibili_protection())
    data.update({
        "ok": True,
        "running": bool(data.get("loop_started")),
        "qq_connected": any(isinstance(bot, QQBot) for bot in bots.values()),
        "poll_interval": get_bilibili_poll_interval(),
        "sources_total": len(sources),
        "sources_enabled": sum(1 for source in sources if source["enabled"]),
        "forwarding_groups": len(get_bilibili_forwarding_groups()),
        "protection": protection_config,
        "protection_queue_pending": sum(
            len(bucket.get("items") or [])
            for bucket in (protection_state.get("queues") or {}).values()
            if isinstance(bucket, dict)
        ),
    })
    return data
