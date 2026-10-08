"""X（Twitter）RSS 订阅推送（RSSHub → QQ）。

独立于 Discord forwarding_groups 的链路，使用独立的 X 转发组
（settings.x_forwarding_groups，X 源 → X 组 → QQ 群）路由目标群：
RSSHub 拉取 → x_rss_parser 解析 → 去重 → 渲染 → sender.send_relay_message。

设计要点：
- 轮询：应用启动时创建任务，QQ Bot 连接后开始拉取；默认 300 秒/次（config.x_rss_poll_interval，
  对齐 RSSHub 缓存 max-age=300）；请求带 If-None-Match，304 时复用
  上次解析结果补充持久队列（游标幂等，发送失败的群继续补投）
- 去重：tweet status ID（单调递增整数）做消息 ID，复用
  data/discord_last.json 的伪频道键 "x_rss:{handle}" 与 dedup.py
  纯函数（select_target_groups / apply_baseline / apply_success），
  按群记录游标，重启后天然恢复
- 首次订阅：该源在 discord_last.json 无任何记录时只建基线
  （基线 = 当前 feed 最大 status ID），不发送历史内容
- 新启用群：源已有记录但某启用群无游标时，先为该群建基线再发送，
  避免新群收到 feed 窗口内（最多 20 条）的历史刷屏
- 媒体：图片/视频统一 media.fetch_bytes_many 并发下载（走代理、
  30MB 上限）；视频 ≤30MB 用 file_video(bytes) 发本体，
  下载失败/超限降级为文字链接（sender 现有降级路径）
- 容错：单个源、单条 item、单次请求失败都只记日志，不影响
  其他源与轮询循环；RSS 拉取失败/解析失败绝不推进游标
- 投递保护：X 独立使用持久化队列和宽松默认限速（5 秒/条、每小时 300 条、
  每源积压 100 条），不共用 Bilibili 的限速配置
"""

import asyncio
import copy
import json
import re
import threading
import time
from urllib.parse import quote, unquote

import httpx

from nonebot import get_app, get_bots, get_driver
from nonebot import logger

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

from plugins.config import (
    get_x_rss_status_snapshot,
    get_x_rss_polling_snapshot,
    get_x_rss_poll_interval,
    get_x_protection,
    get_x_rsshub_base,
    get_x_groups_for_source,
    get_x_source_delivery_config,
)
from plugins.filter import check_x_post_filter
from plugins.media import fetch_bytes_many, make_text, make_media_link
from plugins.message_time import format_message_datetime
from plugins.sender import get_qq_bot
from plugins.stats import record
from plugins.history import (
    load_last_messages,
    load_last_messages_async,
    update_last_messages,
)
from plugins.dedup import (
    apply_baseline,
    apply_success,
    apply_skipped,
    normalize_channel_map,
    select_target_groups,
)
from plugins.retry import send_and_record
from plugins.translation import translate_text_parts
from plugins.json_io import (
    atomic_write_json,
    load_json,
    run_in_thread_to_completion,
    update_json,
)
from plugins.paths import data_path
from plugins.rss_polling import read_rss_poll_interval, run_rss_poll_loop
from plugins.x_rss_parser import parse_feed, QUOTE_TEXT_LIMIT
from plugins.x_rate_limit import (
    choose_overflow,
    normalize_x_protection_config,
    prune_sent_timestamps,
    rate_wait_seconds,
)


# RSS 拉取超时（RSSHub 为本机/内网服务，无需代理）
FEED_TIMEOUT = 30
# RSSHub 请求属于外部 I/O：有限并发避免慢源拖住整轮，
# 同时避免一次性并发过多触发 RSSHub/代理限流。
X_POLL_CONCURRENCY = 4
DEFAULT_X_POLL_INTERVAL = 300
# RSSHub 的 429、5xx 和连接异常通常是短暂的；同一轮内做有限退避重试，
# 避免把偶发故障直接延后到下一个完整轮询周期。非临时 4xx 不重试。
FEED_MAX_ATTEMPTS = 3
FEED_RETRY_DELAYS = (1, 3)
# RSSHub 暂时不可用时使用 X 公开时间线作为回退，避免本地 RSSHub
# 停止或 Twitter 路由短暂异常时整条 RSS 链路长期显示失败。
SYNDICATION_BASE = "https://syndication.twitter.com/srv/timeline-profile/screen-name"

# 运行时状态：每源的 ETag / 最近轮询时间（data/ 已在 .gitignore，
# 文件首次轮询时自动创建，不进版本库）
X_STATE_FILE = data_path("x_rss.json")
X_PROTECTION_FILE = data_path("x_protection.json")

# 每源最近一次解析结果（内存缓存）：304 时复用并重跑发送循环。
# 进程重启后缓存丢失，此时收到 304 会强制重取一次全量（自愈）。
_posts_cache = {}
_state_file_lock = threading.Lock()
_protection_file_lock = threading.Lock()
_protection_state_lock = threading.RLock()
_protection_load_lock = threading.Lock()
_protection_lock = asyncio.Lock()
_protection_wakeup = asyncio.Event()
_protection_runtime_state = None
_protection_load_error = ""
_protection_runtime_error = ""
_dispatch_counter_lock = threading.Lock()
_dispatch_sent_since_poll = 0
_dispatch_failed_since_poll = 0
_PROTECTION_STATE_MISSING = object()
_feed_client = None
_x_request_tasks = set()
_dispatch_task = None
_feed_stopping = False
_syndication_lock = asyncio.Lock()


def _get_feed_client():
    global _feed_client
    if _feed_client is None:
        # .env.prod 的代理只服务 Discord；localhost RSSHub 必须直连，
        # 否则代理会把连接失败改写成无上下文的 HTTP 502。
        _feed_client = httpx.AsyncClient(follow_redirects=True, trust_env=False)
    return _feed_client


