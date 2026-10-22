# -*- coding: utf-8 -*-
"""QQ→QQ 手动转发。

任何 QQ 用户在一个已注册的 QQ 接收群里：

  /转发 列表      列出当前可作为转发目标的、已注册且 enabled 的 QQ 群（带序号）
  /转发 1 3       引用一条消息后发送，直接转发到第 1、3 个目标群

引用消息优先；没有引用时使用该用户最近一条普通消息（600 秒内）。
发送前会再次校验目标群（仍注册 / enabled / 与当前群仍属同一已启用 QQ 转发组 / 排除当前群），
配置变化导致失效时回复「转发失败」。

实现要点：
  - 优先读取 QQ 引用消息（event.reply / msg_elements），保留文字与附件；机器人消息和
    命令消息不会被当作转发内容。
  - 候选目标群 = 与当前群至少共同属于一个 QQ→QQ 转发组（qq_forwarding_groups）的其他已注册(enabled)群，
    跨多个转发组合并去重；当前群不属于任何转发组时候选为空。与测试组无关。
  - 发送复用 plugins/sender（分片/重试/限流退避/媒体降级）与 plugins/media（图片/音视频字节上传+链接降级）。
  - 不依赖白名单；QQ→QQ 转发组只在本功能内使用，不读取 Discord/X 转发组。
"""
import asyncio
import sys
import time
from weakref import WeakValueDictionary

import httpx

from nonebot import logger
from nonebot import on_message
from nonebot import get_driver
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment
from nonebot.adapters.qq.event import GroupMessageCreateEvent

from plugins.config import (
    get_qq_fwd_routing_snapshot,
    is_group_registered,
    GROUP_NOT_REGISTERED_TEXT,
)
from plugins.media import make_text, make_media_link
from plugins.sender import send_relay_message
from plugins.translation import translate_text_parts
from plugins.qq_keyboard import (
    escape_markdown,
    send_titled_reply,
)


# =====================================================
# 指令
#
#   /转发 列表      —— 纯文本编号名单与用法提示（不再提供按钮）
#   /转发 1 3       —— 引用消息后直接转发到第 1、3 个目标群
# =====================================================
CMD_LIST = "/转发 列表"
CMD_PREFIX = "/转发 "
CMD_BARE = "/转发"

USAGE_TEXT = "请引用要转发的消息，再发送 /转发 序号（可一次写多个，如 /转发 1 3）。"
NO_CONTENT_TEXT = "没有可转发的内容：请引用一条消息，或先发送要转发的内容再使用 /转发 序号。"
BOT_QUOTE_TEXT = "请引用普通消息；机器人消息和命令消息不会被转发。"
EMPTY_QUOTE_TEXT = "引用的消息没有可转发的内容。"

# 记住最近一条可转发内容的时间窗（秒），用于未引用消息时的回退。
CONTENT_MEMORY_SECONDS = 600


# =====================================================
# 目标群计算（纯函数）
#
# 候选 = 与当前群至少共同属于一个转发组的已注册(enabled)群，且非当前群。
# 跨多个转发组合并去重（由 related_openids 集合保证）。
# =====================================================

def compute_target_list(entries, current_openid, related_openids):
    """从群条目列表计算可转发目标群。

    related_openids：与当前群至少共同属于一个转发组的其他群 openid 集合
    （见 config.get_related_group_openids；set 或可 in 判断的容器）。
    返回 [{"openid", "name"}]；只含 enabled、在 related 集合内且非当前群。
    顺序与 settings 中条目顺序一致，「/转发 列表」与「/转发 {序号}」共用同一顺序。
    """
    current = str(current_openid)
    related = set(related_openids or ())
    targets = []
    seen = set()
    for item in entries:
        oid = str(item.get("openid") or "")
        if not oid or oid in seen:
            continue
        if oid == current:
            continue
        if not item.get("enabled"):
            continue
        if oid not in related:
            continue
        seen.add(oid)
        targets.append({
            "openid": oid,
            "name": str(item.get("name") or item.get("remark") or "").strip(),
        })
    return targets


def build_relay_list(targets, allow_all=False):
    """把目标群列表渲染为纯文本编号名单和转发方法。

    私聊 allow_all=True 时额外说明 /转发 全部；群聊不提供全选。
    """
    lines = ["转发群列表"]
    if not targets:
        lines.append("当前没有可转发的目标群")
        return "\n".join(lines)
    for i, target in enumerate(targets, start=1):
        lines.append("{}. {}".format(i, target["name"] or target["openid"]))
    lines.append("")
    if allow_all:
        lines.append(
            "引用要转发的消息后发送 /转发 序号（可写多个，如 /转发 1 3）；"
            "/转发 全部 可发往全部群。"
        )
    else:
        lines.append(
            "引用要转发的消息后发送 /转发 序号（可写多个，如 /转发 1 3）。"
        )
    lines.append("未引用时使用最近一条消息。")
    return "\n".join(lines)


