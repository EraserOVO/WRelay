"""Bilibili 投稿/标签/关键词 → QQ 自动转发。

链路：RSSHub 拉取 → bilibili_parser 解析 → 来源筛选 → 独立游标与跨来源投稿去重 →
复用 translation/retry/sender 投递到 QQ。该模块不改变 Discord、X 或 QQ→QQ
的路由和配置。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import threading
import time
from urllib.parse import urlencode, urlparse

import httpx

from nonebot import get_app, get_bots, get_driver, logger
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

from plugins.bilibili_parser import format_forward_text, parse_feed, parse_video_url
from plugins.config import (
    get_bilibili_groups_for_source,
    get_bilibili_poll_interval,
    get_bilibili_protection,
    get_bilibili_rsshub_base,
    get_bilibili_source_filter,
    get_bilibili_source_delivery_config,
    get_bilibili_polling_snapshot,
    get_bilibili_status_snapshot,
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
from plugins.history import (
    load_last_messages,
    load_last_messages_async,
    update_last_messages,
)
from plugins.json_io import atomic_write_json, run_in_thread_to_completion
from plugins.media import fetch_bytes, fetch_bytes_many, get_max_download_bytes, make_media_link, make_text
from plugins.paths import data_path
from plugins.retry import send_and_record
from plugins.rss_polling import read_rss_poll_interval, run_rss_poll_loop
from plugins.stats import record
from plugins.translation import translate_text_parts


FEED_TIMEOUT = 30
# RSSHub 会继续请求 Bilibili 上游；多个来源同时刷新容易触发上游拦截。
FEED_MAX_ATTEMPTS = 4
FEED_RETRY_DELAYS = (2, 5, 15)
FEED_REQUEST_GAP = 2
BILIBILI_POLL_CONCURRENCY = 4
DEFAULT_BILIBILI_POLL_INTERVAL = 300
BILIBILI_STATE_FILE = data_path("bilibili.json")
BILIBILI_PROTECTION_FILE = data_path("bilibili_protection.json")

_feed_client = None
_feed_request_tasks = set()
_feed_stopping = False
_state_file_lock = threading.Lock()
_protection_file_lock = threading.Lock()
_protection_state_lock = threading.RLock()
_protection_lock = asyncio.Lock()
_feed_request_lock = asyncio.Lock()
_wbi_key_lock = asyncio.Lock()
_last_feed_request_ts = 0.0
_posts_cache = {}
_protection_runtime_state = None
_protection_load_error = ""
_PROTECTION_STATE_MISSING = object()
_STATE_MISSING = object()
_state_load_error = ""
_wbi_key_cache = ""
_wbi_key_expires_at = 0.0

_WBI_MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)

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


class BilibiliProtectionError(RuntimeError):
    """本地投递保护状态不可用，不能继续投递或推进游标。"""


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


async def _bilibili_get(*args, **kwargs):
    """Track Bilibili and RSSHub requests so shutdown can reap one-shot calls."""
    if _feed_stopping:
        raise asyncio.CancelledError("Bilibili RSS HTTP client is shutting down")
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


async def _get_wbi_mixin_key():
    """读取 Bilibili WBI 图片/子图片密钥并生成签名用 mixin key。"""
    global _wbi_key_cache, _wbi_key_expires_at
    now = time.time()
    if _wbi_key_cache and now < _wbi_key_expires_at:
        return _wbi_key_cache

    async with _wbi_key_lock:
        now = time.time()
        if _wbi_key_cache and now < _wbi_key_expires_at:
            return _wbi_key_cache
        response = await _bilibili_get(
            "https://api.bilibili.com/x/web-interface/nav"
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        wbi = data.get("wbi_img") or {}

        def key_from_url(value):
            filename = urlparse(str(value or "")).path.rsplit("/", 1)[-1]
            return filename.split(".", 1)[0]

        img_key = key_from_url(wbi.get("img_url"))
        sub_key = key_from_url(wbi.get("sub_url"))
        raw_key = img_key + sub_key
        if len(raw_key) <= max(_WBI_MIXIN_KEY_ENC_TAB):
            raise RuntimeError("Bilibili WBI 密钥响应无效")
        _wbi_key_cache = "".join(
            raw_key[index] for index in _WBI_MIXIN_KEY_ENC_TAB
        )[:32]
        _wbi_key_expires_at = now + 12 * 60 * 60
        return _wbi_key_cache


async def _signed_wbi_params(params):
    """为 Bilibili playurl API 添加 WBI 签名参数。"""
    mixin_key = await _get_wbi_mixin_key()
    values = dict(params)
    values["wts"] = int(time.time())
    cleaned = {
        key: re.sub(r"[!'()*]", "", str(value))
        for key, value in sorted(values.items())
    }
    query = urlencode(cleaned)
    values["w_rid"] = hashlib.md5(
        (query + mixin_key).encode("utf-8")
    ).hexdigest()
    return values


# view 接口的业务错误码。这些返回的 message 通常就是数字本身，直接抛给用户
# 无法理解；码值含义来自 Bilibili 官方接口文档（video/info.md）。
_VIEW_ERROR_TEXT = {
    -400: "投稿链接或请求参数无效",
    -403: "访问权限不足",
    -404: "投稿不存在或已删除",
    62002: "稿件不可见",
    62004: "稿件审核中",
    62012: "仅UP主自己可见",
}


def _view_error_text(payload):
    """把 view 接口的业务错误码翻译成可读原因；未知码保留原始 message。"""
    code = payload.get("code")
    text = _VIEW_ERROR_TEXT.get(code)
    if text:
        return "%s：%s" % (text, code)
    return str(payload.get("message") or "读取 Bilibili 投稿失败")


async def _fetch_view_data(video_ref):
    """通过公开 view API 获取投稿与分 P 的 cid。"""
    params = {}
    if video_ref.get("bvid"):
        params["bvid"] = video_ref["bvid"]
    elif video_ref.get("avid"):
        params["aid"] = video_ref["avid"]
    else:
        raise ValueError("Bilibili 投稿链接中没有 BV/AV 号")

    response = await _bilibili_get(
        "https://api.bilibili.com/x/web-interface/view",
        params=params,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(_view_error_text(payload))
    return payload["data"]


async def fetch_bilibili_manual_post(url):
    """读取 QQ 私聊提交的 Bilibili 视频链接，返回 (post, source_name, error)。"""
    video_ref = parse_video_url(url)
    if not video_ref:
        return None, "", "不是受支持的 Bilibili 投稿链接"

    try:
        if video_ref.get("short_url"):
            response = await _bilibili_get(video_ref["short_url"])
            response.raise_for_status()
            video_ref = parse_video_url(str(response.url))
            if not video_ref:
                raise RuntimeError("短链接没有跳转到 Bilibili 投稿页")

        data = await _fetch_view_data(video_ref)
        pages = data.get("pages") or []
        page_index = min(max(int(video_ref.get("page") or 1) - 1, 0), len(pages) - 1) if pages else 0
        page = pages[page_index] if pages else {}
        bvid = str(data.get("bvid") or video_ref.get("bvid") or "").strip()
        avid = str(data.get("aid") or video_ref.get("avid") or "").strip()
        link_id = bvid or ("av" + avid if avid else "")
        if not link_id:
            raise RuntimeError("Bilibili 投稿接口没有返回投稿编号")
        page_number = page_index + 1
        link = "https://www.bilibili.com/video/%s" % link_id
        if page_number > 1:
            link += "?p=%s" % page_number
        title = str(data.get("title") or "").strip()
        page_title = str(page.get("part") or "").strip()
        if page_number > 1 and page_title:
            title = "%s（P%d：%s）" % (title, page_number, page_title)
        cover = str(data.get("pic") or "").strip()
        if cover.startswith("//"):
            cover = "https:" + cover
        elif cover.startswith("http://"):
            cover = "https://" + cover[len("http://"):]
        author = str((data.get("owner") or {}).get("name") or "").strip()
        post = {
            "id": link_id,
            "bvid": bvid,
            "avid": avid,
            "cid": page.get("cid"),
            "page": page_number,
            "link": link,
            "title": title,
            "author": author,
            "media": ([{"kind": "图片", "url": cover}] if cover else []),
        }
        return post, author, None
    except Exception as exc:
        logger.warning("读取 Bilibili 投稿失败: {}", exc)
        return None, "", _short_error(exc)


async def _fetch_video_url(post):
    """返回 (可下载的视频 URL, 可上传的视频字节)；失败时保留已知链接。"""
    referer = str(post.get("link") or "https://www.bilibili.com/")
    direct_urls = [
        item.get("url")
        for item in (post.get("media") or [])
        if item.get("kind") == "视频" and item.get("url")
    ]
    for video_url in direct_urls:
        data = await fetch_bytes(video_url, headers={"Referer": referer})
        if data:
            return video_url, data

    video_ref = {
        "bvid": str(post.get("bvid") or ""),
        "avid": str(post.get("avid") or ""),
    }
    cid = post.get("cid")
    if not cid:
        try:
            view = await _fetch_view_data(video_ref)
            pages = view.get("pages") or []
            page_index = min(max(int(post.get("page") or 1) - 1, 0), len(pages) - 1) if pages else 0
            page = pages[page_index] if pages else {}
            cid = page.get("cid") or view.get("cid")
        except Exception as exc:
            logger.warning("获取 Bilibili 视频信息失败: {}", exc)
            return (direct_urls[0] if direct_urls else referer), None
    if not cid:
        return (direct_urls[0] if direct_urls else referer), None

    limit = get_max_download_bytes()
    for quality in (64, 32, 16):
        params = {
            "cid": cid,
            "qn": quality,
            "fnval": 0,
            "fnver": 0,
            "fourk": 0,
        }
        if video_ref["bvid"]:
            params["bvid"] = video_ref["bvid"]
        elif video_ref["avid"]:
            params["avid"] = video_ref["avid"]
        try:
            signed = await _signed_wbi_params(params)
            response = await _bilibili_get(
                "https://api.bilibili.com/x/player/wbi/playurl",
                params=signed,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                continue
            data = payload.get("data") or {}
            segments = data.get("durl") or []
            # 只支持单段直传；多段投稿需要合并，不能把其中一段误当完整视频。
            if len(segments) != 1:
                continue
            segment = segments[0]
            video_url = str(segment.get("url") or "").strip()
            if not video_url:
                continue
            size = segment.get("size")
            if isinstance(size, int) and size > limit:
                return video_url, None
            video_bytes = await fetch_bytes(
                video_url,
                headers={"Referer": referer},
            )
            return video_url, video_bytes
        except Exception as exc:
            logger.debug("Bilibili 视频清晰度 {} 获取失败: {}", quality, exc)
    return (direct_urls[0] if direct_urls else referer), None


def _read_state():
    try:
        with open(BILIBILI_STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return _STATE_MISSING


def _load_state():
    global _state_load_error
    try:
        data = _read_state()
    except (OSError, ValueError) as exc:
        error = "Bilibili 来源状态读取失败：{}".format(_short_error(exc))
        if error != _state_load_error:
            logger.error("{}；已暂停来源轮询以保留原基线", error)
        _state_load_error = error
        raise RuntimeError(error) from exc

    if data is _STATE_MISSING:
        data = {"sources": {}}
    elif not isinstance(data, dict):
        error = "Bilibili 来源状态格式无效：顶层 JSON 必须是对象"
        if error != _state_load_error:
            logger.error("{}；已暂停来源轮询以保留原基线", error)
        _state_load_error = error
        raise RuntimeError(error)
    elif not isinstance(data.get("sources"), dict):
        error = "Bilibili 来源状态格式无效：sources 必须是对象"
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
    atomic_write_json(BILIBILI_STATE_FILE, data, indent=2)


def _valid_cursor_id(value):
    """恢复持久化队列时仅接受解析器生成的 ASCII 十进制游标。"""
    return bool(value) and value.isascii() and value.isdecimal()


def _read_protection_state():
    try:
        with open(BILIBILI_PROTECTION_FILE, "r", encoding="utf-8") as file:
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
        error = "Bilibili 积压队列读取失败：{}".format(_short_error(exc))
        with _protection_state_lock:
            if _protection_runtime_state is not None:
                return _protection_runtime_state
            should_log = error != _protection_load_error
            _protection_load_error = error
        if should_log:
            logger.error("{}；已暂停 Bilibili 自动投递并保留原文件", error)
        return state

    if raw is _PROTECTION_STATE_MISSING:
        raw = {}
    elif not isinstance(raw, dict):
        error = "Bilibili 积压队列格式无效：顶层 JSON 必须是对象"
        with _protection_state_lock:
            if _protection_runtime_state is not None:
                return _protection_runtime_state
            should_log = error != _protection_load_error
            _protection_load_error = error
        if should_log:
            logger.error("{}；已暂停 Bilibili 自动投递并保留原文件", error)
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
        logger.warning("Bilibili 积压队列未能安全读取，跳过保存以保留原文件")
        return False
    try:
        with _protection_file_lock:
            atomic_write_json(BILIBILI_PROTECTION_FILE, state, indent=2)
        return True
    except Exception as exc:
        logger.warning("Bilibili 限速队列保存失败: {}", exc)
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
    return await asyncio.to_thread(get_bilibili_source_delivery_config, source_id)


async def _load_source_groups(source_id):
    return await asyncio.to_thread(get_bilibili_groups_for_source, source_id)


async def _load_protection_config():
    return await asyncio.to_thread(get_bilibili_protection)


async def _load_polling_snapshot():
    return await asyncio.to_thread(get_bilibili_polling_snapshot)


async def _remove_queue_item(bucket, item, source_id, active_groups):
    """移除已处理条目，仅在来源队列清空后推进延后跳过记录。"""
    bucket["items"].remove(item)
    if not bucket["items"]:
        await _flush_deferred_skips(source_id, active_groups)


async def _dispatch_one_locked():
    """在全局锁内最多投递一个已入队的 Bilibili 投稿。"""

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
        if not check_bilibili_video_filter(filter_config, item["post"]):
            await _skip_queue_items(channel_key, active_groups, [item])
            await _remove_queue_item(bucket, item, source_id, active_groups)
            await _save_protection_state_async(state)
            continue

        try:
            rendered = await _render_post(item["post"], source)
            if rendered is None:
                await _skip_queue_items(channel_key, active_groups, [item])
                await _remove_queue_item(bucket, item, source_id, active_groups)
                await _save_protection_state_async(state)
                continue
            text_parts, media_items = rendered
            text_parts = await translate_text_parts(
                text_parts,
                ("bilibili", source_id, item["post"]["id"]),
                skip_leading_parts=0,
                config=delivery_config["translation"],
            )
            ok_map = await send_and_record(
                channel_key,
                item["cursor_id"],
                text_parts,
                media_items,
                targets,
                idempotency_key="bilibili:%s:%s"
                % (item["post"].get("post_type") or "视频", item["id"]),
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
            if getattr(ok_map, "busy_groups", ()):
                return 0, 0
            logger.warning(
                "Bilibili 投稿全部目标群发送失败，保留在限速队列: {} {}",
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
    baseline_cursor_id=None,
    feed_initialized=None,
    active_post_types=None,
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
        if baseline_cursor_id is not None:
            source_state["baseline_cursor_id"] = str(baseline_cursor_id)
        if feed_initialized is not None:
            source_state["feed_initialized"] = bool(feed_initialized)
        if active_post_types is not None:
            source_state["active_post_types"] = list(active_post_types)
        _save_state(state)


async def _update_source_state_async(*args, **kwargs):
    """Update and persist RSS metadata outside the asyncio event loop."""
    return await asyncio.to_thread(_update_source_state, *args, **kwargs)


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
                    response = await _bilibili_get(
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


async def _render_post(
    post,
    source,
    source_label="自动转发来自",
    include_video=None,
    enabled_types=None,
):
    text_parts = []
    media_items = []
    text = format_forward_text(post, source, source_label=source_label)
    if text and enabled_types is None:
        lines = text.splitlines()
        text_parts.append(make_text(lines[0]))
        if len(lines) > 1:
            text_parts.append(make_text("\n" + "\n".join(lines[1:])))

    if enabled_types is not None:
        enabled_types = set(enabled_types)
        body_lines = []
        title = str(post.get("title") or "").strip()
        link = str(post.get("link") or "").strip()
        if title and "文字" in enabled_types:
            body_lines.append(title)
        if link and "链接" in enabled_types:
            body_lines.append(link)

    image_urls = [
        item["url"]
        for item in (post.get("media") or [])
        if item.get("kind") == "图片"
        and item.get("url")
        and (enabled_types is None or "图片" in enabled_types)
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

    video_enabled = (
        source.get("forward_video") is True
        if include_video is None and isinstance(source, dict)
        else include_video is True
    )
    if video_enabled and str(post.get("post_type") or "视频") == "视频" and (
        enabled_types is None or "文件" in enabled_types
    ):
        video_url, video_data = await _fetch_video_url(post)
        if video_data:
            segment = QQMessageSegment.file_video(video_data, "bilibili-video.mp4")
            media_items.append({"kind": "视频", "url": video_url, "segment": segment})
        elif video_url:
            logger.info("Bilibili 视频未能上传，降级为投稿视频链接: {}", video_url)
            media_items.append({
                "kind": "视频",
                "url": video_url,
                "segment": make_media_link("视频", video_url),
            })

    if enabled_types is not None:
        if body_lines or media_items:
            source_name = str(
                (post.get("author") if isinstance(post, dict) else "")
                or (source.get("name") if isinstance(source, dict) else "")
                or (source.get("value") if isinstance(source, dict) else "")
                or ""
            ).strip()
            header = (
                "%s Bilibili 中[%s]的投稿："
                % (source_label, source_name)
                if source_name
                else ""
            )
            lines = ([header] if header else []) + body_lines
            if lines:
                text_parts.append(make_text(lines[0]))
                if len(lines) > 1:
                    text_parts.append(make_text("\n" + "\n".join(lines[1:])))

    if not text_parts and not media_items:
        return None
    return text_parts, media_items


async def render_bilibili_post(
    post,
    source_name="",
    include_video=True,
    enabled_types=None,
):
    """渲染 QQ 私聊手动转发的 Bilibili 投稿。"""
    source = {"name": source_name, "value": source_name}
    return await _render_post(
        post,
        source,
        source_label="手动转发来自",
        include_video=include_video,
        enabled_types=enabled_types,
    )


async def _send_posts(source, posts):
    if not posts:
        return 0, 0, 0
    source_id = source["id"]
    channel_key = _source_channel_key(source_id)
    active_groups = await _load_source_groups(source_id)
    if not active_groups:
        logger.debug("Bilibili 来源无可用目标群，跳过: {}", source["name"])
        return 0, 0, 0

    latest_id = max(posts, key=lambda item: int(item["cursor_id"]))["cursor_id"]
    async with _protection_lock:
        _, load_error = await _load_protection_state_async()
        if load_error:
            raise BilibiliProtectionError(load_error)
        await _ensure_baseline(channel_key, active_groups, latest_id)
        await _flush_deferred_skips(source_id, active_groups)

        state, load_error = await _load_protection_state_async()
        if load_error:
            raise BilibiliProtectionError(load_error)
        bucket = _protection_bucket(state, source_id)
        queued_ids = {item["id"] for item in bucket["items"]}
        deferred_ids = {item["id"] for item in bucket["deferred_skips"]}
        filter_config = await asyncio.to_thread(
            get_bilibili_source_filter,
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


async def _poll_feed_posts(source, source_id, feed, feed_type, state_key):
    """获取并缓存单个投稿类型 Feed，错误只返回给当前来源汇总。"""
    feed_state = (await _load_state_async())["sources"].get(state_key, {})
    if not isinstance(feed_state, dict):
        feed_state = {}
    feed_url = feed["url"]
    etag = feed_state.get("etag")
    cached_posts = _posts_cache.get(state_key)

    # ETags and parsed posts belong to one feed URL. A changed or
    # unrecorded URL must get a full fetch before reusing per-key state.
    if feed_state.get("url") != feed_url:
        etag = None
        cached_posts = None
        _posts_cache.pop(state_key, None)
        await _update_source_state_async(
            state_key,
            clear_etag=True,
            source_url=feed_url,
        )

    body, new_etag, error = await _fetch_feed(feed_url, etag)
    feed_posts = None
    if error is None and body is None:
        if cached_posts is not None:
            feed_posts = cached_posts
        else:
            body, new_etag, error = await _fetch_feed(feed_url, None)

    if error:
        logger.warning(
            "Bilibili RSS 拉取失败，本轮跳过该投稿类型: {} ({}, {}): {}",
            source["name"],
            source_id,
            feed_type,
            error,
        )
        return None, ("fetch_failed", _short_error(error))

    if feed_posts is None:
        try:
            feed_posts = parse_feed(body, post_type=feed_type) if body else []
        except Exception as exc:
            logger.warning(
                "Bilibili RSS 解析失败，本轮跳过该投稿类型: {} ({}, {}): {}",
                source["name"],
                source_id,
                feed_type,
                exc,
            )
            await _update_source_state_async(state_key, clear_etag=True)
            return None, ("parse_failed", _short_error(exc))

        await _update_source_state_async(
            state_key,
            etag=new_etag,
            clear_etag=not bool(new_etag),
            source_url=feed_url,
        )
        _posts_cache[state_key] = feed_posts

    return feed_posts, None


async def _poll_source(source):
    source_id = source["id"]
    feeds = source.get("feeds") or [{
        "type": "视频",
        "url": source["url"],
        "state_key": source_id,
    }]
    source_state = (await _load_state_async())["sources"].get(source_id, {})
    if not isinstance(source_state, dict):
        logger.warning(
            "Bilibili 来源状态条目损坏，按安全基线重建: {}",
            source_id,
        )
        source_state = {}
    previous_types = source_state.get("active_post_types")
    if isinstance(previous_types, list):
        previous_types = [str(post_type) for post_type in previous_types]
    else:
        # 旧版来源状态只轮询视频；新增类型首次启用时应建立单独基线。
        previous_types = ["视频"] if source_state else []
    feed_types = list(dict.fromkeys(str(feed.get("type") or "视频") for feed in feeds))
    newly_selected_types = set(feed_types) - set(previous_types)
    posts_by_id = {}
    errors = []
    successful_feeds = 0
    for feed in feeds:
        feed_type = str(feed.get("type") or "视频")
        state_key = str(feed.get("state_key") or source_id)
        feed_posts, feed_error = await _poll_feed_posts(
            source,
            source_id,
            feed,
            feed_type,
            state_key,
        )
        if feed_error:
            errors.append((feed_error[0], feed_type, feed_error[1]))
            continue

        if feed_type != "视频":
            feed_state = (await _load_state_async())["sources"].get(state_key, {})
            if feed_type in newly_selected_types or not feed_state.get("feed_initialized"):
                baseline = max(
                    (post for post in feed_posts),
                    key=lambda item: int(item["cursor_id"]),
                    default=None,
                )
                await _update_source_state_async(
                    state_key,
                    baseline_cursor_id=baseline.get("cursor_id", "") if baseline else "",
                    feed_initialized=True,
                )
                feed_posts = []
            else:
                baseline_cursor_id = str(feed_state.get("baseline_cursor_id") or "")
                if baseline_cursor_id:
                    feed_posts = [
                        post for post in feed_posts
                        if int(post["cursor_id"]) > int(baseline_cursor_id)
                    ]

        successful_feeds += 1
        for post in feed_posts:
            posts_by_id.setdefault(str(post.get("id") or ""), post)

    await _update_source_state_async(source_id, active_post_types=feed_types)
    if not successful_feeds and errors:
        return {
            "result": errors[0][0],
            "error": "; ".join("%s：%s" % (kind, error) for _, kind, error in errors),
            "new": 0,
            "sent": 0,
            "failed": 0,
        }

    posts = sorted(posts_by_id.values(), key=lambda item: int(item["cursor_id"]))
    counts = await _send_posts(source, posts)
    if errors:
        return {
            "result": errors[0][0],
            "error": "; ".join("%s：%s" % (kind, error) for _, kind, error in errors),
            "new": counts[0],
            "sent": counts[1],
            "failed": counts[2],
        }
    return {"result": "ok", "error": "", "new": counts[0], "sent": counts[1], "failed": counts[2]}


async def _poll_all():
    configured_sources, groups_by_source = await _load_polling_snapshot()
    configured_cache_keys = set()
    for source in configured_sources:
        feeds = source.get("feeds") or [{"state_key": source["id"]}]
        configured_cache_keys.update(
            str(feed.get("state_key") or source["id"])
            for feed in feeds
        )
    for cached_key in list(_posts_cache):
        if cached_key not in configured_cache_keys:
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
            logger.warning("Bilibili 配置存在重复来源 ID，跳过后续轮询: {}", source_id)
            continue
        seen_source_ids.add(source_id)
        sources.append(source)
    if not sources:
        _runtime_status["source_health"] = []
        return
    sem = asyncio.Semaphore(BILIBILI_POLL_CONCURRENCY)

    async def poll_one(source):
        async with sem:
            try:
                return await _poll_source(source)
            except BilibiliProtectionError as exc:
                return {"result": "protection_failed", "error": _short_error(exc), "new": 0, "sent": 0, "failed": 0}
            except Exception as exc:
                logger.exception("Bilibili 来源轮询异常: {} ({})", source["name"], source["id"])
                return {"result": "fetch_failed", "error": _short_error(exc), "new": 0, "sent": 0, "failed": 0}

    results = await asyncio.gather(*(poll_one(source) for source in sources))
    previous = {item["id"]: item for item in _runtime_status.get("source_health", [])}
    health = []
    for source, result in zip(sources, results):
        identity = str(source["id"])
        count = 0 if result.get("result") == "ok" else previous.get(identity, {}).get("consecutive_failures", 0) + 1
        health.append({"id": identity, "name": source["name"], "consecutive_failures": count})
    _runtime_status["source_health"] = health
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
    await run_rss_poll_loop(
        source_name="Bilibili RSS",
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
        get_bilibili_poll_interval,
        DEFAULT_BILIBILI_POLL_INTERVAL,
        source_name="Bilibili RSS",
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
            logger.error("Bilibili RSS 轮询任务异常退出: {}", error)


def _ensure_poll_loop_started():
    global _loop_started, _poll_task
    if _poll_stopping:
        return
    if _poll_task is not None and not _poll_task.done():
        return
    _loop_started = True
    _runtime_status["loop_started"] = True
    logger.info("Bilibili RSS 轮询后台任务已启动；QQ 连接状态每 5 秒检查一次")
    _poll_task = asyncio.create_task(_poll_loop())
    _poll_task.add_done_callback(_on_poll_task_done)


@get_driver().on_startup
async def _start_poll_loop():
    """Start polling at application startup, before QQ connection hooks run."""
    _ensure_poll_loop_started()


_bilibili_app = get_app()


@_bilibili_app.get("/api/bilibili/status")
async def api_bilibili_status():
    bots = get_bots()
    status_config = await asyncio.to_thread(get_bilibili_status_snapshot)
    sources = status_config["sources"]
    data = runtime_status()
    protection_state, protection_load_error = await _load_protection_state_async()
    protection_config = status_config["protection"]
    data.update({
        "ok": True,
        "running": bool(data.get("loop_started")),
        "qq_connected": any(isinstance(bot, QQBot) for bot in bots.values()),
        "poll_interval": await asyncio.to_thread(get_bilibili_poll_interval),
        "sources_total": len(sources),
        "sources_enabled": sum(1 for source in sources if source["enabled"]),
        "forwarding_groups": len(status_config["forwarding_groups"]),
        "protection": protection_config,
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