async def _x_get(*args, **kwargs):
    """Track X RSS GET requests so shutdown can reap one-shot and poll calls."""
    if _feed_stopping:
        raise asyncio.CancelledError("X RSS HTTP client is shutting down")
    task = asyncio.current_task()
    if task is not None:
        _x_request_tasks.add(task)
    try:
        return await _get_feed_client().get(*args, **kwargs)
    finally:
        if task is not None:
            _x_request_tasks.discard(task)


@get_driver().on_shutdown
async def _close_feed_client():
    global _feed_client, _poll_task, _dispatch_task, _loop_started, _poll_stopping, _feed_stopping
    _feed_stopping = True
    _poll_stopping = True
    current_task = asyncio.current_task()
    pending_tasks = set()
    poll_task = _poll_task
    if poll_task is not None and not poll_task.done():
        pending_tasks.add(poll_task)
    dispatch_task = _dispatch_task
    if dispatch_task is not None and not dispatch_task.done():
        pending_tasks.add(dispatch_task)
    pending_tasks.update(
        task for task in _x_request_tasks
        if task is not current_task and not task.done()
    )
    for task in pending_tasks:
        task.cancel()
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    _x_request_tasks.clear()
    if _poll_task is poll_task:
        _poll_task = None
    if _dispatch_task is dispatch_task:
        _dispatch_task = None
    _protection_wakeup.clear()
    _loop_started = False
    _runtime_status["loop_started"] = False
    if _feed_client is not None:
        await _feed_client.aclose()
        _feed_client = None


# =====================================================
# 运行时状态（仅供管理面板 X → QQ 页状态卡片展示）
#
# 内存态，重启清零，不写入 settings.json 等用户配置。字段：
# - loop_started      轮询循环是否已启动（应用启动后即启动；QQ 未连接时等待）
# - last_poll_ts      上一轮轮询完成时间（epoch 秒；0 = 本进程尚未轮询过）
# - last_result       上一轮结果："ok" / "fetch_failed" / "parse_failed" / "protection_failed"
# - last_error        上一轮失败的简短原因（成功轮清空）
# - last_new_count    上一轮发现的新帖数（存在该帖未送达的目标群）
# - last_sent_count   上一轮发送成功帖数（至少一个群送达）
# - last_failed_count 最近一轮轮询或后台投递中的失败帖数
# =====================================================

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
    """返回 X RSS 运行时状态快照（面板状态接口使用）"""
    return dict(_runtime_status)


def _record_dispatch_counters(sent=0, failed=0):
    """将后台投递结果计入下一次完成的轮询状态快照。"""

    global _dispatch_sent_since_poll, _dispatch_failed_since_poll
    with _dispatch_counter_lock:
        _dispatch_sent_since_poll += int(sent or 0)
        _dispatch_failed_since_poll += int(failed or 0)


def _short_error(err, limit=120):
    """压成单行并截断的错误摘要（面板展示用，不带完整堆栈）"""
    return " ".join(str(err or "").split())[:limit]


def _load_state():
    data = load_json(X_STATE_FILE, default={})
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("sources"), dict):
        data["sources"] = {}
    return data


async def _load_state_async():
    """Read RSS metadata state without blocking the asyncio event loop."""
    return await asyncio.to_thread(_load_state)


class XProtectionError(RuntimeError):
    """X 投递保护状态无法安全读取或保存。"""


def _read_protection_state():
    try:
        with open(X_PROTECTION_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return _PROTECTION_STATE_MISSING


def _load_protection_state():
    with _protection_load_lock:
        return _load_protection_state_locked()


def _load_protection_state_locked():
    """加载并校验持久化队列；顶层损坏时暂停投递且保留原文件。"""

    global _protection_runtime_state, _protection_load_error, _protection_runtime_error
    with _protection_state_lock:
        if _protection_runtime_state is not None:
            return _protection_runtime_state, _protection_load_error

    state = {"version": 1, "queues": {}, "sent_at": []}
    try:
        raw = _read_protection_state()
    except (OSError, ValueError) as exc:
        error = "X 积压队列读取失败：{}".format(_short_error(exc))
        with _protection_state_lock:
            should_log = error != _protection_load_error
            _protection_load_error = error
        if should_log:
            logger.error("{}；已暂停 X 自动投递并保留原文件", error)
        return state, error

    if raw is _PROTECTION_STATE_MISSING:
        raw = {}
    if not isinstance(raw, dict):
        error = "X 积压队列格式无效：顶层 JSON 必须是对象"
        with _protection_state_lock:
            should_log = error != _protection_load_error
            _protection_load_error = error
        if should_log:
            logger.error("{}；已暂停 X 自动投递并保留原文件", error)
        return state, error
    version = raw.get("version", 1)
    if type(version) is not int or version != 1:
        error = "X 积压队列版本不受支持"
        with _protection_state_lock:
            should_log = error != _protection_load_error
            _protection_load_error = error
        if should_log:
            logger.error("{}；已暂停 X 自动投递并保留原文件", error)
        return state, error
    queues = raw.get("queues", {})
    if not isinstance(queues, dict):
        error = "X 积压队列格式无效：queues 必须是对象"
        with _protection_state_lock:
            should_log = error != _protection_load_error
            _protection_load_error = error
        if should_log:
            logger.error("{}；已暂停 X 自动投递并保留原文件", error)
        return state, error

    sent_at = raw.get("sent_at")
    if isinstance(sent_at, list):
        state["sent_at"] = prune_sent_timestamps(sent_at, time.time())
    for source_key, bucket in queues.items():
        if not isinstance(bucket, dict):
            continue
        items = []
        seen = set()
        raw_items = bucket.get("items")
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict) or not isinstance(item.get("post"), dict):
                continue
            post = item["post"]
            post_id = str(item.get("id") or post.get("id") or "").strip()
            cursor_id = str(item.get("cursor_id") or post.get("id") or "").strip()
            if (
                not post_id
                or not cursor_id.isascii()
                or not cursor_id.isdecimal()
                or post_id in seen
            ):
                continue
            seen.add(post_id)
            items.append({"id": post_id, "cursor_id": cursor_id, "post": post})
        deferred = []
        deferred_seen = set()
        raw_deferred = bucket.get("deferred_skips")
        if isinstance(raw_deferred, list):
            for item in raw_deferred:
                if not isinstance(item, dict):
                    continue
                post_id = str(item.get("id") or "").strip()
                cursor_id = str(item.get("cursor_id") or "").strip()
                if (
                    not post_id
                    or not cursor_id.isascii()
                    or not cursor_id.isdecimal()
                    or post_id in deferred_seen
                ):
                    continue
                deferred_seen.add(post_id)
                deferred.append({"id": post_id, "cursor_id": cursor_id})
        if items or deferred:
            state["queues"][str(source_key)] = {
                "items": sorted(items, key=lambda item: int(item["cursor_id"])),
                "deferred_skips": sorted(
                    deferred,
                    key=lambda item: int(item["cursor_id"]),
                ),
            }

    with _protection_state_lock:
        _protection_load_error = ""
        _protection_runtime_error = ""
        _protection_runtime_state = state
        return _protection_runtime_state, ""


