import asyncio

from nonebot import get_app
from nonebot import get_bots
from fastapi import Request
from fastapi.responses import JSONResponse

from nonebot.adapters.discord import Bot as DiscordBot
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
# 面板 /api/status 询问本接口，据此把徽章区分成
# "运行中（已连接）/ 运行中（未连接）/ 已停止"。
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
async def api_health():
    """返回 Discord/QQ 实时连接状态（供面板显示真实运行状态）"""
    bots = get_bots()
    qq_connected = any(isinstance(bot, QQBot) for bot in bots.values())
    qq_bots = [
        str(getattr(bot, "self_id", "") or getattr(bot, "id", "") or "").strip()
        for bot in bots.values()
        if isinstance(bot, QQBot)
    ]
    qq_bots = [bot_id for bot_id in qq_bots if bot_id]
    qq_bots.extend(personal_qq.connected_bots())
    return {
        "ok": True,
        "discord": any(
            isinstance(b, DiscordBot)
            for b in bots.values()
        ),
        "qq": qq_connected or personal_qq.any_connected(),
        "qq_bots": qq_bots,
        "personal_qq": personal_qq.connection_status(),
        "retry": get_retry_status(),
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
    from plugins.failure_notification import notify_maintenance_failure
    qq = next((bot for bot in get_bots().values() if isinstance(bot, QQBot)), None)
    if qq is None:
        return {"ok": False, "sent": 0}
    sent = await notify_maintenance_failure(qq, data["id"], data["message"])
    return {"ok": True, "sent": sent}
