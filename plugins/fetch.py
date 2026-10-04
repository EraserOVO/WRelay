import asyncio
import math
import os
import re
import time
from collections import OrderedDict

import httpx

from nonebot import get_driver, logger
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

from plugins.config import get_discord_token
from plugins.filter import (
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_AUDIO,
    MESSAGE_TYPE_FILE,
    MESSAGE_TYPE_LINK,
    MESSAGE_TYPE_EMOJI,
)
from plugins.media import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    VIDEO_EXTS,
    convert_mentions,
    convert_mention_ids,
    cleanup_markdown,
    parse_discord_emoji,
    parse_discord_emoji_parts,
    make_text,
    make_file_link,
    make_media_link,
    fetch_bytes_many,
)
from plugins.message_time import format_message_datetime


# =====================================================
# Discord 消息统一抓取 / 解析 / 渲染
#
# 单一职责：Discord 消息 → QQ 消息段 (text_parts, media_items)。
# 两条数据来源（实时事件 / REST 单条抓取）先归一为同一种
# 规范化消息 dict，再走同一份渲染逻辑 build_parts()：
#   - relay.py 用 normalize_event() 归一事件对象
#   - command.py 用 fetch_message() 按链接抓取
# 媒体下载统一走 media.py 的并发限流。
# =====================================================

DISCORD_API_TIMEOUT = 20
_discord_client = None
_discord_request_tasks = set()
_discord_stopping = False


def _get_discord_proxy():
    """Read the Discord adapter's dedicated proxy from the loaded NoneBot config."""
    try:
        proxy = getattr(get_driver().config, "discord_proxy", None)
    except Exception:
        return None
    if isinstance(proxy, str):
        proxy = proxy.strip()
        return proxy or None
    return None


def _get_discord_client():
    global _discord_client
    if _discord_client is None:
        _discord_client = httpx.AsyncClient(
            proxy=_get_discord_proxy(),
            timeout=DISCORD_API_TIMEOUT,
        )
    return _discord_client


async def _discord_request(method, *args, **kwargs):
    """Track one REST request so shutdown can reap it before closing HTTPX."""
    if _discord_stopping:
        raise asyncio.CancelledError("Discord HTTP client is shutting down")
    task = asyncio.current_task()
    if task is not None:
        _discord_request_tasks.add(task)
    try:
        return await getattr(_get_discord_client(), method)(*args, **kwargs)
    finally:
        if task is not None:
            _discord_request_tasks.discard(task)


@get_driver().on_shutdown
async def _close_discord_client():
    global _discord_client, _metadata_stopping, _discord_stopping
    _metadata_stopping = True
    _discord_stopping = True
    request_tasks = set(_discord_request_tasks)
    request_tasks.update(_channel_metadata_tasks.values())
    for task in request_tasks:
        if not task.done():
            task.cancel()
    if request_tasks:
        await asyncio.gather(*request_tasks, return_exceptions=True)
    _channel_metadata_tasks.clear()
    _discord_request_tasks.clear()
    if _discord_client is not None:
        await _discord_client.aclose()
        _discord_client = None

# =====================================================
# 补发抓取分页参数（仅 fetch_channel_messages_after 使用）
#
# - FETCH_PAGE_INTERVAL：翻页请求间隔秒数，避免大缺口连续请求过快触发限流
# - FETCH_COLLECT_CAP：单次收集消息总量上限，防止极大缺口无限占用内存；
#   未确认翻到缺口底部就超限时返回 []（本轮不补发），绝不返回不完整
#   列表让补发游标越过更旧的缺口消息
# - RATE_LIMIT_ATTEMPTS / RATE_LIMIT_FALLBACK：429 重试次数与
#   Retry-After 缺失/非法时的默认退避秒数
# =====================================================

FETCH_PAGE_INTERVAL = 0.5
FETCH_COLLECT_CAP = 1000
RATE_LIMIT_ATTEMPTS = 3
RATE_LIMIT_FALLBACK = 5.0
RATE_LIMIT_MAX_WAIT = 30.0

# 频道名称/最新消息是面板与补发统计使用的轻量 REST 查询；
# 遇到短暂连接异常、429 或 5xx 时重试一次，避免一次瞬时网络抖动
# 让面板显示不完整或把待补发数量误报为失败。
REST_RETRY_ATTEMPTS = 2
REST_RETRY_DELAY = 0.5

# 回复引用行正文显示上限（防止被回复的长文刷屏）
REFERENCE_TEXT_LIMIT = 50