async def _load_protection_state_async():
    """Read the delivery queue without blocking the asyncio event loop."""

    return await asyncio.to_thread(_load_protection_state)


def _save_protection_state(state):
    global _protection_runtime_error
    if _protection_load_error:
        logger.warning("X 积压队列状态不完整，跳过保存以保留原文件")
        return False
    try:
        with _protection_file_lock:
            atomic_write_json(X_PROTECTION_FILE, state, indent=2)
        _protection_runtime_error = ""
        return True
    except Exception as exc:
        _protection_runtime_error = "X 投递保护状态保存失败：{}".format(_short_error(exc))
        logger.warning("{}", _protection_runtime_error)
        return False


async def _save_protection_state_async(state):
    """Persist an isolated snapshot and keep the caller lock through cancellation."""

    snapshot = copy.deepcopy(state)
    return await run_in_thread_to_completion(_save_protection_state, snapshot)


def _protection_bucket(state, source_key):
    return state["queues"].setdefault(
        str(source_key),
        {"items": [], "deferred_skips": []},
    )


def _save_state(mutate):
    """Apply one locked JSON update; malformed existing state stays untouched."""
    try:
        update_json(X_STATE_FILE, mutate, default={}, indent=2, write_if=bool)
    except Exception as exc:
        # 状态文件写失败只影响 ETag 命中率，不影响去重正确性
        # （去重游标在 discord_last.json，由 history.py 负责落盘）
        logger.warning("X RSS 状态文件写入失败: {}", exc)


def _update_source_state(key, etag=None, clear_etag=False, source_url=None):
    """更新单个源的 URL、ETag / 最近轮询时间并落盘。"""
    # 多源并发时保护 read-modify-write，避免互相覆盖 ETag。
    with _state_file_lock:
        def mutate(state):
            sources = state.setdefault("sources", {})
            if not isinstance(sources, dict):
                raise ValueError("X RSS 状态 sources 必须是对象")
            src_state = sources.get(key)
            if not isinstance(src_state, dict):
                src_state = {}
                sources[key] = src_state
            src_state["last_poll_ts"] = int(time.time())
            if source_url is not None:
                src_state["url"] = source_url
            if clear_etag:
                src_state.pop("etag", None)
            elif etag:
                src_state["etag"] = etag
            return True

        _save_state(mutate)


async def _update_source_state_async(*args, **kwargs):
    """Update and persist RSS metadata outside the asyncio event loop."""
    return await asyncio.to_thread(_update_source_state, *args, **kwargs)


# =====================================================
# RSS 拉取（ETag / If-None-Match / 304）
# =====================================================

async def _fetch_feed(url, etag=None):
    """拉取 RSS；返回 (xml_bytes | None(304), 新etag | None, 错误 | None)

    - 200：返回 (内容, 本次ETag, None)
    - 304：返回 (None, 原etag, None) —— 内容未变
    - 其他/异常：返回 (None, None, 错误) —— 调用方不推进任何状态

    实测（RSSHub/Express）：带 gzip 时 compression 中间件会把强 ETag
    弱化为 W/"..."，而该服务端只对强形式回 304；因此请求 identity
    编码，并在回传 If-None-Match 时剥掉 W/ 前缀（弱比较语义下等价，
    对规范服务器同样成立）。"""
    headers = {"Accept-Encoding": "identity"}
    if isinstance(etag, str) and etag:
        headers["If-None-Match"] = etag.removeprefix("W/")
    last_error = ""
    for attempt in range(FEED_MAX_ATTEMPTS):
        try:
            resp = await _x_get(
                url,
                headers=headers,
                timeout=FEED_TIMEOUT,
            )
        except Exception as exc:
            last_error = f"请求异常: {exc}"
            retriable = True
        else:
            if resp.status_code == 304:
                return None, etag, None
            if resp.status_code == 200:
                return resp.content, resp.headers.get("etag"), None
            last_error = f"HTTP {resp.status_code}"
            retriable = resp.status_code == 429 or resp.status_code >= 500

        if not retriable or attempt + 1 >= FEED_MAX_ATTEMPTS:
            break

        delay = FEED_RETRY_DELAYS[min(attempt, len(FEED_RETRY_DELAYS) - 1)]
        logger.warning(
            "X RSS 拉取临时失败，将在 {} 秒后重试（{}/{}）: {}",
            delay,
            attempt + 1,
            FEED_MAX_ATTEMPTS,
            last_error,
        )
        await asyncio.sleep(delay)

    # RSSHub 服务本身不可用时，回退到 X 的公开时间线接口。只有能从
    # RSSHub 标准路径提取出用户名时才启用，避免改变自定义 RSS URL 行为。
    match = re.search(r"/twitter/user/([^/?#]+)", str(url or ""))
    if match:
        handle = unquote(match.group(1)).lstrip("@")
        fallback_url = f"{SYNDICATION_BASE}/{quote(handle, safe='')}"
        try:
            # X 公开时间线对同一出口的并发请求较敏感；同一轮多个源
            # 回退时串行访问，避免把正常的 RSS 故障放大成 429。
            async with _syndication_lock:
                response = await _x_get(
                    fallback_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "User-Agent": "Mozilla/5.0 (WRelay RSS fallback)",
                    },
                    timeout=FEED_TIMEOUT,
                )
            if response.status_code == 200 and response.content:
                # 公开时间线对不存在的用户也会返回 200 空页面；先校验
                # __NEXT_DATA__，避免把无效源记录成 RSS 正常。
                parse_feed(response.content)
                logger.warning("RSSHub 不可用，已使用 X 公开时间线回退: {}", handle)
                return response.content, None, None
            fallback_error = f"HTTP {response.status_code}"
        except Exception as exc:
            fallback_error = f"请求异常: {exc}"
        logger.warning("X 公开时间线回退失败（{}）: {}", handle, fallback_error)
        last_error = f"{last_error}; 回退失败: {fallback_error}"
    return None, None, last_error


