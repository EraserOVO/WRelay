"""Desktop-only personal QQ configuration and proxies; credentials stay on disk."""
from fastapi import Request
from fastapi.responses import JSONResponse

from plugins import personal_qq_config as config
from plugins.json_io import run_in_thread_to_completion


def register_routes(app, panel):
    @app.get("/api/personal-qq/config")
    def get_config():
        try:
            data = config.public_config()
        except Exception:
            return {"ok": False, "msg": "无法读取个人 QQ 配置，请检查 personal_qq.json"}
        status = panel["bot_api_get"]("/api/personal-qq/status", timeout=2) or {}
        return dict(ok=True, **data, status=status.get("connections", []),
                    uncertain=status.get("uncertain", []),
                    reverse_url="ws://127.0.0.1:%d/onebot/v11/ws" % panel["BOT_PORT"])

    @app.post("/api/personal-qq/config")
    async def save_config(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("配置格式错误")
            bound_ids = await run_in_thread_to_completion(panel["_bound_qq_bot_ids"])
            data = await run_in_thread_to_completion(
                config.save_config, body.get("connections"), body.get("revision"), bound_ids)
            return dict(ok=True, **data)
        except ValueError as exc:
            return JSONResponse({"ok": False, "msg": str(exc)}, status_code=400)
        except Exception:
            return JSONResponse({"ok": False, "msg": "个人 QQ 配置保存失败"}, status_code=500)

    @app.get("/api/personal-qq/groups")
    def groups(uin: str):
        if not config.numeric_id(uin):
            return {"ok": False, "msg": "QQ 号无效"}
        return panel["bot_api_get"]("/api/personal-qq/groups?uin=" + uin, timeout=20) or {
            "ok": False, "msg": "机器人未运行或个人 QQ 未连接"}

    @app.post("/api/personal-qq/resolve/{key}/{action}")
    def resolve(key: str, action: str):
        import re
        if re.fullmatch(r"[a-f0-9]{64}", key) is None or action not in ("delivered", "retry"):
            return {"ok": False, "msg": "处理方式无效"}
        return panel["bot_api_post"]("/api/personal-qq/resolve/" + key + "/" + action) or {
            "ok": False, "msg": "机器人未运行或处理失败"}
