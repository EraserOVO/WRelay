import asyncio
import datetime
import math
import re
import time
from dataclasses import dataclass
from weakref import WeakValueDictionary

from nonebot import logger
from nonebot import on_message
from nonebot import on_notice
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    GroupAddRobotEvent,
    GroupMessageCreateEvent,
)

from plugins import identities
from plugins import qq_fwd
from plugins.config import (
    get_all_channels,
    get_active_user_openids,
    get_qq_group_entries,
    get_qq_c2c_relay_filter,
    get_bilibili_c2c_relay_filter,
    get_channel_translation_config,
    get_x_c2c_relay_filter,
    get_x_source_translation_config,
    is_user_authorized,
    USER_NOT_AUTHORIZED_TEXT,
)
from plugins.fetch import (
    is_discord_url,
    fetch_message,
    build_parts,
)
from plugins.sender import send_relay_message
from plugins.translation import translate_text_parts
from plugins.user_errors import localize_user_error
from plugins.filter import (
    DISCORD_MESSAGE_TYPES,
    X_MESSAGE_TYPES,
    BILIBILI_MESSAGE_TYPES,
    get_enabled_message_types,
)
from plugins.x_rss import (
    is_x_status_url,
    fetch_x_post,
    render_x_post,
)
from plugins.bilibili_parser import parse_manual_post_url
from plugins.bilibili import fetch_bilibili_manual_post, render_bilibili_post
from plugins.json_io import (
    load_json,
    update_json,
)
from plugins.paths import data_path
from plugins.c2c_consumed import mark_consumed
from plugins.qq_keyboard import send_titled_reply


relay_command = on_message(
    priority=14,
    block=False
)

_PRIVATE_LINK_RE = re.compile(r"https?://\S+", re.IGNORECASE)


# =====================================================
# group_openid / 用户 openid 自动发现
#
# 机器人被拉进群 / 收到群消息时，自动把群 openid 打印到控制台
# 并写入 data/qq_group_openids.json；收到私聊消息时，把用户
# openid 写入 data/qq_user_openids.json。面板同步后勾选放行，
# 未放行的用户触发 relay 会被拒绝。
# =====================================================

OPENID_FILE = data_path("qq_group_openids.json")
USER_OPENID_FILE = data_path("qq_user_openids.json")


def _qq_bot_id(bot):
    return str(getattr(bot, "self_id", "") or getattr(bot, "id", "") or "").strip()


def save_group_openid(openid, bot_id=""):
    openid = str(openid or "").strip()
    bot_id = str(bot_id or "").strip()
    if not openid:
        return

    def mutate(data):
        if not isinstance(data, dict):
            data = {}
        openids = data.setdefault("group_openids", [])
        if not isinstance(openids, list):
            openids = []
            data["group_openids"] = openids
        changed = False
        if openid not in openids:
            openids.append(openid)
            changed = True
        if bot_id:
            bindings = data.setdefault("group_bot_ids", {})
            if not isinstance(bindings, dict):
                bindings = {}
                data["group_bot_ids"] = bindings
            if bindings.get(openid) != bot_id:
                bindings[openid] = bot_id
                changed = True
        return changed

    update_json(OPENID_FILE, mutate, default={}, indent=4, write_if=bool)

    logger.info(
        "[QQ group_openid] {}（Bot {}）",
        openid,
        bot_id or "未知",
    )


def save_user_openid(openid):
    def mutate(data):
        if not isinstance(data, dict):
            data = {}
        openids = data.setdefault("user_openids", [])
        if openid in openids:
            return False
        openids.append(openid)
        return True

    update_json(USER_OPENID_FILE, mutate, default={}, indent=4, write_if=bool)

    logger.info(
        "[QQ user_openid] {}",
        openid
    )


def _record_user_openid_activity(openid, nickname, last_active):
    """Persist QQ user discovery and identity outside the event-loop thread."""
    save_user_openid(openid)
    try:
        identities.record_user_identity(
            openid,
            nickname=nickname,
            last_active=last_active,
        )
    except Exception as exc:
        logger.warning("QQ 用户身份资料写入失败，已跳过展示信息更新: {}", exc)


def _record_group_openid_activity(openid, bot_id, group_name, last_active):
    """Persist QQ group discovery and identity outside the event-loop thread."""
    save_group_openid(openid, bot_id)
    try:
        identities.record_group_identity(
            openid,
            group_name=group_name,
            last_active=last_active,
        )
    except Exception as exc:
        logger.warning("QQ 群身份资料写入失败，已跳过展示信息更新: {}", exc)