def is_x_status_url(text):
    """判断文本是否以 X/Twitter 单条推文链接开头。"""
    return re.match(
        r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)",
        str(text or "").strip(),
    )


async def fetch_x_post(url):
    """通过 RSSHub Tweet Details 路由抓取单条 X 推文。

    返回 ``(post, source_name, error)``；post 使用 x_rss_parser 的统一结构。
    """
    match = is_x_status_url(url)
    if not match:
        return None, None, "链接格式无法解析"

    handle, status_id = match.group(1), match.group(2)
    rsshub_base = await asyncio.to_thread(get_x_rsshub_base)
    api_url = (
        f"{rsshub_base}/twitter/tweet/"
        f"{quote(handle, safe='')}/status/{status_id}"
    )
    try:
        response = await _x_get(api_url, timeout=FEED_TIMEOUT)
    except Exception as exc:
        logger.warning("X 单条推文抓取异常: {}", exc)
        return None, None, "请求异常"

    if response.status_code != 200:
        logger.warning("X 单条推文抓取错误: {} {}", response.status_code, response.text)
        return None, None, f"HTTP {response.status_code}"

    try:
        posts = parse_feed(response.content)
    except Exception as exc:
        logger.warning("X 单条推文解析失败: {}", exc)
        return None, None, "内容解析失败"

    post = next((item for item in posts if str(item.get("id")) == status_id), None)
    if post is None:
        return None, None, "RSSHub 未返回该推文"
    return post, post.get("author") or handle, None


# =====================================================
# 渲染：推文 dict → (text_parts, media_items)
# =====================================================

_IMAGE_EXT_RE = re.compile(r"[?&]format=(\w+)")


def _image_filename(url):
    """从 pbs.twimg.com URL 的 format= 参数推断文件名扩展名"""
    m = _IMAGE_EXT_RE.search(url or "")
    ext = m.group(1).lower() if m else "jpg"
    return f"image.{ext}"


async def _render_post(
    post,
    source_name,
    source_label="自动转发来自",
    enabled_types=None,
    show_datetime=False,
):
    """推文 dict → (text_parts, media_items) 或 None（无可发送内容）

    文字层级与 RSS 顺序一致：header → RT 行 → 主推文字 → 引用行；
    媒体单独成条（主推媒体在前、引用媒体在后）。"""
    text_parts = []
    media_items = []

    def type_enabled(kind):
        return enabled_types is None or kind in enabled_types

    header = f"{source_label} X 中 [{source_name}] 的消息："
    if show_datetime:
        post_time = format_message_datetime(post.get("pub_date"))
        if post_time:
            header += "\n时间：" + post_time
    text_parts.append(make_text(header))

    has_body = False

    if post["is_retweet"] and post["rt_author"] and type_enabled("文字"):
        text_parts.append(
            make_text(f"\n↻ RT {post['rt_author']}：")
        )
        has_body = True

    if post["text"] and type_enabled("文字"):
        text_parts.append(
            make_text("\n" + post["text"])
        )
        has_body = True

    quote = post.get("quote")
    if quote and type_enabled("文字") and quote.get("text"):
        quote_text = quote.get("text") or ""
        if len(quote_text) > QUOTE_TEXT_LIMIT:
            quote_text = quote_text[:QUOTE_TEXT_LIMIT] + "…"
        text_parts.append(
            make_text(
                f"\n┃ 引用 {quote.get('author') or '（未知作者）'}：{quote_text}"
            )
        )
        has_body = True

    all_media = list(post["media"])
    if quote:
        all_media.extend(quote.get("media") or [])

    all_media = [
        media for media in all_media
        if type_enabled("图片" if media.get("kind") == "图片" else "文件")
    ]

    if all_media:
        has_body = True
        urls = [m["url"] for m in all_media]
        bytes_map = await fetch_bytes_many(urls)
        for m in all_media:
            data = bytes_map.get(m["url"])
            if data and m["kind"] == "图片":
                segment = QQMessageSegment.file_image(
                    data,
                    _image_filename(m["url"]),
                )
            elif data and m["kind"] == "视频":
                # ≤30MB（fetch_bytes 上限）：直接发视频本体
                segment = QQMessageSegment.file_video(
                    data,
                    "video.mp4",
                )
            else:
                # 下载失败/超限：降级为文字链接
                logger.warning(
                    "X 媒体下载失败，降级为链接: {} {}",
                    m["kind"],
                    m["url"],
                )
                segment = make_media_link(m["kind"], m["url"])
            media_items.append({
                "kind": m["kind"],
                "url": m["url"],
                "segment": segment,
            })

    if not has_body:
        # 纯 header 无任何内容（正文与媒体全为空的异常 item）不发送
        return None

    return text_parts, media_items


