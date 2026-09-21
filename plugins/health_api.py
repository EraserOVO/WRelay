from nonebot import get_app
from nonebot import get_bots
from fastapi import Request
from fastapi.responses import JSONResponse

from nonebot.adapters.discord import Bot as DiscordBot
from nonebot.adapters.qq import Bot as QQBot

from plugins.retry import get_retry_status
from plugins.message_trace import get_delivery_traces
from plugins.translation import get_translation_status
from plugins.json_io import load_json
from plugins.paths import data_path


# =====================================================
# bot 健康接口（供管理面板显示真实运行状态）
#
# get_bots() 只包含已成功完成网关握手的 bot：
# 握手失败（如启动时网络异常）返回空；连上后断线会被移除。
# 面板 /api/status 询问本接口，据此把徽章区分成
# "运行中（已连接）/ 运行中（未连接）/ 已停止"。
# =====================================================

app = get_app()
PANEL_AUTH_FILE = data_path("panel_auth.json")


def _panel_token():
    data = load_json(PANEL_AUTH_FILE, default={})
    token = data.get("token") if isinstance(data, dict) else ""
    return token if isinstance(token, str) and len(token) >= 32 else ""


@app.middleware("http")
async def bot_api_token_guard(request: Request, call_next):
    """仅允许持有本机面板令牌的调用访问 Bot 管理接口。"""
    if request.url.path.startswith("/api/"):
        token = _panel_token()
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
    return {
        "ok": True,
        "discord": any(
            isinstance(b, DiscordBot)
            for b in bots.values()
        ),
        "qq": qq_connected,
        "qq_bots": qq_bots,
        "retry": get_retry_status(),
        "translation": get_translation_status(),
    }


@app.get("/api/message-trace")
async def api_message_trace(limit: int = 50):
    """返回管理面板所需的近期消息投递追踪。"""
    return {
        "ok": True,
        "entries": get_delivery_traces(limit),
    }
