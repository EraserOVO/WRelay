import asyncio

from nonebot import get_adapter, get_app
from nonebot import get_bots
from fastapi import Request
from fastapi.responses import JSONResponse

from nonebot.adapters.discord import Adapter as DiscordAdapter
from nonebot.adapters.discord import Bot as DiscordBot
from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.qq import Bot as QQBot

from plugins.retry import get_retry_status
from plugins.message_trace import get_delivery_traces
from plugins.translation import get_translation_status, get_translation_statuses
from plugins.json_io import load_json
from plugins.paths import data_path
from plugins import personal_qq


# =====================================================
# bot 健康接口（供管理面板显示真实运行状态）
#
# get_bots() 只包含已成功完成网关握手的 bot：
# 握手失败（如启动时网络异常）返回空；连上后断线会被移除。
# 面板 /api/status 询问本接口，据此区分已连接、连接任务进行中和未连接，
# 避免把启动握手中的连接误判为失败。
# =====================================================

app = get_app()


@app.middleware("http")
async def profile_context(request: Request, call_next):
    from plugins import bot_profiles
    owner = request.query_params.get("bot_id", "")
    if owner:
        try:
            bot_profiles.filename(owner)
        except ValueError as exc:
            return JSONResponse({"ok": False, "msg": str(exc)}, status_code=400)
    with bot_profiles.scope(owner):
        return await call_next(request)
PANEL_AUTH_FILE = data_path("panel_auth.json")


def _maintenance_alert_bot_ids(alert_id, profile_owners):
    """Resolve profile-owned alert IDs; return [] rather than broadcast if ownership is unclear."""
    if alert_id == "file:bot_profiles/unassigned.json":
        return []
    from plugins import bot_profiles
    for owner in profile_owners:
        owner = str(owner or "").strip()
        if not owner or owner == "unassigned":
            continue
        try:
            profile_name = bot_profiles.filename(owner)
        except ValueError:
            continue
        stem = profile_name.rsplit(".", 1)[0]
        if any(alert_id.startswith("rss:{}:{}:".format(chain, owner))
               for chain in ("X", "Bilibili", "YouTube")):
            return [owner]
        if any(alert_id == "{}:{}:{}".format(kind, chain, owner)
               for kind in ("config", "protection")
               for chain in ("X", "Bilibili", "YouTube")):
            return [owner]
        if (alert_id == "file:bot_profiles/" + profile_name
                or alert_id.startswith("file:bot_profile_runtime/" + stem + "/")):
            return [owner]
    # These ID families are account-scoped. If the owning profile is missing
    # from the current index, fail closed instead of sending to every Bot.
    account_scoped_prefixes = tuple(
        prefix
        for chain in ("X", "Bilibili", "YouTube")
        for prefix in ("rss:{}:".format(chain), "config:{}:".format(chain), "protection:{}:".format(chain))
    ) + ("file:bot_profiles/", "file:bot_profile_runtime/")
    if alert_id.startswith(account_scoped_prefixes):
        # Before profile migration, RSS configuration was genuinely shared by
        # all Bots. Keep that legacy behavior only when no account profiles exist.
        if (not profile_owners and alert_id.startswith(("rss:X:", "rss:Bilibili:", "rss:YouTube:"))):
            return None
        return []
    return None


def _adapter_connecting(adapter_type):
    """Return whether this adapter still has an unfinished gateway task."""
    try:
        adapter = get_adapter(adapter_type)
    except ValueError:
        return False
    tasks = getattr(adapter, "tasks", ())
    try:
        return any(
            callable(getattr(task, "done", None)) and not task.done()
            for task in tasks
        )
    except TypeError:
        return False


def _panel_token():
    data = load_json(PANEL_AUTH_FILE, default={})
    token = data.get("token") if isinstance(data, dict) else ""
    return token if isinstance(token, str) and len(token) >= 32 else ""


