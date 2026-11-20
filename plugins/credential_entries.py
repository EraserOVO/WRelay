"""Credential-list validation and runtime filtering without logging secrets."""
import json
import base64


def enabled_bots(bots):
    if not isinstance(bots, list):
        raise ValueError("账号配置格式无效")
    result = []
    for bot in bots:
        if not isinstance(bot, dict) or not isinstance(bot.get("enabled", True), bool):
            raise ValueError("账号启用状态无效")
        if bot.get("enabled", True):
            result.append({key: value for key, value in bot.items() if key != "enabled"})
    return result


def parse_youtube_keys(raw, legacy=""):
    if raw is None or raw == "":
        return [{"key": legacy, "enabled": True}] if legacy else []
    try:
        if raw.startswith("base64:"):
            raw = base64.b64decode(raw[7:], validate=True).decode("utf-8")
        items = json.loads(raw)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("YouTube 密钥列表格式无效") from exc
    if not isinstance(items, list) or len(items) > 20:
        raise ValueError("YouTube 密钥列表格式无效")
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get("key"), str)
                or not isinstance(item.get("enabled", True), bool)):
            raise ValueError("YouTube 密钥列表格式无效")
    return items