# =====================================================
# OpenID 身份记录
#
# 在自动发现 openid 的同时，把昵称/群名、最后活动时间记录到
# data/qq_identities.json（独立于白名单的身份资料库）。名称只
# 用于面板展示识别，绝不参与放行判断（qq_user_openids 仍是唯一
# 权限来源）。新发现的用户只记录身份，不会自动加入白名单。
#
# 昵称取事件自带（FriendAuthor/GroupMemberAuthor.username），
# 缺失时保持「未命名」，由面板兜底显示并允许手动备注。
# QQ 官方 API 不提供群名/昵称查询接口，无法主动补全。
# =====================================================

def _event_ts(event):
    """取有限且合理的事件时间（秒）；异常值回退到当前时间。"""
    now = time.time()
    ts = getattr(event, "timestamp", None)
    try:
        if isinstance(ts, datetime.datetime):
            value = ts.timestamp()
        elif isinstance(ts, (int, float)) and not isinstance(ts, bool):
            value = float(ts)
        elif isinstance(ts, str):
            value = float(ts)
        else:
            return now
        if value > 1e12:
            value /= 1000.0
        if not math.isfinite(value) or value <= 0 or value > now + 300:
            return now
        return value
    except (OverflowError, OSError, TypeError, ValueError):
        return now


openid_logger = on_message(
    priority=1,
    block=False
)


@openid_logger.handle()
async def log_group_message_openid(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):
        return

    if isinstance(
        event,
        C2CMessageCreateEvent
    ):

        openid = event.author.user_openid

        # 记录身份：昵称取事件自带，缺失时保持未命名
        username = str(
            getattr(
                event.author,
                "username",
                ""
            ) or ""
        ).strip()

        await asyncio.to_thread(
            _record_user_openid_activity,
            openid,
            username or None,
            _event_ts(event),
        )

        return

    if not isinstance(
        event,
        GroupMessageCreateEvent
    ):
        return

    await asyncio.to_thread(
        _record_group_openid_activity,
        event.group_openid,
        _qq_bot_id(bot),
        None,
        _event_ts(event),
    )


group_notice = on_notice(
    priority=1,
    block=False
)


@group_notice.handle()
async def log_group_add_openid(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):
        return

    if not isinstance(
        event,
        GroupAddRobotEvent
    ):
        return

    await asyncio.to_thread(
        _record_group_openid_activity,
        event.group_openid,
        _qq_bot_id(bot),
        None,
        _event_ts(event),
    )


# =====================================================
# 私聊 relay 命令（引用直发）
#
#   /转发 列表 / /私聊转发   列出全部已注册且 enabled 的群（纯文本，无按钮）
#   /转发 1 3               把引用消息转发到指定群（可多个）
#   /转发 全部              转发到全部可用群
#
# 优先使用 QQ 自带「回复」引用的消息；未引用时回退到最近一条普通私聊
# （600 秒内）。私聊专属：链接仍分别走 Discord / X / Bilibili 的
# 单条消息抓取与渲染机制；命令入口统一使用已授权 QQ 用户白名单。
#
# 与 manage.py（priority=20）的关系：本 matcher priority=14 先执行；relay
# 指令会 mark_consumed / stop_propagation，避免 manage.py 再把同一消息当
# 私聊命令回复。授权用户的普通文字只写入最近内容缓存、不消费消息，仍由
# manage.py 回复「未知命令」提示；含链接的内容同样缓存，但会消费事件，
# 不再回复提示。未授权用户不记录内容，由 manage.py 回复准入提示。
# =====================================================

_c2c_locks = WeakValueDictionary()

# 最近一条可转发内容：user_openid -> {"ts", "event", "message", "qq_message"}
_c2c_last_content = {}

CONTENT_MEMORY_SECONDS = 600


def _get_c2c_lock(user_openid):
    lock = _c2c_locks.get(user_openid)
    if lock is None:
        lock = asyncio.Lock()
        _c2c_locks[user_openid] = lock
    return lock


def _stop_propagation():
    """阻止当前事件继续传播到更低优先级 matcher（防止 manage.py 双回复）。

    测试直接调用 handler 时无 current_matcher 上下文，静默忽略。"""
    try:
        from nonebot.internal.matcher import current_matcher
        current_matcher.get().stop_propagation()
    except Exception:
        pass


