"""Best-effort QQ profile lookup; identity data never grants permissions."""
import asyncio
import time
from collections import OrderedDict

from nonebot import logger
from nonebot.drivers import Request

from plugins import identities

_REFRESH_SECONDS = 6 * 3600
_FAILURE_SECONDS = 3600
_MAX_PENDING = 128
_MAX_CACHE = 4096
_cache = OrderedDict()
_pending = set()
_tasks = set()
_locks = {}
_last_request = {}


def schedule_identity_lookup(bot, kind, openid):
    bot_id = str(getattr(bot, "self_id", "") or "").strip()
    openid = str(openid or "").strip()
    if kind not in ("users", "groups") or not bot_id or not openid:
        return
    key = (bot_id, kind, openid)
    if key in _pending or len(_pending) >= _MAX_PENDING or _cache.get(key, 0) > time.monotonic():
        return
    _pending.add(key)
    task = asyncio.create_task(_lookup(bot, key))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _lookup(bot, key):
    bot_id, kind, openid = key
    delay = _FAILURE_SECONDS
    try:
        # 低于群资料接口 30 QPM，按 Bot 限制请求速率。
        lock = _locks.setdefault(bot_id, asyncio.Lock())
        async with lock:
            wait = _last_request.get(bot_id, 0) + 2.2 - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            _last_request[bot_id] = time.monotonic()
            base = bot.adapter.get_api_base()
            # 新接口使用统一域名；沙箱和自定义 API 地址仍沿用适配器配置。
            if base.host == "api.sgroup.qq.com":
                base = base.with_host("api.bot.qq.com")
            url = base.joinpath("v2", kind, openid, "info")
            result = await asyncio.wait_for(bot._request(Request("GET", url)), timeout=15)
        field = "group_name" if kind == "groups" else "username"
        name = result.get(field) if isinstance(result, dict) else None
        if isinstance(name, str) and name.strip():
            if kind == "groups":
                await asyncio.to_thread(identities.record_group_identity, openid, group_name=name, bot_id=bot_id)
            else:
                await asyncio.to_thread(identities.record_user_identity, openid, nickname=name, bot_id=bot_id)
            delay = _REFRESH_SECONDS
    except Exception as exc:
        logger.debug("QQ 身份资料查询失败（{}）: {}", kind, exc)
    finally:
        _pending.discard(key)
        _cache[key] = time.monotonic() + delay
        _cache.move_to_end(key)
        while len(_cache) > _MAX_CACHE:
            _cache.popitem(last=False)