# =====================================================
# 指令解析（纯函数）
#
# parse_relay 返回 (kind, payload)：
#   ("list",    None)       —— /转发 列表（也接受裸 /转发）
#   ("forward", [int, ...]) —— /转发 {序号...}，按序号直接转发
#   ("invalid", None)       —— 无法解析的 /转发 参数
#   (None,      None)       —— 不是 relay 指令
# =====================================================

def _parse_relay_numbers(text):
    """解析空格分隔的正整数序号；非法或为空返回 None。"""
    tokens = str(text or "").split()
    if not tokens:
        return None
    numbers = []
    for token in tokens:
        if not token.isascii() or not token.isdecimal():
            return None
        digits = token.lstrip("0") or "0"
        if digits == "0" or len(digits) > 9:
            return None
        numbers.append(int(digits))
    return numbers


def parse_relay(text):
    st = (text or "").strip()
    if st == CMD_BARE or st == CMD_LIST or st.startswith(CMD_LIST + " "):
        return ("list", None)
    if st.startswith(CMD_PREFIX):
        numbers = _parse_relay_numbers(st[len(CMD_PREFIX):])
        return ("forward", numbers) if numbers else ("invalid", None)
    return (None, None)


# =====================================================
# 每成员串行锁与最近内容回退
#
# _last_content[(group, member)] = {"ts", "event", "text"}
# 未引用消息时使用该成员最近一条普通消息，避免必须补发内容。
# =====================================================
_member_locks = WeakValueDictionary()

_last_content = {}


def _get_member_lock(group_openid, member_openid):
    key = (group_openid, member_openid)
    lock = _member_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _member_locks[key] = lock
    return lock


def remember_content(group_openid, member_openid, event, text):
    """记住最近一条可转发内容，供未引用消息时回退。"""
    now = time.monotonic()
    if len(_last_content) >= 256:
        cutoff = now - CONTENT_MEMORY_SECONDS
        for key, record in list(_last_content.items()):
            if record.get("ts", 0) < cutoff:
                _last_content.pop(key, None)
    _last_content[(group_openid, member_openid)] = {
        "ts": now,
        "event": event,
        "text": text,
    }


def get_remembered_content(group_openid, member_openid, now_ts=None):
    record = _last_content.get((group_openid, member_openid))
    if not record:
        return None
    now = now_ts if now_ts is not None else time.monotonic()
    if now - record["ts"] > CONTENT_MEMORY_SECONDS:
        _last_content.pop((group_openid, member_openid), None)
        return None
    return record


# =====================================================
# 引用消息提取
#
# 适配器在 event.reply 中放被引用消息；缺失时回退 msg_elements。
# 引用内容保留文字与附件，供群聊和私聊手动转发共用。
# =====================================================
_BOT_REPLY_PREFIXES = (
    "# 转发群列表",
    "转发群列表",
    "# 转发结果",
    "转发结果",
    "# WRelay菜单",
    "WRelay菜单",
    "# 群准入提示",
    "群准入提示",
    "# 准入提示",
    "准入提示",
)


def extract_quoted_message(event):
    """提取被引用消息；没有引用时返回 None。

    返回 {"content", "attachments", "from_bot", "author_id", "author_name",
    "author_known"}，attachments 的结构与 build_forward_content 入参一致。
    """
    reply = getattr(event, "reply", None)
    if reply is None:
        elements = getattr(event, "msg_elements", None) or []
        reply = elements[0] if elements else None
    if reply is None:
        return None

    author = getattr(reply, "author", None)
    return {
        "content": str(getattr(reply, "content", "") or ""),
        "attachments": [
            {"url": attachment.url, "content_type": attachment.content_type or ""}
            for attachment in (getattr(reply, "attachments", None) or [])
            if getattr(attachment, "url", None)
        ],
        "from_bot": bool(getattr(author, "bot", False)),
        "author_id": str(
            getattr(author, "id", None)
            or getattr(author, "member_openid", None)
            or getattr(author, "user_openid", None)
            or ""
        ),
        "author_name": str(getattr(author, "username", None) or ""),
        "author_known": author is not None,
    }