def _c2c_enabled_groups():
    """私聊可选目标：全部已注册且 enabled 的群（不受测试/正式隔离）"""
    return [
        e for e in get_qq_group_entries()
        if e.get("enabled") and e.get("openid")
    ]


def _c2c_group_label(group):
    """群显示名：优先名称，其次备注，最后 openid。"""
    return (
        str(group.get("name") or group.get("remark") or "").strip()
        or str(group.get("openid") or "")
    )


def remember_c2c_content(user_openid, event, message, qq_message):
    """记住最近一条可转发内容，供未引用消息时回退。"""
    now = time.monotonic()
    if len(_c2c_last_content) >= 256:
        cutoff = now - CONTENT_MEMORY_SECONDS
        for key, record in list(_c2c_last_content.items()):
            if record.get("ts", 0) < cutoff:
                _c2c_last_content.pop(key, None)
    _c2c_last_content[user_openid] = {
        "ts": now,
        "event": event,
        "message": message,
        "qq_message": qq_message,
    }


def get_c2c_content(user_openid, now_ts=None):
    record = _c2c_last_content.get(user_openid)
    if not record:
        return None
    now = now_ts if now_ts is not None else time.monotonic()
    if now - record["ts"] > CONTENT_MEMORY_SECONDS:
        _c2c_last_content.pop(user_openid, None)
        return None
    return record


def _parse_c2c_relay_indexes(argument, group_count):
    """Parse private relay indexes as bounded ASCII decimal integers."""
    tokens = str(argument or "").split()
    if not tokens:
        return None

    max_digits = len(str(max(0, int(group_count))))
    indexes = []
    for token in tokens:
        if not token or any(char < "0" or char > "9" for char in token):
            return None
        normalized = token.lstrip("0") or "0"
        if len(normalized) > max_digits:
            return None
        index = int(normalized)
        if index < 1 or index > group_count:
            return None
        indexes.append(index)
    return indexes


@relay_command.handle()
async def handle(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):

        return

    if not isinstance(
        event,
        C2CMessageCreateEvent
    ):

        return

    qq_message = event.get_message()
    message = event.get_plaintext()
    user = event.author.user_openid

    async with _get_c2c_lock(user):
        stripped = message.strip()

        # ---- 私聊转发命令 ----
        if (
            stripped == "/转发"
            or stripped.startswith("/转发 ")
            or stripped.startswith("/私聊转发")
        ):
            mark_consumed(event)
            await _handle_relay_cmd(bot, event, stripped, user)
            return

        # 普通斜杠命令交给各自处理器，不作为转发内容。
        if stripped.startswith("/"):
            return

        if (
            not message
            and not qq_message["emoji"]
            and not (getattr(event, "attachments", None) or [])
        ):
            return

        # 未授权用户不记录内容，保留 manage.py 的准入提示。
        if not await asyncio.to_thread(is_user_authorized, user):
            return

        remember_c2c_content(user, event, message, qq_message)

        # 链接通常就是待转发内容；缓存后不再让 manage.py 回复「未知命令」。
        # 普通文字保持原有提示，未授权用户仍在上面提前返回。
        if _PRIVATE_LINK_RE.search(message):
            mark_consumed(event)


async def _send_c2c_list(bot, event):
    """发送纯文本目标群名单与转发方法。"""
    groups = await asyncio.to_thread(_c2c_enabled_groups)
    targets = [
        {"openid": str(group.get("openid")), "name": _c2c_group_label(group)}
        for group in groups
    ]
    await send_titled_reply(
        bot,
        event,
        "转发群列表",
        qq_fwd.build_relay_list(targets, allow_all=True),
    )