async def render_x_post(post, source_name, enabled_types=None, show_datetime=False):
    """渲染私聊手动转发的 X 推文，沿用自动 RSS 的内容格式。"""
    return await _render_post(
        post,
        source_name,
        source_label="手动转发来自",
        enabled_types=enabled_types,
        show_datetime=show_datetime,
    )


# =====================================================
# 去重游标（复用 discord_last.json + dedup.py）
# 来源游标保持独立；发送入口另按 X 原生推文 ID 和目标群跨来源判重。
# =====================================================

def _source_channel_key(source_key):
    """X 源在 discord_last.json 中的伪频道键"""
    return f"x_rss:{source_key}"


async def _ensure_baselines(channel_key, groups, latest_id):
    """确保所有启用群都有游标基线（只建游标，不触发发送）。

    - 源完全无记录（首次订阅）：全部群建基线，不发送历史内容
    - 源已有记录但个别群无游标（中途新启用的群）：只为这些群建基线，
      避免新群收到 feed 窗口内（最多 20 条）的历史刷屏
    返回是否产生变更。"""
    def _mutate(last):
        channel_map = normalize_channel_map(last, channel_key)
        if not channel_map:
            return apply_baseline(channel_map, groups, latest_id)
        missing = [
            group for group in groups
            if not (channel_map.get(group) or channel_map.get("*"))
        ]
        if missing:
            return apply_baseline(channel_map, missing, latest_id)
        return False

    return await update_last_messages(_mutate)


async def _load_source_delivery_config(source_key):
    return await asyncio.to_thread(get_x_source_delivery_config, source_key)


async def _load_source_groups(source_key):
    return await asyncio.to_thread(get_x_groups_for_source, source_key)


async def _load_polling_snapshot():
    return await asyncio.to_thread(get_x_rss_polling_snapshot)


# =====================================================
# 发送循环（200 新内容与 304 缓存复用共用）
# =====================================================

async def _record_post_delivery_result(name, post_id, targets, ok_map, counts):
    """Update per-round counters and logs from one send result."""
    sent_groups = getattr(ok_map, "sent_groups", ())
    deduplicated_groups = getattr(ok_map, "deduplicated_groups", ())
    busy_groups = getattr(ok_map, "busy_groups", ())
    attempted_groups = (
        set(targets) - set(deduplicated_groups) - set(busy_groups)
    )
    if sent_groups or attempted_groups or busy_groups:
        counts["new"] += 1

    if sent_groups:
        counts["sent"] += 1
        await record(True)
        logger.info(
            "X 推文已发送: {} {} -> {}",
            name,
            post_id,
            sorted(sent_groups),
        )
    elif deduplicated_groups and not attempted_groups and not busy_groups:
        logger.debug(
            "X 推文已由其他来源发送，跳过重复投递: {} {}",
            name,
            post_id,
        )
    elif busy_groups and not attempted_groups:
        logger.debug(
            "X 推文正在由其他来源投递，留待下一轮处理: {} {}",
            name,
            post_id,
        )
    else:
        counts["failed"] += 1
        await record(False)
        logger.warning(
            "X 推文全部目标群发送失败（游标不动，下轮补投）: {} {}",
            name,
            post_id,
        )


async def _skip_protection_items(channel_key, active_groups, items):
    """将明确裁掉的条目推进游标，避免每次轮询重新入队。"""

    if not items or not active_groups:
        return
    ordered = sorted(items, key=lambda item: int(item["cursor_id"]))

    def mutate(last):
        channel_map = normalize_channel_map(last, channel_key)
        changed = False
        for item in ordered:
            targets = select_target_groups(
                channel_map,
                active_groups,
                item["cursor_id"],
            )
            if targets:
                changed = apply_skipped(
                    channel_map,
                    targets,
                    item["cursor_id"],
                ) or changed
        return changed

    await update_last_messages(mutate)


async def _flush_deferred_skips(bucket, channel_key, active_groups):
    if bucket["items"] or not bucket["deferred_skips"]:
        return
    deferred = list(bucket["deferred_skips"])
    await _skip_protection_items(channel_key, active_groups, deferred)
    bucket["deferred_skips"] = []


def _append_deferred_skips(bucket, items):
    known = {item["id"] for item in bucket["deferred_skips"]}
    bucket["deferred_skips"].extend(
        {"id": item["id"], "cursor_id": item["cursor_id"]}
        for item in items
        if item["id"] not in known
    )
    bucket["deferred_skips"].sort(key=lambda item: int(item["cursor_id"]))


async def _remove_protection_item(bucket, item, channel_key, active_groups):
    if item in bucket["items"]:
        bucket["items"].remove(item)
        await _flush_deferred_skips(bucket, channel_key, active_groups)


async def _load_x_protection_config():
    return await asyncio.to_thread(get_x_protection)


