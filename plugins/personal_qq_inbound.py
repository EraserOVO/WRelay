"""Authorized personal QQ commands; never automatically relay group messages."""
import asyncio
import time
from collections import OrderedDict
from types import SimpleNamespace
from weakref import WeakValueDictionary

from nonebot import on_message, logger
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.adapters.qq import Message, MessageSegment as Segment

from plugins import config, personal_qq
from plugins.json_io import run_in_thread_to_completion, update_json

_events = OrderedDict()
_recent = {}
_locks = WeakValueDictionary()
_seen = OrderedDict()
TTL = 600
LIMIT = 1000


def user_id(event):
    return "personal:" + str(event.user_id)


def conversation(bot, event):
    group = getattr(event, "group_id", None)
    return (str(bot.self_id), str(group) if group is not None else "private:" + str(event.user_id), str(event.user_id))


def _prune():
    now = time.monotonic()
    for cache in (_events, _seen):
        while cache and (len(cache) > LIMIT or next(iter(cache.values()))[0] < now - TTL):
            cache.popitem(last=False)
    for key, value in list(_recent.items()):
        if value not in _events:
            _recent.pop(key, None)


def remember(bot, event):
    key = (*conversation(bot, event)[:2], str(event.message_id))
    _events[key] = (time.monotonic(), event)
    _events.move_to_end(key)
    _recent[conversation(bot, event)] = key
    _prune()


def clear_connection(uin):
    for key in list(_events):
        if key[0] == str(uin):
            _events.pop(key, None)
    _prune()


def content_event(bot, event):
    _prune()
    replies = [str(s.data.get("id", "")) for s in event.message if s.type == "reply"]
    if not replies and getattr(event, "reply", None) is not None:
        replies = [str(event.reply.message_id)]
    key = ((*conversation(bot, event)[:2], replies[0]) if replies
           else _recent.get(conversation(bot, event)))
    record = _events.get(key)
    return record[1] if record else None


def register_user(event):
    """An explicit request is discoverable but never grants permission."""
    def mutate(data):
        entries = data.setdefault("qq_user_openids", [])
        if any(item.get("openid") == user_id(event) for item in entries):
            return
        if len(entries) >= LIMIT:
            raise ValueError("用户登记已达上限，请联系管理员")
        entries.append({"openid": user_id(event), "enabled": False,
                        "name": str(getattr(event.sender, "nickname", "") or event.user_id)[:80],
                        "remark": "个人 QQ 注册申请"})
    update_json(config.SETTINGS_FILE, mutate, default={})


def targets(bot, event):
    entries = config.get_qq_group_entries()
    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        source = "personal:" + str(group_id)
        if not any(e.get("openid") == source and e.get("enabled")
                   and e.get("bot_id") == "personal:" + str(bot.self_id) for e in entries):
            return []
        related = config.get_related_group_openids(source)
        return [e for e in entries if e.get("enabled") and e.get("openid") in related]
    # Explicitly authorized private commands use the same global target selection as official C2C.
    return [e for e in entries if e.get("enabled") and e.get("openid")]


async def reply(bot, event, text):
    # No automatic retry for replies whose outcome is unknown.
    await bot.send(event, str(text))


async def prepare(event):
    from plugins import command
    from plugins.media import fetch_bytes
    from plugins.translation import translate_text_parts
    text = event.get_plaintext().strip()
    if command.is_discord_url(text) or command.is_x_status_url(text) or command.parse_manual_post_url(text):
        content = await command._prepare_manual_relay_content({"content": text})
        parts = await translate_text_parts(content.text_parts, content.translation_key,
                                          skip_leading_parts=1, config=content.translation_config)
        return parts, content.media_items
    parts, media = [], []
    for segment in event.message:
        if segment.type == "text":
            parts.append(Segment.text(segment.data["text"]))
        elif segment.type == "face":
            parts.append(Segment.text("[表情 " + str(segment.data.get("id", "")) + "]"))
        elif segment.type == "at":
            parts.append(Segment.text("@" + str(segment.data.get("qq", ""))))
        elif segment.type in ("image", "record", "video"):
            url = segment.data.get("url") or segment.data.get("file") or ""
            if not str(url).startswith(("https://", "http://")):
                raise ValueError("媒体没有可访问的网络地址，不能转发本地路径")
            data = await fetch_bytes(url)
            if not data:
                raise ValueError("媒体下载失败或超过大小限制")
            kind = {"image": "图片", "record": "音频", "video": "视频"}[segment.type]
            constructor = {"image": Segment.file_image, "record": Segment.file_audio,
                           "video": Segment.file_video}[segment.type]
            media.append({"kind": kind, "url": url, "segment": constructor(data)})
        elif segment.type != "reply":
            raise ValueError("该消息包含暂不支持的附件类型")
    return await translate_text_parts(parts, ("qq_group", str(event.message_id))), media


