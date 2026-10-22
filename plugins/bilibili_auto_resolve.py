"""Bilibili 自动解析（群内链接）。

已登记且启用的 QQ 群里，成员发送 Bilibili 投稿视频链接（含 b23.tv /
bili2233.cn 短链）时，机器人下载视频本体并以被动回复发回同一个群；
下载失败、超过媒体大小上限或视频不是单段时静默，不回复原链接。

直播、番剧、专栏、动态和其他站点链接一律静默。QQ 小程序分享卡片
依赖适配器保留的 ark_data，本模块暂不处理。
"""

import asyncio
import re
import time
from urllib.parse import urlparse

from nonebot import logger
from nonebot import on_message
from nonebot.adapters import Bot, Event
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import Message, MessageSegment as QQMessageSegment
from nonebot.adapters.qq.event import GroupMessageCreateEvent

from plugins import bilibili
from plugins.config import (
    get_active_group_routing_snapshot,
    get_bilibili_auto_resolve_enabled,
)


# 同一群内同一视频（同一分 P）的发送窗口，避免刷屏和重复下载。
AUTO_RESOLVE_COOLDOWN = 600

_URL_RE = re.compile(r"https?://[^\s<>\"'，。；]+", re.I)
_SHORT_HOSTS = {"b23.tv", "bili2233.cn"}

# 锁保护 _sent_at（10 分钟窗口）与 _inflight（在途去重）。
_state_lock = asyncio.Lock()
_sent_at = {}
_inflight = set()


def _bilibili_url(value):
    """返回受支持 Bilibili 域名的完整链接，否则 None。"""
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host in _SHORT_HOSTS or host == "bilibili.com" or host.endswith(".bilibili.com"):
        return raw
    return None


def extract_first_video_url(text):
    """取消息纯文本中第一个受支持的 Bilibili 链接；没有则 None。

    这里只做域名粗筛，直播/番剧/专栏等不支持的页面由后续解析判定为
    静默，不会误发提示。
    """
    for match in _URL_RE.finditer(str(text or "")):
        url = _bilibili_url(match.group(0))
        if url:
            return url
    return None


def _video_key(group_openid, video_ref, fallback_url):
    bvid = str(video_ref.get("bvid") or "").strip()
    avid = str(video_ref.get("avid") or "").strip()
    identity = bvid or ("av" + avid if avid else "")
    if not identity:
        identity = str(fallback_url or "").strip()
    return (str(group_openid), identity, int(video_ref.get("page") or 1))


async def _claim(key):
    """占用去重槽位；返回 False 表示已在途或仍在发送窗口内。"""
    async with _state_lock:
        now = time.time()
        for existing in [k for k, ts in _sent_at.items() if now - ts >= AUTO_RESOLVE_COOLDOWN]:
            _sent_at.pop(existing, None)
        if key in _inflight or now - _sent_at.get(key, 0) < AUTO_RESOLVE_COOLDOWN:
            return False
        _inflight.add(key)
        return True


async def _finish(key, sent):
    async with _state_lock:
        _inflight.discard(key)
        if sent:
            _sent_at[key] = time.time()


def _group_allowed(group_openid, bot_id):
    """群是否已登记、启用且属于当前 Bot。"""
    groups, bot_ids = get_active_group_routing_snapshot()
    if group_openid not in groups:
        return False
    binding = str(bot_ids.get(group_openid) or "")
    return not binding or not bot_id or binding == bot_id


def _read_gate(group_openid, bot_id):
    return get_bilibili_auto_resolve_enabled() and _group_allowed(group_openid, bot_id)


def _qq_bot_id(bot):
    return str(getattr(bot, "self_id", "") or getattr(bot, "id", "") or "").strip()


def _mentions_bot(bot, event, segments):
    is_tome = getattr(event, "is_tome", None)
    try:
        if bool(is_tome() if callable(is_tome) else is_tome):
            return True
    except Exception:
        pass

    bot_id = _qq_bot_id(bot)
    mention_types = ("mention_user", "at", "mention", "at_user", "to_me")
    for segment in segments:
        segment_type = str(getattr(segment, "type", None) or "").lower()
        if segment_type == "to_me":
            return True
        if segment_type not in mention_types:
            continue
        data = getattr(segment, "data", None)
        if not isinstance(data, dict):
            continue
        if data.get("is_bot") is True:
            return True
        mention_id = str(
            data.get("user_id") or data.get("qq") or data.get("target")
            or data.get("openid") or data.get("id") or ""
        ).strip()
        if bot_id and mention_id == bot_id:
            return True
    return False


async def _reply(bot, event, segments):
    await bot.send(event, Message(segments))


async def _resolve_and_send(bot, event, url):
    """解析第一个受支持的视频链接并回复；不支持的链接或无法获取的视频静默。"""
    try:
        ref, error = await bilibili._resolve_manual_ref(url)
    except Exception as exc:
        logger.info("Bilibili 短链接解析失败，静默跳过: {}", exc)
        return
    if error or not isinstance(ref, dict) or ref.get("kind") != "视频":
        return

    video_ref = ref.get("video_ref") or {}
    key = _video_key(event.group_openid, video_ref, url)
    if not await _claim(key):
        logger.debug("Bilibili 群链接去重命中: {}", key)
        return

    sent = False
    try:
        sent = await _fetch_and_reply(bot, event, video_ref)
    finally:
        await _finish(key, sent)


async def _fetch_and_reply(bot, event, video_ref):
    """下载视频并回复文件；失败、超限或多段时静默。返回是否已发送消息。"""
    try:
        post, _author = await bilibili._fetch_video_manual_post(video_ref)
        _video_url, video_data = await bilibili._fetch_video_url(post)
        if not video_data:
            logger.info("Bilibili 群链接未能取得视频，静默跳过: {}", video_ref)
            return False
        await _reply(
            bot,
            event,
            [QQMessageSegment.file_video(video_data, "bilibili-%s.mp4" % post["id"])],
        )
        return True
    except Exception as exc:
        logger.warning("Bilibili 群链接解析失败，静默跳过: {}", exc)
        return False


bilibili_auto_resolve = on_message(
    priority=30,
    block=False
)


@bilibili_auto_resolve.handle()
async def handle_bilibili_auto_resolve(
    bot: Bot,
    event: Event
):
    if not isinstance(bot, QQBot):
        return

    if not isinstance(event, GroupMessageCreateEvent):
        return

    if getattr(event.author, "bot", False):
        return

    message = event.get_plaintext().strip()
    if not message or message.startswith("/"):
        return

    if _mentions_bot(bot, event, list(event.get_message())):
        # 交给 plugins.manage 的群 @ 提示，避免同一消息两次回复。
        return

    url = extract_first_video_url(message)
    if not url:
        return

    allowed = await asyncio.to_thread(_read_gate, event.group_openid, _qq_bot_id(bot))
    if not allowed:
        return

    logger.info("收到群聊 Bilibili 链接: group={} url={}", event.group_openid, url)
    await _resolve_and_send(bot, event, url)