@app.middleware("http")
async def bot_api_token_guard(request: Request, call_next):
    """仅允许持有本机面板令牌的调用访问 Bot 管理接口。"""
    if request.url.path.startswith("/api/"):
        token = await asyncio.to_thread(_panel_token)
        if not token or request.headers.get("x-wrelay-token") != token:
            return JSONResponse({"ok": False, "msg": "Unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/api/health")
async def api_health(bot_id: str = ""):
    """返回 Discord/QQ 实时连接状态（供面板显示真实运行状态）"""
    from plugins.bot_profiles import CURRENT
    owner = bot_id or CURRENT.get()
    bots = get_bots()
    qq_connected = any(isinstance(bot, QQBot) for bot in bots.values())
    discord_connected = any(isinstance(bot, DiscordBot) for bot in bots.values())
    qq_bots = [
        str(getattr(bot, "self_id", "") or getattr(bot, "id", "") or "").strip()
        for bot in bots.values()
        if isinstance(bot, QQBot)
    ]
    qq_bots = [bot_id for bot_id in qq_bots if bot_id]
    qq_bots.extend(personal_qq.connected_bots())
    return {
        "ok": True,
        "discord": discord_connected,
        "discord_connecting": (
            not discord_connected and _adapter_connecting(DiscordAdapter)
        ),
        "qq": qq_connected or personal_qq.any_connected(),
        "qq_connecting": (
            not qq_connected and _adapter_connecting(QQAdapter)
        ),
        "qq_bots": qq_bots,
        "personal_qq": personal_qq.connection_status(),
        "retry": await asyncio.to_thread(get_retry_status, owner or None),
        "translation": get_translation_status(),
        "translation_by_bot": get_translation_statuses(),
    }


@app.get("/api/personal-qq/status")
async def personal_qq_status():
    try:
        records = await asyncio.to_thread(personal_qq.uncertain_records)
        return {"ok": True, "connections": personal_qq.connection_status(),
                "uncertain": [dict(key=key, **value) for key, value in records.items()]}
    except Exception:
        return {"ok": False, "msg": "个人 QQ 待确认记录无法读取"}


@app.get("/api/personal-qq/groups")
async def personal_qq_groups(uin: str):
    sender = personal_qq.connected_bots().get("personal:" + uin)
    if sender is None:
        return {"ok": False, "msg": "个人 QQ 尚未连接并完成账号校验"}
    try:
        groups = await asyncio.wait_for(sender.bot.get_group_list(), 15)
        return {"ok": True, "groups": [
            {"group_id": str(item["group_id"]), "group_name": str(item.get("group_name") or "")}
            for item in groups if isinstance(item, dict) and item.get("group_id")
        ]}
    except Exception:
        return {"ok": False, "msg": "读取群列表失败，请检查个人 QQ 连接"}


@app.post("/api/personal-qq/resolve/{key}/{action}")
async def personal_qq_resolve(key: str, action: str):
    try:
        await personal_qq.resolve_uncertain(key, action)
        return {"ok": True}
    except ValueError as exc:
        return JSONResponse({"ok": False, "msg": str(exc)}, status_code=400)


@app.get("/api/message-trace")
async def api_message_trace(limit: int = 50, bot_id: str = ""):
    """返回管理面板所需的近期消息投递追踪。"""
    return {
        "ok": True,
        "entries": await asyncio.to_thread(get_delivery_traces, limit, bot_id or None),
    }


@app.post("/api/maintenance/alert")
async def api_maintenance_alert(request: Request):
    data = await request.json()
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not isinstance(data.get("message"), str):
        return JSONResponse({"ok": False}, status_code=400)
    if not data["id"] or len(data["id"]) > 200 or len(data["message"]) > 500:
        return JSONResponse({"ok": False}, status_code=400)
    requested_bot_ids = data.get("bot_ids")
    if requested_bot_ids is not None:
        if (not isinstance(requested_bot_ids, list) or len(requested_bot_ids) > 100
                or any(not isinstance(bot_id, str) or not bot_id for bot_id in requested_bot_ids)):
            return JSONResponse({"ok": False}, status_code=400)
        from plugins.bot_profiles import filename
        try:
            for bot_id in requested_bot_ids:
                filename(bot_id)
        except ValueError:
            return JSONResponse({"ok": False}, status_code=400)
    from plugins.failure_notification import notify_maintenance_failure
    qq_by_id = {
        str(getattr(bot, "self_id", "") or getattr(bot, "id", "") or "").strip(): bot
        for bot in get_bots().values()
        if isinstance(bot, QQBot)
    }
    profile_owners = set()
    try:
        from plugins import bot_profiles
        from plugins.config import SETTINGS_FILE
        profile_owners.update(owner for owner in bot_profiles.ids(SETTINGS_FILE)
                              if owner != "unassigned")
    except Exception:
        pass
    inferred_bot_ids = _maintenance_alert_bot_ids(data["id"], profile_owners)
    if data["id"] == "file:bot_profiles/unassigned.json":
        targets = []
    elif inferred_bot_ids:
        # An account-scoped alert ID is authoritative even if an older panel
        # caller omits bot_ids or accidentally supplies a broadcast list.
        targets = [(bot_id, qq_by_id[bot_id]) for bot_id in inferred_bot_ids
                   if bot_id in qq_by_id]
    elif inferred_bot_ids == []:
        # A caller-supplied list must not turn an unresolved account alert into
        # a broadcast; wait until ownership can be established.
        targets = []
    elif requested_bot_ids is not None:
        targets = [(bot_id, qq_by_id[bot_id]) for bot_id in dict.fromkeys(requested_bot_ids)
                   if bot_id in qq_by_id]
    else:
        targets = list(qq_by_id.items())
    sent = 0
    for _, qq in targets:
        sent += await notify_maintenance_failure(qq, data["id"], data["message"])
    return {"ok": True, "sent": sent, "bots": len(targets)}