def _rate_limit_wait(response, fallback):
    """从 429 响应头取 Retry-After（秒）；缺失/非法时用 fallback"""
    try:
        raw = response.headers.get("retry-after")
        if raw:
            wait = float(raw)
            if math.isfinite(wait):
                return max(fallback, min(wait, RATE_LIMIT_MAX_WAIT))
    except Exception:
        pass
    return fallback


async def _discord_get_with_retry(api, headers):
    """执行轻量 Discord GET，并对瞬时错误做有限重试。

    非临时 HTTP 错误原样返回给调用方；连接异常在重试耗尽后返回 None。
    该函数只供频道元数据/最新消息查询使用，分页补发仍沿用自己的
    完整限流与安全处理。
    """
    for attempt in range(REST_RETRY_ATTEMPTS):
        try:
            response = await _discord_request(
                "get",
                api,
                headers=headers,
            )
        except Exception as exc:
            if attempt + 1 >= REST_RETRY_ATTEMPTS:
                logger.warning(
                    "Discord API请求异常，重试耗尽（{}）: {}",
                    type(exc).__name__,
                    repr(exc),
                )
                return None
            logger.warning(
                "Discord API请求异常，将在 {} 秒后重试（{}/{}，{}）: {}",
                REST_RETRY_DELAY,
                attempt + 1,
                REST_RETRY_ATTEMPTS,
                type(exc).__name__,
                repr(exc),
            )
            await asyncio.sleep(REST_RETRY_DELAY)
            continue

        if response.status_code != 429 and response.status_code < 500:
            return response
        if attempt + 1 >= REST_RETRY_ATTEMPTS:
            return response

        wait = (
            _rate_limit_wait(response, REST_RETRY_DELAY)
            if response.status_code == 429
            else REST_RETRY_DELAY
        )
        logger.warning(
            "Discord API临时错误，将在 {} 秒后重试（{}/{}）: HTTP {}",
            wait,
            attempt + 1,
            REST_RETRY_ATTEMPTS,
            response.status_code,
        )
        await asyncio.sleep(wait)

    return None


def parse_discord_url(url):
    """解析 Discord 消息链接，返回 {guild_id, channel_id, message_id}"""
    pattern = (
        r"discord\.com/channels/"
        r"(\d+)/(\d+)/(\d+)"
    )

    result = re.search(
        pattern,
        url
    )

    if not result:
        return None

    return {
        "guild_id": result.group(1),
        "channel_id": result.group(2),
        "message_id": result.group(3),
    }


def is_discord_url(text):
    """判断文本是否为 Discord 消息链接（http 前缀）"""
    return re.match(
        r"https://discord\.com/channels/\d+/\d+/\d+",
        text
    )


# =====================================================
# REST 抓取（按链接拉单条消息）
# =====================================================

def _discord_id(value):
    """Discord ID → str；None/UNSET 哨兵 → 空串"""
    if value is None:
        return ""
    if type(value).__name__ == "_UNSET":
        return ""
    return str(value)


def _mention_name_map(mentions):
    """从 Discord mentions 列表提取用户 ID → 显示名映射。"""
    if not isinstance(mentions, (list, tuple)):
        return {}
    result = {}
    for mention in mentions:
        if isinstance(mention, dict):
            user_id = mention.get("id")
            name = (
                mention.get("global_name")
                or mention.get("username")
                or mention.get("display_name")
            )
        else:
            user_id = getattr(mention, "id", None)
            name = (
                getattr(mention, "global_name", None)
                or getattr(mention, "username", None)
                or getattr(mention, "display_name", None)
            )
        user_id = _discord_id(user_id)
        name = str(name or "").strip()
        if user_id and name:
            result[user_id] = name
    return result


def _reference_author_name(author):
    """返回引用作者的首个有效显示名；非字符串字段按缺失处理。"""
    if not isinstance(author, dict):
        return ""
    for key in ("global_name", "username"):
        value = author.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _extract_reference(ref_obj, referenced=None):
    """从 API 消息对象提取回复引用 → 可选 reference dict

    内联 referenced_message（含作者+正文）优先；只有 message_reference
    时仅带 message_id/channel_id，由 build_parts 阶段回退抓取补全。
    返回 {message_id, channel_id, username, content} 或 None"""
    if not isinstance(ref_obj, dict):
        ref_obj = {}
    if not isinstance(referenced, dict):
        referenced = {}

    message_id = referenced.get("id") or ref_obj.get("message_id")
    if not message_id:
        return None

    author = referenced.get("author")
    if not isinstance(author, dict):
        author = {}
    content = referenced.get("content")

    return {
        "message_id": _discord_id(message_id),
        "channel_id": _discord_id(
            ref_obj.get("channel_id")
            or referenced.get("channel_id")
        ),
        "username": _reference_author_name(author),
        "content": content if isinstance(content, str) else "",
        "mention_names": _mention_name_map(referenced.get("mentions")),
    }