def quoted_message_error(quote, bot=None):
    """校验引用内容；不可转发时返回提示文本，否则返回 None。"""
    if quote is None:
        return None
    text = quote["content"].strip()
    bot_ids = {
        _qq_bot_id(bot),
        str(getattr(bot, "id", "") or ""),
    }
    bot_ids.discard("")
    bot_name = str(
        getattr(getattr(bot, "self_info", None), "username", "") or ""
    ).strip()
    if (
        quote["from_bot"]
        or (quote["author_id"] and quote["author_id"] in bot_ids)
        or (bot_name and quote["author_name"] == bot_name)
    ):
        return BOT_QUOTE_TEXT
    if text.startswith("/"):
        return BOT_QUOTE_TEXT
    if not quote["author_known"] and text.startswith(_BOT_REPLY_PREFIXES):
        # 引用载荷缺少作者时，用 WRelay 固定回复标题兜底识别机器人名单/结果。
        return BOT_QUOTE_TEXT
    if not text and not quote["attachments"]:
        return EMPTY_QUOTE_TEXT
    return None


# =====================================================
# 媒体重发：把 QQ 附件的 URL 字节上传到目标群
#
# QQ 自己的图片/音视频 URL 国内直连即可，不走 Discord 代理。
# 下载失败时降级为文字链接（由 sender 的 media 路径兜底）。
# =====================================================
_MAX_MEDIA_BYTES = 30 * 1024 * 1024
_media_timeout = 60
_media_sema = asyncio.Semaphore(4)
_qq_media_client = None
_qq_media_tasks = set()
_qq_media_stopping = False


def _get_qq_media_client():
    global _qq_media_client
    if _qq_media_client is None:
        # 与 media/RSS 链路一致：忽略系统与环境代理，避免 Windows 代理绕过
        # 列表中的裸 IPv6 条目（如 ::1）在构造客户端时抛 InvalidURL，使
        # QQ 手动转发的媒体下载全部失败。
        _qq_media_client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
            trust_env=False,
        )
    return _qq_media_client


@get_driver().on_shutdown
async def _close_qq_media_client():
    global _qq_media_client, _qq_media_stopping
    _qq_media_stopping = True
    current_task = asyncio.current_task()
    media_tasks = [
        task for task in _qq_media_tasks
        if task is not current_task and not task.done()
    ]
    for task in media_tasks:
        task.cancel()
    if media_tasks:
        await asyncio.gather(*media_tasks, return_exceptions=True)
    _qq_media_tasks.clear()
    if _qq_media_client is not None:
        await _qq_media_client.aclose()
        _qq_media_client = None


_CONTENT_KIND = {
    "image": "图片",
    "audio": "音频",
    "video": "视频",
}
_SEGMENT_BUILDER = {
    "image": QQMessageSegment.file_image,
    "audio": QQMessageSegment.file_audio,
    "video": QQMessageSegment.file_video,
}


def _kind_of(content_type):
    if not content_type:
        return "file"
    return content_type.split("/", maxsplit=1)[0]


async def _fetch_no_proxy(url):
    """下载附件字节（不走代理）；失败/超限返回 None。"""
    if _qq_media_stopping:
        return None
    media_task = asyncio.current_task()
    if media_task is not None:
        _qq_media_tasks.add(media_task)
    try:
        async with _media_sema:
            async with _get_qq_media_client().stream(
                "GET", url, timeout=_media_timeout
            ) as resp:
                if resp.status_code != 200:
                    logger.warning("QQ转发媒体下载失败: {} {}", resp.status_code, url)
                    return None
                data = bytearray()
                async for chunk in resp.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > _MAX_MEDIA_BYTES:
                        logger.warning("QQ转发媒体超上限，放弃: {}", url)
                        return None
                return bytes(data)
    except Exception as exc:
        logger.warning("QQ转发媒体下载异常: {} {}", exc, url)
        return None
    finally:
        if media_task is not None:
            _qq_media_tasks.discard(media_task)


def normalize_event_message(event):
    """把一条群消息归一化为可转发内容 dict（文本 + 附件）。

    结构对齐 build_forward_content 的入参，供待转发的“下一条消息”使用。
    """
    content = event.get_plaintext() or ""
    attachments = [
        {"url": a.url, "content_type": a.content_type or ""}
        for a in (getattr(event, "attachments", None) or [])
        if getattr(a, "url", None)
    ]
    return {"content": content, "attachments": attachments}