async def _handle_relay_cmd(bot, event, stripped, user):

    # 白名单校验：私聊转发仅允许管理面板中勾选放行的用户使用
    active_users = await asyncio.to_thread(get_active_user_openids)
    if user not in active_users:

        logger.info(
            "拒绝未授权用户转发: {}",
            user
        )

        await send_titled_reply(
            bot,
            event,
            "准入提示",
            USER_NOT_AUTHORIZED_TEXT,
        )

        _stop_propagation()
        return

    # /转发 列表、裸 /转发 与「私聊转发」入口显示同一份纯文本名单。
    if (
        stripped in ("/转发", "/转发 列表", "/私聊转发", "/转发 私聊")
        or stripped.startswith("/转发 列表 ")
        or stripped.startswith("/私聊转发 ")
    ):
        await _send_c2c_list(bot, event)
        _stop_propagation()
        return

    arg = stripped[len("/转发"):].strip() if stripped.startswith("/转发 ") else ""

    groups = await asyncio.to_thread(_c2c_enabled_groups)
    if not groups:
        await send_titled_reply(
            bot,
            event,
            "转发结果",
            "当前没有可用的QQ接收群（请在管理面板勾选）",
        )
        _stop_propagation()
        return

    # /转发 全部：直接发往全部可用群。
    if arg == "全部":
        targets = [str(group.get("openid")) for group in groups]
        await _forward_quoted_or_recent(bot, event, targets, user)
        _stop_propagation()
        return

    # /转发 {序号1} {序号2} ...：按名单序号直接转发。
    indexes = _parse_c2c_relay_indexes(arg, len(groups))
    if indexes is None:
        tokens = arg.split()
        if tokens and all(token.isascii() and token.isdecimal() for token in tokens):
            reply = "无法找到对应的转发群"
        else:
            reply = qq_fwd.USAGE_TEXT
        await send_titled_reply(
            bot,
            event,
            "转发结果",
            reply,
        )
        _stop_propagation()
        return

    targets = []
    seen = set()
    for index in indexes:
        openid = str(groups[index - 1].get("openid"))
        if openid in seen:
            continue
        seen.add(openid)
        targets.append(openid)

    await _forward_quoted_or_recent(bot, event, targets, user)
    _stop_propagation()


async def _forward_quoted_or_recent(bot, event, targets, user):
    """优先转发引用消息，未引用时回退最近一条普通私聊内容。"""
    quote = qq_fwd.extract_quoted_message(event)
    error = qq_fwd.quoted_message_error(quote, bot)
    if error:
        await send_titled_reply(bot, event, "转发结果", error)
        return

    content_event = event
    qq_message = None
    if quote is not None:
        message = {
            "content": quote["content"],
            "attachments": quote["attachments"],
        }
    else:
        record = get_c2c_content(user)
        if record is None:
            await send_titled_reply(
                bot, event, "转发结果", qq_fwd.NO_CONTENT_TEXT
            )
            return
        message = qq_fwd.normalize_event_message(record["event"])
        if not message["content"].strip() and not message["attachments"]:
            await send_titled_reply(
                bot, event, "转发结果", qq_fwd.NO_CONTENT_TEXT
            )
            return
        content_event = record["event"]
        qq_message = record.get("qq_message")

    await _consume_next(
        bot,
        event,
        message,
        targets,
        user,
        qq_message=qq_message,
        content_event=content_event,
    )


@dataclass
class _ManualRelayContent:
    text_parts: list
    media_items: list
    translation_key: tuple | None
    translation_config: dict | None = None


class _ManualRelayContentError(Exception):
    """An expected fetch or filtering failure with a user-facing message."""


async def _prepare_discord_relay_content(message):
    logger.info("检测到Discord消息链接")
    msg, fetch_err = await fetch_message(message)
    if msg is None:
        raise _ManualRelayContentError(
            "获取Discord消息失败"
            + (f"（{localize_user_error(fetch_err)}）" if fetch_err else "")
        )

    # 频道名：面板配置名优先，未配置时由 build_parts 兜底查真实名。
    all_channels = await asyncio.to_thread(get_all_channels)
    msg["channel_name"] = all_channels.get(msg["channel_id"])
    relay_filter = await asyncio.to_thread(get_qq_c2c_relay_filter)
    translation_config = await asyncio.to_thread(
        get_channel_translation_config, msg["channel_id"], manual=True
    )
    text_parts, media_items, has_content = await build_parts(
        msg,
        source_label="手动转发来自",
        enabled_types=get_enabled_message_types(
            relay_filter, DISCORD_MESSAGE_TYPES
        ),
        show_datetime=translation_config.get("show_datetime") is True,
    )
    if not has_content:
        raise _ManualRelayContentError("消息被筛选，未转发")

    translation_key = (
        "discord",
        str(msg.get("channel_id") or ""),
        str(msg.get("message_id") or ""),
    )
    return _ManualRelayContent(
        text_parts, media_items, translation_key, translation_config
    )