def normalize_api_message(msg):
    """Discord API 消息对象 → 规范化 dict（含 message_id）

    供 fetch_message / fetch_channel_messages_after 复用。
    结构：{message_id, username, channel_id, channel_name, content,
           embeds, attachments, reference}
    reference 可选：{message_id, channel_id, username, content}
    或 None（非回复消息）。
    embeds: [{title, url, image_url}]
    attachments: [{url, filename}]"""
    author = msg.get("author")
    if not isinstance(author, dict):
        author = {}
    content = msg.get("content")
    if not isinstance(content, str):
        content = ""
    author_username = author.get("username")
    if not isinstance(author_username, str):
        author_username = ""

    username = (
        author.get("global_name")
        or author.get("username")
        or "unknown"
    )

    raw_embeds = msg.get("embeds")
    if not isinstance(raw_embeds, list):
        raw_embeds = []
    embeds = []
    for embed in raw_embeds:
        if not isinstance(embed, dict):
            continue
        image = embed.get("image") or {}
        image_url = image.get("url") if isinstance(image, dict) else None
        title = embed.get("title")
        url = embed.get("url")
        embeds.append({
            "title": title if isinstance(title, str) else None,
            "url": url if isinstance(url, str) else None,
            "image_url": image_url if isinstance(image_url, str) else None,
        })

    raw_attachments = msg.get("attachments")
    if not isinstance(raw_attachments, list):
        raw_attachments = []
    attachments = []
    for att in raw_attachments:
        if not isinstance(att, dict):
            continue
        url = att.get("url")
        if not isinstance(url, str) or not url:
            continue
        filename = att.get("filename")
        attachments.append({
            "url": url,
            "filename": filename if isinstance(filename, str) else "",
        })

    return {
        "message_id": str(msg.get("id") or ""),
        "author_username": author_username,
        "username": username,
        "channel_id": str(msg.get("channel_id") or ""),
        "channel_name": None,
        "created_at": msg.get("timestamp"),
        "content": content,
        "embeds": embeds,
        "attachments": attachments,
        "mention_names": _mention_name_map(msg.get("mentions")),
        "reference": _extract_reference(
            msg.get("message_reference"),
            msg.get("referenced_message"),
        ),
    }