async def build_forward_content(message):
    """把一条待转发消息渲染为 (text_parts, media_items)。

    结构对齐 plugins.fetch.build_parts，可被 send_relay_message 直接消费：
      - 文本 → text_parts（make_text）
      - 附件 → media_items（file_* 段，下载失败自动降级为链接）
    """
    text_parts = []
    media_items = []

    text = (message.get("content") or "").strip()
    if text:
        text_parts.append(make_text(text))

    for att in message.get("attachments") or []:
        url = att.get("url")
        if not url:
            continue
        kind = _kind_of(att.get("content_type"))
        segment_builder = _SEGMENT_BUILDER.get(kind)
        if segment_builder is None:
            # 未知类型按文件处理
            kind = "file"
            segment_builder = _SEGMENT_BUILDER.get(kind)
        if segment_builder is not None:
            data = await _fetch_no_proxy(url)
            if data is not None:
                media_items.append({
                    "kind": _CONTENT_KIND.get(kind, "文件"),
                    "url": url,
                    "segment": segment_builder(data),
                })
                continue
        # 下载失败或类型不支持 → 交给 sender 降级为链接
        media_items.append({
            "kind": _CONTENT_KIND.get(kind, "文件"),
            "url": url,
            "segment": make_media_link(_CONTENT_KIND.get(kind, "文件"), url),
        })

    return text_parts, media_items


# =====================================================
# 发送收口
#
# custom_send 默认走 sender.send_relay_message（分片/重试/限流退避/降级）。
# 测试可替换为桩，验证“发到哪个目标群/发了什么内容”。
# 只发送到目标群，不发送到全局启用群。
# =====================================================
async def send_forward_to_group(
    target_openids, text_parts, media_items, source_group_name="未知群"
):
    """转发到目标群；target_openids 可以是单个 openid 或 openid 列表。"""
    groups = (
        [target_openids]
        if isinstance(target_openids, str)
        else [str(item) for item in target_openids]
    )
    return await custom_send(
        text_parts,
        media_items,
        groups=groups,
        source_label=source_group_name,
    )


async def _default_custom_send(text_parts, media_items, groups=None, source_label=None):
    return await send_relay_message(
        text_parts, media_items, groups=groups, source_label=source_label
    )


custom_send = _default_custom_send  # 测试可替换


# 目标群实际发送前再次校验（目标群须仍与当前群同属至少一个转发组且仍 enabled）
def _target_still_valid(target, group, entries, related_openids):
    related = set(related_openids or ())
    return any(
        str(e.get("openid")) == target["openid"]
        and e.get("enabled")
        and str(e.get("openid")) in related
        for e in entries
    )


# =====================================================
# 事件处理器
# =====================================================

def _qq_bot_id(bot):
    return str(getattr(bot, "self_id", "") or getattr(bot, "id", "") or "").strip()


async def _group_not_registered(bot, event):
    """返回当前 Bot 对应的群是否未登记（准入检查，以 qq_group_openids 为准）。

    未登记时回复准入提示并返回 True，调用方应停止后续功能执行；
    已登记返回 False，继续原逻辑。"""
    registered = await asyncio.to_thread(
        is_group_registered, event.group_openid, _qq_bot_id(bot)
    )
    if registered:
        return False
    await send_titled_reply(
        bot,
        event,
        "群准入提示",
        GROUP_NOT_REGISTERED_TEXT,
    )
    return True


def _is_registration_command(text):
    st = (text or "").strip()
    return (
        st == "/注册"
        or st.startswith("/注册 ")
        or st == "/注册群"
        or st.startswith("/注册群 ")
    )


fwd_command = on_message(
    priority=15,
    block=False,
)


@fwd_command.handle()
async def handle_fwd(bot: Bot, event: Event):
    if not isinstance(bot, QQBot):
        return
    if not isinstance(event, GroupMessageCreateEvent):
        return
    group = event.group_openid
    user = event.author.member_openid
    async with _get_member_lock(group, user):
        text = event.get_plaintext() or ""

        # 注册申请入口不受群登记限制（未登记群正是用它来提交申请）。
        if _is_registration_command(text):
            return

        kind, payload = parse_relay(text)

        # 群聊命令：群未登记时不执行功能，提示先登记群。
        if kind is not None:
            if await _group_not_registered(bot, event):
                return
            if kind == "list":
                await _cmd_relay_list(bot, event)
            elif kind == "forward":
                await _cmd_relay_forward(bot, event, payload)
            else:
                await send_titled_reply(bot, event, "转发结果", USAGE_TEXT)
            return

        # 普通斜杠命令交给各自处理器，不作为转发内容。
        if text.strip().startswith("/"):
            return

        if not text.strip() and not (getattr(event, "attachments", None) or []):
            return
        remember_content(group, user, event, text)