async def forward(bot, event, text):
    from plugins.qq_fwd import parse_relay, _selected_targets, build_relay_list
    from plugins.sender import send_relay_message
    available = await asyncio.to_thread(targets, bot, event)
    kind, numbers = parse_relay(text)
    private = getattr(event, "group_id", None) is None
    if kind == "list":
        await reply(bot, event, build_relay_list(available, allow_all=private))
        return
    selected = available if private and text.strip() == "/转发 全部" else (
        _selected_targets(available, numbers) if kind == "forward" else None)
    if not selected:
        await reply(bot, event, "请发送 /转发 列表，再引用消息发送 /转发 序号。")
        return
    original = content_event(bot, event)
    if original is None:
        await reply(bot, event, "没有可转发内容：请引用最近十分钟内本次连接收到的普通消息，或先发送内容。")
        return
    from plugins.command import _ManualRelaySilentSkip, _ManualRelayContentError
    try:
        parts, media = await prepare(original)
    except _ManualRelaySilentSkip:
        return
    except _ManualRelayContentError as exc:
        await reply(bot, event, str(exc))
        return
    if not media and not any(str(part.data.get("text", "")).strip() for part in parts):
        await reply(bot, event, "引用的消息没有可转发的内容")
        return
    # Recheck grants and routing after media/network work.
    if not await asyncio.to_thread(config.is_user_authorized, user_id(event)):
        await reply(bot, event, "未授权，转发已取消")
        return
    valid = {e["openid"] for e in await asyncio.to_thread(targets, bot, event)}
    groups = [e["openid"] for e in selected if e["openid"] in valid]
    result = await send_relay_message(parts, media, groups=groups,
        channel_id="qq_manual:" + ":".join(conversation(bot, event)), message_id=str(event.message_id)) if groups else {}
    await reply(bot, event, "转发成功" if result and all(result.values()) else "转发未全部成功，请查看消息追踪及待确认投递")


class LinkReply:
    """Bridge existing Bilibili parser to the guarded personal sender."""
    def __init__(self, sender):
        self.sender = sender

    async def send(self, event, message):
        await self.sender.send_to_group(event.group_openid, message,
            "personal-link:" + event.group_openid, str(event.id))


async def group_link(bot, event, sender):
    from plugins import bilibili_auto_resolve as service
    group = "personal:" + str(event.group_id)
    if event.is_tome():
        return
    if any(s.type == "at" and str(s.data.get("qq")) in (str(bot.self_id), "all") for s in event.message):
        return
    if not await asyncio.to_thread(service._read_gate, group, sender.self_id):
        return
    url = service.extract_first_video_url(event.get_plaintext())
    if url:
        await service._resolve_and_send(LinkReply(sender), SimpleNamespace(group_openid=group, id=event.message_id), url)


async def confirm_restart(bot):
    from plugins import manage
    async with manage._restart_confirm_lock:
        data = await manage._load_restart_pending_async()
        if not isinstance(data, dict) or data.get("bot_id") != "personal:" + str(bot.self_id):
            return
        user = data.get("openid", "")
        stamp = data.get("time")
        if (not isinstance(user, str) or not user.startswith("personal:")
                or type(stamp) not in (int, float) or not -60 <= time.time() - stamp <= 300):
            await manage._clear_restart_pending_async()
            return
        try:
            if await asyncio.to_thread(config.is_user_authorized, user):
                await bot.send_private_msg(user_id=int(user.split(":", 1)[1]), message="重启完毕")
        except Exception as exc:
            logger.warning("个人 QQ 重启确认失败: {}", type(exc).__name__)
        finally:
            await manage._clear_restart_pending_async()


async def dispatch(bot, event):
    sender = personal_qq.connected_bots().get("personal:" + str(bot.self_id))
    if sender is None or sender.bot is not bot or str(event.self_id) != str(bot.self_id):
        return
    accounts = await asyncio.to_thread(personal_qq.config.enabled_connections)
    account = accounts.get(str(bot.self_id))
    if not account or (sender.token is not None and sender.token != account["token"]):
        return
    if str(event.user_id) == str(bot.self_id) or getattr(event.sender, "role", None) == "bot":
        return
    key = (*conversation(bot, event)[:2], str(event.message_id))
    _prune()
    if key in _seen:
        return
    _seen[key] = (time.monotonic(), None)
    text = event.get_plaintext().strip()
    private = getattr(event, "group_id", None) is None
    if private and text == "/注册":
        await run_in_thread_to_completion(register_user, event)
        await reply(bot, event, "已登记个人 QQ，请联系管理员在 QQ → QQ 页的白名单中明确启用。")
        return
    authorized = await asyncio.to_thread(config.is_user_authorized, user_id(event))
    if text.startswith("/"):
        if not authorized:
            await reply(bot, event, "未授权，请先私聊 /注册 并联系管理员启用白名单")
            return
        if text == "/转发" or text.startswith("/转发 "):
            await forward(bot, event, text)
        elif private:
            from plugins import manage
            command, *args = text.split()
            if command == "/重启":
                await reply(bot, event, "正在重启机器人…")
                await manage._write_restart_pending_async(user_id(event), sender.self_id)
                try:
                    result = await manage._panel_post("/api/bot/restart")
                    if not isinstance(result, dict) or not result.get("ok"):
                        raise ValueError("重启失败")
                except Exception:
                    await manage._clear_restart_pending_async()
                    await reply(bot, event, "重启失败，请检查桌面端")
            elif command == "/菜单":
                await reply(bot, event, "/运行状态\n/转发 列表\n/定时消息\n/转发组\n/群列表\n/频道列表\n/权限列表\n/补发状态\n/重启")
            else:
                await reply(bot, event, await manage._dispatch(command, args))
        return
    if private:
        if authorized:
            remember(bot, event)
    else:
        if not await asyncio.to_thread(config.is_group_registered, "personal:" + str(event.group_id), sender.self_id):
            return
        remember(bot, event)
        await group_link(bot, event, sender)


personal_messages = on_message(priority=5, block=False)


@personal_messages.handle()
async def handle(bot: Bot, event: MessageEvent):
    if not isinstance(bot, Bot) or not isinstance(event, MessageEvent):
        return
    lock = _locks.setdefault(conversation(bot, event), asyncio.Lock())
    async with lock:
        try:
            from plugins.bot_profiles import scope
            with scope("personal:" + str(bot.self_id)):
                await dispatch(bot, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("个人 QQ 入站处理失败: {}", type(exc).__name__)
            await reply(bot, event, "处理失败，请检查连接、授权及待确认投递")