async def _send_posts(source, posts):
    """把新推文放入持久队列；后台投递器按保护配置逐条发送。"""

    if not posts:
        return (0, 0, 0)
    source_key = str(source["key"])
    channel_key = _source_channel_key(source_key)

    async with _protection_lock:
        state, load_error = await _load_protection_state_async()
        if load_error:
            raise XProtectionError(load_error)
        active_groups = await _load_source_groups(source_key)
        if not active_groups:
            logger.debug("X RSS 无可用目标群，跳过发送: {}", source["name"])
            return (0, 0, 0)

        latest_id = max(posts, key=lambda post: int(post["id"]))["id"]
        await _ensure_baselines(channel_key, active_groups, latest_id)
        bucket = _protection_bucket(state, source_key)
        queued_ids = {item["id"] for item in bucket["items"]}
        deferred_ids = {item["id"] for item in bucket["deferred_skips"]}
        delivery_config = await _load_source_delivery_config(source_key)
        additions = []
        state_changed = False

        for post in sorted(posts, key=lambda item: int(item["id"])):
            post_id = str(post.get("id") or "").strip()
            if (
                not post_id
                or not post_id.isascii()
                or not post_id.isdecimal()
                or post_id in queued_ids
                or post_id in deferred_ids
            ):
                continue
            channel_map = normalize_channel_map(
                await load_last_messages_async(load_last_messages),
                channel_key,
            )
            targets = select_target_groups(channel_map, active_groups, post_id)
            if not targets:
                continue
            if not check_x_post_filter(delivery_config["filter"], post):
                skipped = {"id": post_id, "cursor_id": post_id}
                if bucket["items"] or additions:
                    deferred_count = len(bucket["deferred_skips"])
                    _append_deferred_skips(bucket, [skipped])
                    state_changed = state_changed or (
                        len(bucket["deferred_skips"]) != deferred_count
                    )
                else:
                    await update_last_messages(
                        lambda last: apply_success(
                            normalize_channel_map(last, channel_key),
                            {group: True for group in targets},
                            post_id,
                        )
                    )
                continue
            additions.append({"id": post_id, "cursor_id": post_id, "post": post})
            queued_ids.add(post_id)

        protection = normalize_x_protection_config(
            await _load_x_protection_config()
        )
        items = sorted(
            bucket["items"] + additions,
            key=lambda item: int(item["cursor_id"]),
        )
        kept, dropped = choose_overflow(
            items,
            protection["backlog_limit"],
            protection["overflow_policy"],
        )
        bucket["items"] = kept
        if dropped:
            if protection["overflow_policy"] == "latest":
                await _skip_protection_items(channel_key, active_groups, dropped)
            else:
                _append_deferred_skips(bucket, dropped)
            state_changed = True
        if additions or state_changed:
            if not await _save_protection_state_async(state):
                raise XProtectionError("X 积压队列保存失败")
        if bucket["items"]:
            _protection_wakeup.set()
        return (len(additions), 0, 0)


async def _dispatch_one_locked():
    """全局保护锁内最多投递一个 X 推文；返回再次尝试前的等待秒数。"""

    global _protection_runtime_error
    state, load_error = await _load_protection_state_async()
    if load_error:
        raise XProtectionError(load_error)
    if _protection_runtime_error.startswith("X 投递保护状态保存失败"):
        if not await _save_protection_state_async(state):
            return 30
    elif _protection_runtime_error:
        _protection_runtime_error = ""
    pending = [
        (source_key, bucket, item)
        for source_key, bucket in state["queues"].items()
        for item in bucket["items"]
    ]
    if not pending:
        return None

    if not any(isinstance(bot, QQBot) for bot in get_bots().values()):
        return 5

    protection = normalize_x_protection_config(
        await _load_x_protection_config()
    )
    now = time.time()
    state["sent_at"] = prune_sent_timestamps(state.get("sent_at"), now)
    wait = rate_wait_seconds(
        state["sent_at"],
        now,
        protection["min_send_interval"],
        protection["hourly_limit"],
    )
    if wait > 0:
        return wait

    configured_sources, groups_by_source = await _load_polling_snapshot()
    sources = {str(source["key"]): source for source in configured_sources}
    candidates = []
    for source_key, bucket, item in pending:
        source = sources.get(source_key)
        active_groups = groups_by_source.get(source_key, [])
        if source is not None and active_groups:
            candidates.append((int(item["cursor_id"]), source_key, source, bucket, item, active_groups))
    candidates.sort(key=lambda candidate: candidate[0])
    if not candidates:
        return 30

    for _, source_key, source, bucket, item, active_groups in candidates:
        channel_key = _source_channel_key(source_key)
        known_cursors = [
            int(queued.get("cursor_id") or 0)
            for queued in bucket["items"]
        ] + [
            int(skipped.get("cursor_id") or 0)
            for skipped in bucket["deferred_skips"]
        ]
        if known_cursors:
            # Persisted backlog may predate a newly enabled target group.
            # Give missing group cursors a safe baseline before dispatch.
            await _ensure_baselines(
                channel_key,
                active_groups,
                str(max(known_cursors)),
            )
        channel_map = normalize_channel_map(
            await load_last_messages_async(load_last_messages),
            channel_key,
        )
        targets = select_target_groups(
            channel_map,
            active_groups,
            item["cursor_id"],
        )
        if not targets:
            await _remove_protection_item(bucket, item, channel_key, active_groups)
            if not await _save_protection_state_async(state):
                raise XProtectionError("X 积压队列保存失败")
            continue

        try:
            delivery_config = await _load_source_delivery_config(source_key)
            if not check_x_post_filter(delivery_config["filter"], item["post"]):
                await _skip_protection_items(channel_key, active_groups, [item])
                await _remove_protection_item(bucket, item, channel_key, active_groups)
                if not await _save_protection_state_async(state):
                    raise XProtectionError("X 积压队列保存失败")
                continue

            rendered = await _render_post(
                item["post"],
                source["name"],
                show_datetime=delivery_config["translation"].get("show_datetime") is True,
            )
            if rendered is None:
                await _skip_protection_items(channel_key, active_groups, [item])
                await _remove_protection_item(bucket, item, channel_key, active_groups)
                if not await _save_protection_state_async(state):
                    raise XProtectionError("X 积压队列保存失败")
                continue
            text_parts, media_items = rendered
            text_parts = await translate_text_parts(
                text_parts,
                ("x_rss", source_key, item["id"]),
                skip_leading_parts=1,
                config=delivery_config["translation"],
            )

            # 投递前重新读取路由与游标，避免设置变化后仍投到已暂停的群。
            active_groups = await _load_source_groups(source_key)
            if not active_groups:
                return 5
            channel_map = normalize_channel_map(
                await load_last_messages_async(load_last_messages),
                channel_key,
            )
            targets = select_target_groups(
                channel_map,
                active_groups,
                item["cursor_id"],
            )
            if not targets:
                await _remove_protection_item(bucket, item, channel_key, active_groups)
                if not await _save_protection_state_async(state):
                    raise XProtectionError("X 积压队列保存失败")
                continue

            ok_map = await send_and_record(
                channel_key,
                item["cursor_id"],
                text_parts,
                media_items,
                targets,
                idempotency_key="x:%s" % item["id"],
            )
        except XProtectionError:
            raise
        except Exception as exc:
            logger.warning(
                "X 推文处理失败，保留在投递保护队列: {} {}: {}",
                source["name"],
                item["id"],
                exc,
            )
            _record_dispatch_counters(failed=1)
            return 15

        if not ok_map:
            logger.warning(
                "QQ Bot 未连接，X 推文保留在投递保护队列: {} {}",
                source["name"],
                item["id"],
            )
            return 5

        counts = {"new": 0, "sent": 0, "failed": 0}
        await _record_post_delivery_result(
            source["name"],
            item["id"],
            targets,
            ok_map,
            counts,
        )
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
            await _remove_protection_item(bucket, item, channel_key, active_groups)
        if counts["sent"]:
            state["sent_at"].append(time.time())
        _record_dispatch_counters(
            sent=counts["sent"],
            failed=counts["failed"],
        )
        if not await _save_protection_state_async(state):
            raise XProtectionError("X 积压队列保存失败")
        if counts["sent"]:
            return rate_wait_seconds(
                state["sent_at"],
                time.time(),
                protection["min_send_interval"],
                protection["hourly_limit"],
            )
        if counts["failed"]:
            return 15
        if remaining:
            return 2
    return 30


