"""Portable per-account configuration, explicit target mapping and reviewed imports."""
import hashlib
import json
from copy import deepcopy
from fastapi import Request
from fastapi.responses import JSONResponse
from plugins import bot_profiles as store
from plugins.json_io import run_in_thread_to_completion
from plugins.json_io import ConcurrentWriteError


def portable(settings_file, owner):
    data = store.read(settings_file, owner)
    profile = store.strict_read(store.profile_path(settings_file, owner))
    used = {g for key in store.GROUP_KEYS for item in profile["config"].get(key, []) for g in item.get("groups", [])}
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if not any(word in k.lower() for word in ("secret", "token", "password", "api_key"))}
        return [clean(x) for x in value] if isinstance(value, list) else value
    return dict(format="wrelay-bot-export", version=1, origin_bot=owner,
        config=clean({k: v for k, v in profile["config"].items() if k not in (store.USER_KEY, *store.CARD_KEYS)}), targets=[{k: item.get(k, "") for k in ("openid", "name", "qq_id", "bot_id")}
        for item in data.get("qq_group_openids", []) if item.get("openid") in used])


def prepare(settings_file, owner, body):
    revision = store.revision(settings_file)
    document = body.get("document")
    if not isinstance(document, dict) or document.get("format") != "wrelay-bot-export" or type(document.get("version")) is not int or document.get("version") != 1:
        raise ValueError("请选择支持版本的 Bot 配置 JSON")
    incoming = deepcopy(document.get("config"))
    if isinstance(incoming, dict) and any(k in incoming for k in (store.USER_KEY, *store.CARD_KEYS)):
        raise ValueError("导入文件不能包含用户授权、定时计划或翻译设置")
    store.validate_profile(dict(format="wrelay-bot-profile", version=1, bot_id=owner, config=incoming), owner)
    mode = body.get("mode", "merge")
    if mode not in ("merge", "replace"):
        raise ValueError("导入方式无效")
    data = store.read(settings_file, owner)
    mapping = body.get("mapping", {})
    if not isinstance(mapping, dict):
        raise ValueError("群对应关系格式无效")
    available = {g["openid"] for g in data.get("qq_group_openids", []) if g.get("bot_id") == owner}
    if any(value and value not in available for value in mapping.values()):
        raise ValueError("只能对应到目标账号已登记的接收群，不能抢占其他账号绑定")
    warnings = []
    from plugins.config import _x_source_key
    if mode == "merge":
        canonical = {}
        for item in data.get("x_rss_sources", []):
            key = _x_source_key(str(item.get("source", "")))
            canonical.setdefault(key.casefold(), key)
        remap = {}
        for item in incoming.get("x_rss_sources", []):
            key = _x_source_key(str(item.get("source", "")))
            remap[key] = canonical.setdefault(key.casefold(), key)
        for group in incoming.get("x_forwarding_groups", []):
            group["sources"] = list(dict.fromkeys(remap.get(key, key) for key in group.get("sources", [])))
    disabled = {g["openid"]: g.get("name") or g["openid"] for g in data.get("qq_group_openids", [])
                if g.get("bot_id") == owner and not g.get("enabled")}
    warnings.extend("接收群“" + str(disabled[value]) + "”尚未启用，当前不会投递" for value in set(mapping.values()) if value in disabled)
    for key in store.GROUP_KEYS:
        for group in incoming.get(key, []):
            previous = list(group.get("groups", []))
            group["groups"] = list(dict.fromkeys(mapping[x] for x in previous if mapping.get(x)))
            if len([x for x in previous if mapping.get(x)]) != len(previous):
                group["enabled"] = False
                warnings.append(group["name"] + "：存在未对应群，已暂停")
    # IDs generated deterministically from reviewed content, so preview/commit agree.
    seed = hashlib.sha256(json.dumps([owner, document], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    for key in store.GROUP_KEYS:
        for i, group in enumerate(incoming.get(key, [])):
            if mode == "merge" or document.get("origin_bot") != owner:
                group["id"] = "import-" + seed + "-" + str(i)
    candidate = deepcopy(data)
    if mode == "replace":
        for key in store.OWNED:
            if key not in (store.USER_KEY, *store.CARD_KEYS):
                candidate.pop(key, None)
        candidate.update(incoming)
    else:
        for key in store.LIST_KEYS:
            current = candidate.setdefault(key, [])
            def identity(item):
                return _x_source_key(str(item.get("source", ""))).casefold() if key == "x_rss_sources" else str(item.get("id", ""))
            existing = {identity(x): x for x in current}
            for item in incoming.get(key, []):
                item_id = identity(item)
                if item_id in existing:
                    if existing[item_id] != item:
                        warnings.append(key + "：同标识配置保留目标值 " + item_id)
                    continue
                current.append(item)
                existing[item_id] = item
        warnings.append("合并模式保留目标账号的轮询、限速与补发设置")
    candidate["_profile_bot"] = owner
    store.validate_profile(dict(format="wrelay-bot-profile", version=1, bot_id=owner,
        config={key: candidate[key] for key in store.OWNED if key in candidate}), owner)
    warnings.append("来源访问权限需由当前 Discord / RSS 连接确认；导入不会复制凭据、授权或历史投递记录")
    digest = hashlib.sha256(json.dumps([candidate, revision], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return candidate, revision, digest, warnings


def register_routes(app, panel):
    @app.get("/api/bot-profile/export")
    def export(bot_id: str):
        try:
            panel["_ensure_bot_profiles"]()
            return portable(panel["SETTINGS_FILE"], bot_id)
        except (OSError, ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "msg": "该账号配置无法导出：" + str(exc)}, status_code=400)

    @app.post("/api/bot-profile/import")
    async def import_profile(request: Request, bot_id: str):
        try:
            raw = await request.body()
            if len(raw) > 5 * 1024 * 1024:
                raise ValueError("配置文件超过 5 MB")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("导入请求无效")
            def process():
                panel["_ensure_bot_profiles"]()
                candidate, revision, digest, warnings = prepare(panel["SETTINGS_FILE"], bot_id, body)
                if body.get("confirm") is True:
                    if body.get("revision") != revision or body.get("preview_hash") != digest:
                        raise ValueError("配置或导入内容已变化，请重新预览")
                    panel["save_settings"](candidate, expected_revision=revision)
                    return {"ok": True, "saved": True}
                return {"ok": True, "revision": revision, "preview_hash": digest, "warnings": warnings,
                        "counts": {key: len(candidate.get(key, [])) for key in store.LIST_KEYS}}
            return await run_in_thread_to_completion(process)
        except (ValueError, KeyError, TypeError, ConcurrentWriteError) as exc:
            return JSONResponse({"ok": False, "msg": str(exc)}, status_code=400)