async def _cmd_relay_list(bot, event):
    """发送纯文本目标群名单与转发方法。"""
    group = event.group_openid
    entries, related = await asyncio.to_thread(get_qq_fwd_routing_snapshot, group)
    targets = compute_target_list(entries, group, related)
    await send_titled_reply(
        bot,
        event,
        "转发群列表",
        build_relay_list(targets),
    )


async def send_relay_list(bot, event):
    """显示当前群可用的手动转发目标列表。"""
    await _cmd_relay_list(bot, event)


def _selected_targets(targets, numbers):
    """按序号取目标并去重；出现越界序号时返回 None。"""
    selected = []
    seen = set()
    for number in numbers:
        if number < 1 or number > len(targets):
            return None
        target = targets[number - 1]
        openid = str(target["openid"])
        if openid in seen:
            continue
        seen.add(openid)
        selected.append(target)
    return selected


async def _cmd_relay_forward(bot, event, numbers):
    """引用消息（或最近内容）直接转发到指定序号的目标群。"""
    group = event.group_openid
    user = event.author.member_openid
    entries, related = await asyncio.to_thread(get_qq_fwd_routing_snapshot, group)
    targets = compute_target_list(entries, group, related)
    selected = _selected_targets(targets, numbers) if targets else None
    if not selected:
        await send_titled_reply(
            bot, event, "转发结果", "无法找到对应的转发群"
        )
        return

    quote = extract_quoted_message(event)
    error = quoted_message_error(quote, bot)
    if error:
        await send_titled_reply(bot, event, "转发结果", error)
        return

    if quote is not None:
        message = {
            "content": quote["content"],
            "attachments": quote["attachments"],
        }
    else:
        record = get_remembered_content(group, user)
        if record is None:
            await send_titled_reply(bot, event, "转发结果", NO_CONTENT_TEXT)
            return
        message = normalize_event_message(record["event"])
        if not message["content"].strip() and not message["attachments"]:
            await send_titled_reply(bot, event, "转发结果", NO_CONTENT_TEXT)
            return

    await _forward_to_targets(bot, event, selected, message)


async def _forward_to_targets(bot, event, targets, message):
    """把给定内容转发到全部目标群，并汇总回复一次结果。"""
    group = event.group_openid

    # 实际发送前再次校验目标群（仍同属至少一个转发组且 enabled）。
    entries, related = await asyncio.to_thread(
        get_qq_fwd_routing_snapshot, group
    )
    valid = [t for t in targets if _target_still_valid(t, group, entries, related)]
    if not valid:
        await send_titled_reply(
            bot, event, "转发结果", "未转发：目标群已不可用，请重新选择。"
        )
        return

    source_group_name = next(
        (
            str(item.get("name") or item.get("remark") or "").strip()
            for item in entries
            if str(item.get("openid") or "") == str(group)
            and str(item.get("name") or item.get("remark") or "").strip()
        ),
        str(group),
    )

    try:
        text_parts, media_items = await build_forward_content(message)
        event_id = str(getattr(event, "id", "") or "")
        text_parts = await translate_text_parts(
            text_parts,
            ("qq_group", event_id) if event_id else None,
        )
    except Exception as exc:
        logger.exception("QQ转发内容处理异常: {}", exc)
        await send_titled_reply(
            bot, event, "转发结果", "转发失败：内容处理失败。"
        )
        return

    try:
        ok_map = await send_forward_to_group(
            [t["openid"] for t in valid],
            text_parts,
            media_items,
            source_group_name,
        )
    except Exception as exc:
        logger.exception("QQ转发发送异常: {}", exc)
        await send_titled_reply(
            bot, event, "转发结果", "转发失败：发送失败。"
        )
        return

    ok_labels = []
    failed_labels = []
    for target in valid:
        label = escape_markdown(
            str(target.get("name") or "").strip()
            or str(target.get("openid") or "目标群")
        )
        if ok_map.get(target["openid"]):
            ok_labels.append(label)
        else:
            failed_labels.append(label)

    lines = []
    if ok_labels:
        lines.append("已转发到「{}」。".format("」「".join(ok_labels)))
    if failed_labels:
        lines.append("未转发到「{}」：发送失败。".format("」「".join(failed_labels)))
    await send_titled_reply(
        bot, event, "转发结果", "\n".join(lines) or "转发失败"
    )