async def _dispatch_loop():
    """持续排空 X 队列，同时定期重新读取保护配置与 QQ 连接状态。"""

    global _protection_runtime_error
    while not _poll_stopping:
        # Clear before checking the queue. A producer that enqueues after this
        # point will set the event and cannot be lost between the empty check
        # and the wait below.
        _protection_wakeup.clear()
        try:
            async with _protection_lock:
                wait = await _dispatch_one_locked()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("X 投递保护循环异常，队列保留待重试: {}", exc)
            if not _protection_runtime_error.startswith("X 投递保护状态保存失败"):
                _protection_runtime_error = "X 投递保护循环异常：{}".format(
                    _short_error(exc)
                )
            wait = 30

        if wait is None:
            await _protection_wakeup.wait()
            continue
        try:
            await asyncio.wait_for(
                _protection_wakeup.wait(),
                timeout=max(0.1, min(float(wait), 5.0)),
            )
        except asyncio.TimeoutError:
            pass


# =====================================================
# 单源轮询
# =====================================================

async def _poll_source(source):
    """轮询一个 X 源。任何失败只记日志返回，绝不推进去重游标。

    返回单源结果 dict（供轮次汇总运行时状态，不影响转发行为）：
    {result: "ok"/"fetch_failed"/"parse_failed"/"protection_failed", error: str,
     new/sent/failed: int}"""
    key = source["key"]
    url = source["url"]
    name = source["name"]

    state = await _load_state_async()
    source_state = state["sources"].get(key, {})
    if not isinstance(source_state, dict):
        source_state = {}
    etag = source_state.get("etag")
    cached_posts = _posts_cache.get(key)

    # ETags and parsed posts belong to one feed URL. A changed or previously
    # unrecorded URL must get a full fetch before the per-key cache is reused.
    if source_state.get("url") != url:
        etag = None
        cached_posts = None
        _posts_cache.pop(key, None)
        await _update_source_state_async(key, clear_etag=True, source_url=url)

    body, new_etag, err = await _fetch_feed(url, etag)

    if err is None and body is None:
        if cached_posts is not None:
            # 304：内容未变，复用上次解析结果补充队列
            logger.debug("X RSS 304 未变化: {}", name)
            new_n, sent_n, failed_n = await _send_posts(source, cached_posts)
            return {"result": "ok", "error": "",
                    "new": new_n, "sent": sent_n, "failed": failed_n}
        # 重启后缓存丢失且命中 304：强制重取一次全量
        body, new_etag, err = await _fetch_feed(url, None)

    if err:
        logger.warning("X RSS 拉取失败，本轮跳过: {} ({}): {}", name, key, err)
        return {"result": "fetch_failed", "error": _short_error(err),
                "new": 0, "sent": 0, "failed": 0}

    try:
        posts = parse_feed(body)
    except Exception as exc:
        # XML 损坏/RSSHub 路由错误页：本轮放弃，游标不动；
        # 丢弃 ETag，下轮强制 200 重新拉取
        logger.warning("X RSS 解析失败，本轮跳过: {} ({}): {}", name, key, exc)
        await _update_source_state_async(key, clear_etag=True)
        return {"result": "parse_failed", "error": _short_error(exc),
                "new": 0, "sent": 0, "failed": 0}

    # 解析成功才更新 ETag 与缓存
    await _update_source_state_async(
        key,
        etag=new_etag,
        clear_etag=not bool(new_etag),
        source_url=url,
    )
    _posts_cache[key] = posts

    new_n, sent_n, failed_n = await _send_posts(source, posts)
    return {"result": "ok", "error": "",
            "new": new_n, "sent": sent_n, "failed": failed_n}


# =====================================================
# 轮询循环与启动
# =====================================================