def _pagination_message_id(value):
    """返回可安全用于补发游标比较的 Discord 十进制消息 ID。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        return None
    if not text or len(text) > 32 or not text.isascii() or not text.isdecimal():
        return None
    try:
        return text if int(text) > 0 else None
    except ValueError:
        return None


async def fetch_message(url):
    """按链接抓取 Discord 单条消息，返回 (规范化消息 dict, 失败原因 or None)

    失败时消息为 None，原因可用于提示用户（如 HTTP 403/404/429）。
    规范化结构：{username, channel_id, channel_name, content, embeds, attachments}
    embeds: [{title, url, image_url}]
    attachments: [{url, filename}]"""
    data = parse_discord_url(url)

    if not data:
        return None, "链接格式无法解析"

    channel_id = data["channel_id"]
    message_id = data["message_id"]

    api = (
        f"https://discord.com/api/v10/"
        f"channels/{channel_id}/messages/{message_id}"
    )

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    try:
        response = await _discord_request(
            "get",
            api,
            headers=headers,
        )
    except Exception as exc:
        logger.warning(
            "Discord API请求异常: {}",
            exc
        )
        return None, "请求异常"

    if response.status_code != 200:
        logger.warning(
            "Discord API错误: {} {}",
            response.status_code,
            response.text
        )
        return None, f"HTTP {response.status_code}"

    try:
        payload = response.json()
    except Exception as exc:
        logger.warning("Discord 消息 API 响应解析失败: {}", exc)
        return None, "响应格式无效"
    if not isinstance(payload, dict):
        logger.warning(
            "Discord 消息 API 响应根节点不是对象: {}",
            type(payload).__name__,
        )
        return None, "响应格式无效"

    message = normalize_api_message(payload)

    # 链接解析出的频道 ID 为准（与 API 返回应一致，保险起见覆盖）
    message["channel_id"] = channel_id

    return message, None


async def _fetch_discord_message_page(api, headers, channel_id):
    """抓取并解析一页 Discord 消息；失败与有效 JSON 分开返回。"""
    response = None
    rate_limited = False
    for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
        try:
            response = await _discord_request(
                "get",
                api,
                headers=headers,
            )
        except Exception as exc:
            logger.warning(
                "Discord API请求异常，本轮补发放弃: {}",
                exc,
            )
            return None, False

        if response.status_code != 429:
            break
        rate_limited = True
        if attempt == RATE_LIMIT_ATTEMPTS:
            break

        wait = _rate_limit_wait(response, RATE_LIMIT_FALLBACK)
        logger.warning(
            "Discord API限流(429)，{}s 后重试({}/{}): {}",
            wait,
            attempt,
            RATE_LIMIT_ATTEMPTS - 1,
            channel_id,
        )
        await asyncio.sleep(wait)

    if response.status_code != 200:
        if rate_limited:
            logger.warning(
                "Discord API限流重试耗尽，本轮补发放弃: {}",
                channel_id,
            )
        else:
            logger.warning(
                "Discord API错误: {} {}",
                response.status_code,
                response.text,
            )
        return None, False

    try:
        return response.json(), True
    except Exception as exc:
        logger.warning(
            "Discord API响应解析失败，本轮补发放弃: {}",
            exc,
        )
        return None, False


async def fetch_channel_messages_after(channel_id, after_id, limit=10):
    """抓取频道中 after_id 之后的消息，返回最旧的 limit 条（时间正序 旧→新）

    Discord API 单页上限 100 条且按 id 倒序返回，因此翻页到底收集全部
    缺口后取最旧 limit 条，保证补发从旧消息开始、不丢旧消息。

    防漏发安全规则（补发游标只按已发送消息推进，返回不完整列表会让
    游标越过更旧的缺口消息造成永久漏发，因此）：
    - 任一页请求异常/超时/非 200/429 重试耗尽/响应解析失败/翻页锚点缺失
      → 丢弃已收集的部分结果，返回 []，宁可下一轮重新补发
    - 收集量超过 FETCH_COLLECT_CAP 且未确认翻到缺口底部 → 返回 []，
      本轮不补发（极大缺口如确认放弃，可在面板"清除待补发"）
    - 仅当确认翻到缺口底部（短页/空页）才返回收集结果"""
    if limit <= 0:
        return []

    page_limit = 100

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    collected = []
    before = None

    while True:

        api = (
            f"https://discord.com/api/v10/"
            f"channels/{channel_id}/messages"
            f"?after={after_id}&limit={page_limit}"
        )

        if before:
            api += f"&before={before}"

        data, page_ok = await _fetch_discord_message_page(
            api,
            headers,
            channel_id,
        )
        if not page_ok:
            # 丢弃部分结果，避免游标越过更旧的缺口消息。
            return []

        if not isinstance(data, list) or not data:
            break

        page_message_ids = []
        for message in data:
            if not isinstance(message, dict):
                logger.warning(
                    "补发分页包含非对象消息，本轮补发放弃: {}",
                    channel_id,
                )
                return []
            message_id = _pagination_message_id(message.get("id"))
            if message_id is None:
                logger.warning(
                    "补发分页包含无效消息 ID，本轮补发放弃: {}",
                    channel_id,
                )
                return []
            page_message_ids.append(message_id)

        collected.extend(data)

        if len(data) < page_limit:
            # 短页 = 已翻到缺口底部，收集完整
            break

        if len(collected) > FETCH_COLLECT_CAP:
            # 缺口超出收集上限且底部未确认：无法保证不含更旧消息，
            # 本轮安全结束不补发，避免游标越过未抓取的旧消息
            logger.warning(
                "补发抓取: 频道{}缺口超过收集上限({}条)，本轮不补发"
                "（如确认放弃缺口可在面板清除待补发）",
                channel_id,
                FETCH_COLLECT_CAP,
            )
            return []

        # 本页取满，翻页取更早的（仍晚于 after_id）消息
        before = page_message_ids[-1]

        # 页间小延迟：大缺口连续请求过快容易触发 429
        await asyncio.sleep(FETCH_PAGE_INTERVAL)

    if not collected:
        return []

    # 全部缺口（新→旧），反转成旧→新后取最旧 limit 条
    messages = [
        normalize_api_message(msg)
        for msg in collected
    ]
    messages.reverse()

    return messages[:limit]


async def fetch_channel_latest(channel_id):
    """返回频道最新一条消息 ID（REST，走代理）；失败返回 None"""
    api = (
        f"https://discord.com/api/v10/"
        f"channels/{channel_id}/messages?limit=1"
    )

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    response = await _discord_get_with_retry(api, headers)
    if response is None:
        return None

    if response.status_code != 200:
        logger.warning(
            "Discord API错误: {} {}",
            response.status_code,
            response.text
        )
        return None

    data = response.json()

    if not isinstance(data, list) or not data:
        return None

    return str(data[0].get("id") or "")


async def fetch_channel_metadata(channel_id):
    """REST 获取频道名称与服务器 ID；失败返回 None"""
    api = f"https://discord.com/api/v10/channels/{channel_id}"

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    response = await _discord_get_with_retry(api, headers)
    if response is None:
        return None

    if response.status_code != 200:
        logger.warning(
            "Discord API错误: {} {}",
            response.status_code,
            response.text
        )
        return None

    try:
        data = response.json()
    except ValueError as exc:
        logger.warning(
            "Discord 频道元数据响应 JSON 无效，使用频道 ID 作为显示名: {}",
            exc,
        )
        return None

    if not isinstance(data, dict):
        return None

    name = data.get("name")
    guild_id = data.get("guild_id")

    return {
        "name": str(name) if name else None,
        "guild_id": str(guild_id) if guild_id else None,
    }


async def fetch_channel_name(channel_id):
    """REST 获取频道名称；失败返回 None"""
    metadata = await fetch_channel_metadata(channel_id)
    return metadata.get("name") if metadata else None


async def fetch_channel_gap_count(channel_id, after_id, cap=1000):
    """统计频道中 after_id 之后的消息条数（最多统计 cap 条，
    超过时返回 cap 表示"至少 cap 条"）；失败返回 None"""
    page_limit = 100

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    total = 0
    before = None

    while True:

        api = (
            f"https://discord.com/api/v10/"
            f"channels/{channel_id}/messages"
            f"?after={after_id}&limit={page_limit}"
        )

        if before:
            api += f"&before={before}"

        try:
            response = await _discord_request(
                "get",
                api,
                headers=headers,
            )
        except Exception as exc:
            logger.warning(
                "Discord API请求异常: {}",
                exc
            )
            return None

        if response.status_code != 200:
            logger.warning(
                "Discord API错误: {} {}",
                response.status_code,
                response.text
            )
            return None

        data = response.json()

        if not isinstance(data, list) or not data:
            break

        total += len(data)

        if total >= cap:
            return cap

        if len(data) < page_limit:
            break

        before = str(data[-1].get("id") or "")

        if not before:
            break

    return total


# =====================================================
# 事件对象归一（实时转发路径）
# =====================================================

def normalize_event(event, channel_name):
    """把 Discord 事件对象转成与 fetch_message 相同的规范化 dict"""
    author = getattr(event, "author", None)
    if author:
        # 与 fetch_message 保持一致：优先 global_name（昵称），缺失时回退 username
        username = (
            getattr(author, "global_name", None)
            or author.username
            or "unknown"
        )
    else:
        username = "unknown"

    embeds = []
    for embed in getattr(event, "embeds", []):
        image = getattr(embed, "image", None)
        embeds.append({
            "title": getattr(embed, "title", None),
            "url": getattr(embed, "url", None),
            "image_url": (
                getattr(image, "url", None)
                if image
                else None
            ),
        })

    attachments = []
    for file in getattr(event, "attachments", []):
        url = getattr(file, "url", None)
        if not url:
            continue
        attachments.append({
            "url": url,
            "filename": getattr(file, "filename", ""),
        })

    # 事件只携带被引用消息的 ID，无内联原文；
    # 由 build_parts 阶段按 ID 回退抓取一次补全
    ref = getattr(event, "message_reference", None)
    reference = None
    if (
        ref is not None
        and type(ref).__name__ != "_UNSET"
        and _discord_id(getattr(ref, "message_id", None))
    ):
        reference = {
            "message_id": _discord_id(getattr(ref, "message_id", None)),
            "channel_id": _discord_id(getattr(ref, "channel_id", None)),
            "username": "",
            "content": "",
        }

    return {
        "username": username,
        "channel_id": str(event.channel_id),
        "channel_name": channel_name,
        "created_at": getattr(event, "timestamp", None),
        "content": getattr(event, "content", "") or "",
        "embeds": embeds,
        "attachments": attachments,
        "mention_names": _mention_name_map(getattr(event, "mentions", [])),
        "reference": reference,
    }


# =====================================================
# 频道显示名解析（带进程内缓存）
#
# 实时转发/补发/私聊链接共用同一兜底：配置里没填名字时
# 查一次 Discord API 真实名并缓存，避免高频路径每条消息
# 都请求一次 Discord。
# =====================================================

_channel_metadata_cache = OrderedDict()
CHANNEL_METADATA_CONCURRENCY = 5
CHANNEL_METADATA_CACHE_LIMIT = 2048
CHANNEL_METADATA_FAILURE_TTL = 30.0
CHANNEL_METADATA_FAILURE_CACHE_LIMIT = 512
_channel_metadata_semaphore = asyncio.Semaphore(CHANNEL_METADATA_CONCURRENCY)
_channel_metadata_tasks = {}
_channel_metadata_failures = {}
_metadata_stopping = False


async def _fetch_channel_metadata_bounded(channel_id):
    async with _channel_metadata_semaphore:
        metadata = await fetch_channel_metadata(channel_id)
    if metadata is None:
        _channel_metadata_failures[channel_id] = time.monotonic() + CHANNEL_METADATA_FAILURE_TTL
        while len(_channel_metadata_failures) > CHANNEL_METADATA_FAILURE_CACHE_LIMIT:
            _channel_metadata_failures.pop(next(iter(_channel_metadata_failures)))
    else:
        _channel_metadata_cache[channel_id] = metadata
        _channel_metadata_cache.move_to_end(channel_id)
        while len(_channel_metadata_cache) > CHANNEL_METADATA_CACHE_LIMIT:
            _channel_metadata_cache.popitem(last=False)
        _channel_metadata_failures.pop(channel_id, None)
    return metadata


def _forget_channel_metadata_task(channel_id, task):
    if _channel_metadata_tasks.get(channel_id) is task:
        _channel_metadata_tasks.pop(channel_id, None)
    if not task.cancelled():
        # Consume failures even if every caller was cancelled while the shared
        # request was in flight; awaiting callers still receive the exception.
        task.exception()


async def _resolve_channel_metadata(channel_id):
    """查 Discord 频道元数据，进程内缓存；失败返回 None"""
    if _metadata_stopping:
        return None
    if channel_id in _channel_metadata_cache:
        _channel_metadata_cache.move_to_end(channel_id)
        return _channel_metadata_cache[channel_id]
    failure_until = _channel_metadata_failures.get(channel_id)
    if failure_until is not None:
        if time.monotonic() < failure_until:
            return None
        _channel_metadata_failures.pop(channel_id, None)

    task = _channel_metadata_tasks.get(channel_id)
    if task is None or task.done():
        task = asyncio.create_task(_fetch_channel_metadata_bounded(channel_id))
        _channel_metadata_tasks[channel_id] = task
        task.add_done_callback(
            lambda completed, key=channel_id: _forget_channel_metadata_task(key, completed)
        )
    return await asyncio.shield(task)


async def _resolve_channel_name(channel_id):
    """查 Discord 真实频道名，进程内缓存；失败返回 None（调用方回退 ID）"""
    metadata = await _resolve_channel_metadata(channel_id)
    return metadata.get("name") if metadata else None


# =====================================================
# 回复引用行（Discord 回复 → "↩ 用户名：被回复内容"）
#
# 实时转发 / 启动补发 / 私聊链接共用 build_parts 渲染：
#   - REST 路径由 normalize_api_message 优先读内联 referenced_message
#   - 事件路径只有 message_reference.message_id，回退抓取一次原文
# 抓取失败或无可引用正文时静默跳过引用行，不影响当前消息转发。
# =====================================================

async def _fetch_reference_message(channel_id, message_id):
    """按 ID 抓取被引用消息原文；失败返回 None（引用行 best-effort）"""
    if not channel_id or not message_id:
        return None

    api = (
        f"https://discord.com/api/v10/"
        f"channels/{channel_id}/messages/{message_id}"
    )

    headers = {
        "Authorization":
            f"Bot {get_discord_token()}"
    }

    try:
        response = await _discord_request(
            "get",
            api,
            headers=headers,
        )
    except Exception:
        return None

    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except Exception:
        return None

    return data if isinstance(data, dict) else None


async def _resolve_reference(reference, fallback_channel_id):
    """补全引用原文供渲染；只有 ID 时回退抓取一次。

    返回带 username/content 的 reference；无可引用正文返回 None。
    抓取失败静默返回 None，不影响当前消息转发。"""
    if not reference:
        return None

    if (reference.get("content") or "").strip():
        return reference

    if reference.get("username"):
        # 有作者但无正文（如图片回复），没有可引用的文字
        return None

    message_id = reference.get("message_id")
    channel_id = reference.get("channel_id") or fallback_channel_id

    if not message_id or not channel_id:
        return None

    raw = await _fetch_reference_message(
        channel_id,
        message_id,
    )

    if not raw:
        return None

    author = raw.get("author")
    if not isinstance(author, dict):
        author = {}

    reference["username"] = _reference_author_name(author)
    raw_content = raw.get("content")
    reference["content"] = raw_content if isinstance(raw_content, str) else ""
    reference["mention_names"] = _mention_name_map(raw.get("mentions"))

    if not (reference.get("content") or "").strip():
        return None

    return reference


def _render_reference(reference):
    """引用行文本：↩ 用户名：被回复内容（正文截断、压平换行）"""
    content = (reference.get("content") or "").strip()

    if not content:
        return None

    username = reference.get("username")
    if not isinstance(username, str) or not username.strip():
        username = "unknown"

    content = convert_mentions(content)
    content, _ = parse_discord_emoji(content)
    content = convert_mention_ids(content, reference.get("mention_names"))
    content = cleanup_markdown(content)
    content = re.sub(r"\s+", " ", content).strip()

    if not content:
        return None

    if len(content) > REFERENCE_TEXT_LIMIT:
        content = content[:REFERENCE_TEXT_LIMIT] + "…"

    return "↩︎ " + username + "：" + content


async def _append_text_and_emoji(
    raw_content,
    mention_names,
    *,
    text_enabled,
    emoji_enabled,
    text_parts,
):
    """Render message text and custom emoji in their original order."""
    if not raw_content or not (text_enabled or emoji_enabled):
        return False

    content = convert_mentions(raw_content)
    content_parts = parse_discord_emoji_parts(content)
    emoji_urls = [
        value for kind, value in content_parts
        if kind == "emoji" and emoji_enabled
    ]
    emoji_bytes_map = (
        await fetch_bytes_many(list(dict.fromkeys(emoji_urls)))
        if emoji_urls else {}
    )
    content_started = False
    has_content = False

    for kind, value in content_parts:
        if kind == "text":
            if not text_enabled:
                continue
            fragment = convert_mention_ids(value, mention_names)
            fragment = cleanup_markdown(fragment)
            if not fragment.strip():
                continue
            prefix = "\n" if not content_started else ""
            text_parts.append(make_text(prefix + fragment))
            content_started = True
            has_content = True
            continue

        if not emoji_enabled:
            continue
        emoji_bytes = emoji_bytes_map.get(value)
        if not content_started:
            text_parts.append(make_text("\n"))
            content_started = True
        if emoji_bytes:
            text_parts.append(
                QQMessageSegment.file_image(
                    emoji_bytes,
                    "emoji.gif" if value.endswith(".gif") else "emoji.png"
                )
            )
        else:
            text_parts.append(make_media_link("表情", value))
        has_content = True

    return has_content


async def _append_embeds(
    embeds,
    raw_content,
    *,
    text_enabled,
    link_enabled,
    image_enabled,
    text_parts,
    media_items,
):
    """Append enabled Embed titles, links, and images in source order."""
    has_content = False
    for embed in embeds or []:
        title = embed.get("title")
        url = embed.get("url")
        image_url = embed.get("image_url")

        if title and text_enabled:
            text_parts.append(
                make_text("\n\n【" + title + "】")
            )
            has_content = True

        # Only show the link when the body is empty or text is filtered out.
        if (
            url
            and link_enabled
            and (not raw_content.strip() or not text_enabled)
        ):
            text_parts.append(
                make_text("\n" + url)
            )
            has_content = True

        if image_url and image_enabled:
            image_bytes_map = await fetch_bytes_many([image_url])
            image_bytes = image_bytes_map.get(image_url)
            if image_bytes:
                media_items.append({
                    "kind": "图片",
                    "url": image_url,
                    "segment": QQMessageSegment.file_image(
                        image_bytes,
                        "embed.png"
                    ),
                })
            else:
                text_parts.append(
                    make_media_link("图片", image_url)
                )
            has_content = True

    return has_content


async def _append_attachment_media(jobs, kind, segment_factory, text_parts, media_items):
    """Download a group of attachments and append media or a link fallback."""
    if not jobs:
        return False

    media_bytes_map = await fetch_bytes_many([url for url, _ in jobs])
    for url, filename in jobs:
        media_bytes = media_bytes_map.get(url)
        if media_bytes:
            media_items.append({
                "kind": kind,
                "url": url,
                "segment": segment_factory(media_bytes, filename),
            })
        else:
            text_parts.append(make_media_link(kind, url))
    return True


def _collect_attachment_jobs(
    attachments,
    *,
    image_enabled,
    audio_enabled,
    file_enabled,
    text_parts,
):
    """Classify enabled attachments and append links for ordinary files."""
    image_jobs = []
    audio_jobs = []
    video_jobs = []
    has_file_content = False

    for attachment in attachments or []:
        url = attachment.get("url")
        filename = attachment.get("filename") or ""
        if not url:
            continue

        extension = os.path.splitext(filename.lower())[1]
        if extension in IMAGE_EXTS:
            if image_enabled:
                image_jobs.append((url, filename))
        elif extension in AUDIO_EXTS:
            if audio_enabled:
                audio_jobs.append((url, filename))
        elif extension in VIDEO_EXTS:
            # 视频沿用“文件”筛选项，兼容既有筛选配置。
            if file_enabled:
                video_jobs.append((url, filename))
        elif file_enabled:
            text_parts.append(make_file_link(filename, url))
            has_file_content = True

    return image_jobs, audio_jobs, video_jobs, has_file_content


async def _append_message_context(
    message,
    source_label,
    *,
    show_datetime,
    text_enabled,
    text_parts,
):
    """Append the message header and optional reply reference; return channel ID."""
    channel_name = message.get("channel_name") or ""
    channel_id = message.get("channel_id") or ""

    if not channel_name and channel_id:
        # 未配置显示名时兜底：查 Discord API 真实名（每频道缓存一次）
        channel_name = await _resolve_channel_name(channel_id)

    channel_name = channel_name or channel_id or "?"
    username = message.get("username") or "unknown"
    header = (
        f"{source_label}\n"
        f"Discord [#{channel_name}] 中 [{username}]的消息："
    )
    if show_datetime:
        message_time = format_message_datetime(message.get("created_at"))
        if message_time:
            header += "\n时间：" + message_time
    text_parts.append(make_text(header))

    if text_enabled:
        reference = await _resolve_reference(
            message.get("reference"),
            channel_id,
        )
        if reference:
            ref_text = _render_reference(reference)
            if ref_text:
                text_parts.append(make_text("\n" + ref_text))

    return channel_id


# =====================================================
# 规范化消息 → QQ 消息段（唯一一份渲染逻辑）
# =====================================================

async def build_parts(
    message,
    source_label="自动转发来自",
    enabled_types=None,
    show_datetime=False,
):
    """规范化消息 → (text_parts, media_items, has_content)

    - header（来源频道/作者）始终在 text_parts 首位
    - 回复引用行（↩ 用户名：被回复内容）位于 header 后、正文前；
      抓取失败时静默跳过，不进 has_content
    - 正文 + Emoji、embeds、附件统一渲染，媒体并发下载
    - 下载失败降级为文字 + 原链接
    - has_content 用于实时转发跳过空消息（仅 header 时）
    - source_label 区分消息来源（实时转发 / 启动补发）
    - enabled_types：消息类型筛选（plugins/filter.py 的类型名集合），
      None = 全部类型启用（默认，行为与旧版完全一致）。
      按消息内部各内容组件分别过滤：被禁用的组件不渲染；过滤后
      无任何内容时 has_content=False，调用方不发送（不发空消息）"""
    text_parts = []
    media_items = []

    def _type_enabled(kind):
        return enabled_types is None or kind in enabled_types

    text_enabled = _type_enabled(MESSAGE_TYPE_TEXT)
    channel_id = await _append_message_context(
        message,
        source_label,
        show_datetime=show_datetime,
        text_enabled=text_enabled,
        text_parts=text_parts,
    )

    has_content = False

    # ======================
    # 普通文字 + Emoji
    # ======================

    raw_content = message.get("content") or ""
    has_content = await _append_text_and_emoji(
        raw_content,
        message.get("mention_names"),
        text_enabled=text_enabled,
        emoji_enabled=_type_enabled(MESSAGE_TYPE_EMOJI),
        text_parts=text_parts,
    )

    # ======================
    # Embed
    # ======================

    if await _append_embeds(
        message.get("embeds"),
        raw_content,
        text_enabled=text_enabled,
        link_enabled=_type_enabled(MESSAGE_TYPE_LINK),
        image_enabled=_type_enabled(MESSAGE_TYPE_IMAGE),
        text_parts=text_parts,
        media_items=media_items,
    ):
        has_content = True

    # ======================
    # 附件
    # ======================

    image_jobs, audio_jobs, video_jobs, has_file_content = _collect_attachment_jobs(
        message.get("attachments"),
        image_enabled=_type_enabled(MESSAGE_TYPE_IMAGE),
        audio_enabled=_type_enabled(MESSAGE_TYPE_AUDIO),
        file_enabled=_type_enabled(MESSAGE_TYPE_FILE),
        text_parts=text_parts,
    )
    if has_file_content:
        has_content = True

    for jobs, kind, segment_factory in (
        (image_jobs, "图片", QQMessageSegment.file_image),
        (audio_jobs, "音频", QQMessageSegment.file_audio),
        (video_jobs, "视频", QQMessageSegment.file_video),
    ):
        if await _append_attachment_media(
            jobs,
            kind,
            segment_factory,
            text_parts,
            media_items,
        ):
            has_content = True

    return text_parts, media_items, has_content