async def _prepare_x_relay_content(message):
    logger.info("检测到 X 消息链接")
    post, source_name, fetch_err = await fetch_x_post(message)
    if post is None:
        raise _ManualRelayContentError(
            "获取X消息失败"
            + (f"（{localize_user_error(fetch_err)}）" if fetch_err else "")
        )

    x_filter = await asyncio.to_thread(get_x_c2c_relay_filter)
    x_match = is_x_status_url(message)
    translation_config = await asyncio.to_thread(
        get_x_source_translation_config,
        x_match.group(1) if x_match else source_name,
        manual=True,
    )
    rendered = await render_x_post(
        post,
        source_name,
        enabled_types=get_enabled_message_types(x_filter, X_MESSAGE_TYPES),
        show_datetime=translation_config.get("show_datetime") is True,
    )
    if rendered is None:
        raise _ManualRelayContentError("消息被筛选，未转发")

    text_parts, media_items = rendered
    translation_key = ("x_manual", str(post.get("id") or ""))
    return _ManualRelayContent(
        text_parts, media_items, translation_key, translation_config
    )


async def _prepare_bilibili_relay_content(message):
    logger.info("检测到 Bilibili 投稿链接")
    post, source_name, fetch_err = await fetch_bilibili_manual_post(message)
    if post is None:
        raise _ManualRelayContentError(
            "获取Bilibili投稿失败"
            + (f"（{localize_user_error(fetch_err)}）" if fetch_err else "")
        )

    bilibili_filter = await asyncio.to_thread(get_bilibili_c2c_relay_filter)
    rendered = await render_bilibili_post(
        post,
        source_name,
        enabled_types=get_enabled_message_types(
            bilibili_filter, BILIBILI_MESSAGE_TYPES
        ),
    )
    if rendered is None:
        raise _ManualRelayContentError("消息被筛选，未转发")

    text_parts, media_items = rendered
    translation_key = ("bilibili_manual", str(post.get("id") or ""))
    return _ManualRelayContent(text_parts, media_items, translation_key)


async def _prepare_manual_relay_content(
    message,
    qq_message=None,
    content_event=None,
):
    text = str((message or {}).get("content") or "").strip()
    if is_discord_url(text):
        return await _prepare_discord_relay_content(text)
    if is_x_status_url(text):
        return await _prepare_x_relay_content(text)
    if parse_manual_post_url(text):
        return await _prepare_bilibili_relay_content(text)

    text_parts, media_items = await qq_fwd.build_forward_content(message or {})

    if qq_message is not None and qq_message["emoji"]:
        # QQ 表情不在 get_plaintext() 中；优先保留原有文本和表情段。
        emoji_parts = [
            segment.copy()
            for segment in qq_message
            if segment.type in ("text", "emoji")
        ]
        if emoji_parts:
            text_parts = emoji_parts

    event_id = str(getattr(content_event, "id", "") or "")
    translation_key = ("qq_c2c", event_id) if event_id else None
    return _ManualRelayContent(text_parts, media_items, translation_key)


async def _consume_next(
    bot,
    event,
    message,
    targets,
    user,
    qq_message=None,
    content_event=None,
):

    # 准入复查：授权状态可能在引用内容后被撤销，未获准不再转发。
    if not await asyncio.to_thread(is_user_authorized, user):
        await send_titled_reply(
            bot,
            event,
            "准入提示",
            USER_NOT_AUTHORIZED_TEXT,
        )
        _stop_propagation()
        return

    groups = [str(group) for group in targets or [] if group]
    if not groups:

        await send_titled_reply(
            bot,
            event,
            "转发结果",
            "无法找到对应的转发群",
        )

        _stop_propagation()
        return

    try:
        content = await _prepare_manual_relay_content(
            message,
            qq_message=qq_message,
            content_event=content_event,
        )
    except _ManualRelayContentError as exc:
        await send_titled_reply(bot, event, "转发结果", str(exc))
        _stop_propagation()
        return

    text = str((message or {}).get("content") or "").strip()
    text_parts = await translate_text_parts(
        content.text_parts,
        content.translation_key,
        skip_leading_parts=1 if (
            is_discord_url(text)
            or is_x_status_url(text)
            or parse_manual_post_url(text)
        ) else 0,
        config=content.translation_config,
    )

    ok_map = await send_relay_message(
        text_parts,
        content.media_items,
        groups=groups,
    )

    if ok_map and all(ok_map.values()):
        result = "转发成功"
    else:
        result = "转发失败"

    await send_titled_reply(
        bot,
        event,
        "转发结果",
        result,
    )
    _stop_propagation()