async def _poll_all():
    """有限并发轮询全部启用源；单源异常不影响其他源。

    一轮结束后把聚合结果（完成时间/结果/新帖与发送计数）写入
    _runtime_status 供面板展示。状态文件的 read-modify-write 由锁保护。"""
    global _dispatch_sent_since_poll, _dispatch_failed_since_poll
    configured_sources, groups_by_source = await _load_polling_snapshot()
    configured_keys = {source["key"] for source in configured_sources}
    for cached_key in list(_posts_cache):
        if cached_key not in configured_keys:
            _posts_cache.pop(cached_key, None)

    # 没有启用目标群的源不需要拉取；否则关闭的路由仍会消耗 X 回退配额。
    eligible_sources = [
        s for s in configured_sources
        if s["enabled"] and groups_by_source.get(s["key"])
    ]
    sources = []
    seen_source_keys = set()
    for source in eligible_sources:
        source_key = str(source.get("key") or "").strip()
        normalized_key = source_key.casefold()
        if normalized_key in seen_source_keys:
            logger.warning("X RSS 配置存在重复来源 key，跳过后续轮询: {}", source_key)
            continue
        seen_source_keys.add(normalized_key)
        sources.append(source)
    if not sources:
        _runtime_status["source_health"] = []
        return

    sem = asyncio.Semaphore(X_POLL_CONCURRENCY)

    async def poll_one(source):
        async with sem:
            try:
                return await _poll_source(source)
            except XProtectionError as exc:
                logger.error("X 投递保护暂停自动投递: {}", exc)
                return {"result": "protection_failed", "error": _short_error(exc),
                        "new": 0, "sent": 0, "failed": 0}
            except Exception as exc:
                # 单源任何未预期异常都不拖垮其他源与轮询循环
                logger.exception("X RSS 源轮询异常: {} ({})", source["name"], source["key"])
                return {"result": "fetch_failed", "error": _short_error(exc),
                        "new": 0, "sent": 0, "failed": 0}

    results = await asyncio.gather(*(poll_one(source) for source in sources))
    previous = {item["id"]: item for item in _runtime_status.get("source_health", [])}
    health = []
    for source, result in zip(sources, results):
        identity = str(source["key"])
        count = 0 if result.get("result") == "ok" else previous.get(identity, {}).get("consecutive_failures", 0) + 1
        health.append({"id": identity, "name": source["name"], "consecutive_failures": count})
    _runtime_status["source_health"] = health
    new_total = sent_total = failed_total = 0
    bad_result = ""
    bad_error = ""
    for res in results:
        new_total += res.get("new", 0)
        sent_total += res.get("sent", 0)
        failed_total += res.get("failed", 0)
        # 取本轮第一个失败源作为轮次结果（全部成功则记 ok）
        if not bad_result and res.get("result") not in (None, "ok"):
            bad_result = res["result"]
            bad_error = res.get("error") or ""

    with _dispatch_counter_lock:
        sent_total += _dispatch_sent_since_poll
        failed_total += _dispatch_failed_since_poll
        _dispatch_sent_since_poll = 0
        _dispatch_failed_since_poll = 0

    st = _runtime_status
    st["last_poll_ts"] = int(time.time())
    st["last_new_count"] = new_total
    st["last_sent_count"] = sent_total
    st["last_failed_count"] = failed_total
    st["last_result"] = bad_result or "ok"
    st["last_error"] = bad_error


async def _poll_loop():
    await run_rss_poll_loop(
        source_name="X RSS",
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
        get_x_rss_poll_interval,
        DEFAULT_X_POLL_INTERVAL,
        source_name="X RSS",
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
            logger.error("X RSS 轮询任务异常退出: {}", error)


def _on_dispatch_task_done(task):
    global _dispatch_task
    if _dispatch_task is task:
        _dispatch_task = None
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.error("X RSS 投递保护任务异常退出: {}", error)
            if not _poll_stopping:
                task.get_loop().call_soon(_ensure_poll_loop_started)


def _ensure_poll_loop_started():
    """Start the worker independently of bot connection hook execution."""
    global _loop_started, _poll_task, _dispatch_task
    if _poll_stopping:
        return
    if _poll_task is None or _poll_task.done():
        _loop_started = True
        _runtime_status["loop_started"] = True
        logger.info("X RSS 轮询后台任务已启动；QQ 连接状态每 5 秒检查一次")
        _poll_task = asyncio.create_task(_poll_loop())
        _poll_task.add_done_callback(_on_poll_task_done)
    if _dispatch_task is None or _dispatch_task.done():
        logger.info("X RSS 投递保护队列后台任务已启动")
        _dispatch_task = asyncio.create_task(_dispatch_loop())
        _dispatch_task.add_done_callback(_on_dispatch_task_done)
    _protection_wakeup.set()


@get_driver().on_startup
async def _start_poll_loop():
    """Start polling at application startup, before QQ connection hooks run."""
    _ensure_poll_loop_started()


# =====================================================
# 面板状态接口（GET /api/x-rss/status）
#
# 供管理面板 X → QQ 页状态卡片轮询：运行时状态 + 实时 QQ 连接
# + 当前生效的源数/轮询间隔。与 /api/health 的 Discord/QQ 连接
# 状态相互独立，不把 X RSS 状态塞进 Discord/QQ 的连接布尔值里。
# =====================================================

_xrss_app = get_app()


@_xrss_app.get("/api/x-rss/status")
async def api_x_rss_status():
    bots = get_bots()
    status_config = await asyncio.to_thread(get_x_rss_status_snapshot)
    sources = status_config["sources"]
    protection = status_config["protection"]
    protection_state, protection_load_error = await _load_protection_state_async()
    data = runtime_status()
    data.update({
        "ok": True,
        "running": bool(data.get("loop_started")),
        "qq_connected": any(isinstance(b, QQBot) for b in bots.values()),
        "poll_interval": await asyncio.to_thread(get_x_rss_poll_interval),
        "sources_total": len(sources),
        "sources_enabled": sum(1 for s in sources if s["enabled"]),
        "protection": protection,
        "protection_queue_pending": sum(
            len(bucket.get("items") or [])
            for bucket in (protection_state.get("queues") or {}).values()
            if isinstance(bucket, dict)
        ),
        "protection_error": protection_load_error or _protection_runtime_error,
    })
    if protection_load_error:
        data["last_result"] = "protection_failed"
        data["last_error"] = protection_load_error
    return data
