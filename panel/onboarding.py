"""配置向导的纯数据操作。复用既有实体和路由，不从发现记录自动授权。"""

from copy import deepcopy
import re
from urllib.parse import urlsplit, quote

PURPOSES = ("discord", "x", "bilibili", "qq")
DRAFT_FIELDS = ("purpose", "bot_id", "source_id", "source_kind", "source_value",
                "rsshub", "name", "step")


def clean_draft(body):
    if not isinstance(body, dict):
        raise ValueError("向导数据格式不正确")
    result = {}
    for key in DRAFT_FIELDS:
        value = body.get(key, "")
        if not isinstance(value, str) or len(value) > 1000:
            raise ValueError("向导字段格式不正确")
        result[key] = value.strip()
    if result["purpose"] not in PURPOSES:
        raise ValueError("请先选择一种用途")
    if result["step"] not in tuple(str(i) for i in range(7)):
        raise ValueError("向导步骤不正确")
    for key in ("groups", "users"):
        items = body.get(key, [])
        if not isinstance(items, list) or len(items) > 100 or any(
            not isinstance(item, str) or not item or len(item) > 200 for item in items
        ):
            raise ValueError("请选择有效的用户或群")
        result[key] = list(dict.fromkeys(items))
    return result


def rss_base(value):
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("RSSHub 地址应为完整的 http:// 或 https:// 地址，不含账号、查询参数或片段")
    return value


def source_spec(draft, settings):
    purpose = draft["purpose"]
    key = {"discord": "discord_channels", "x": "x_rss_sources", "bilibili": "bilibili_sources"}.get(purpose)
    if not key:
        return None, None, None
    selected = draft.get("source_id", "")
    if selected:
        for item in settings.get(key, []):
            ident = source_id(purpose, item)
            if ident == selected:
                copied = deepcopy(item)
                if purpose == "bilibili":
                    copied.pop("name", None)
                return key, selected, copied
        raise ValueError("所选来源已变更，请重新选择")
    value = draft.get("source_value", "").strip()
    name = draft.get("name", "").strip() or value
    if purpose == "discord":
        if not value.isdigit():
            raise ValueError("请扫描并选择一个可读取的 Discord 频道")
        item = {"id": value, "name": name}
    elif purpose == "x":
        value = value.lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
            raise ValueError("请填写 X 用户名，例如 example，不包含链接或 @")
        item = {"source": value, "name": name}
    else:
        kind = draft.get("source_kind") or "up"
        if kind not in ("up", "tag", "keyword") or not value:
            raise ValueError("请填写 UP 主 UID、标签或关键词")
        if kind == "up" and not value.isdigit():
            raise ValueError("UP 主 UID 应为数字，可从个人空间地址 space.bilibili.com/ 后获取")
        item = {"id": kind + ":" + value, "kind": kind, "value": value}
    ident = item.get("source", item.get("id"))
    # 新增输入与已登记的来源相同，复用原条目及筛选条件。
    for existing in settings.get(key, []):
        old_id = source_id(purpose, existing)
        if old_id.casefold() == ident.casefold():
            copied = deepcopy(existing)
            if purpose == "bilibili":
                copied.pop("name", None)
            return key, old_id, copied
    return key, ident, item


def source_id(purpose, item):
    if purpose == "x":
        return str(item.get("source") or "")
    if purpose == "bilibili":
        return str(item.get("id") or (item.get("kind", "up") + ":" + str(item.get("value") or item.get("uid") or "")))
    return str(item.get("id") or "")


def feed_url(draft, settings):
    _, _, item = source_spec(draft, settings)
    base = rss_base(draft.get("rsshub", ""))
    if draft["purpose"] == "x":
        source = item["source"]
        if source.startswith(("http://", "https://")):
            return source
        return base + "/twitter/user/" + quote(source.lstrip("@"), safe="")
    kind, value = item.get("kind", "up"), quote(str(item.get("value") or item.get("uid") or ""), safe="")
    return base + ("/bilibili/user/video/" + value if kind == "up" else "/bilibili/vsearch/" + value + "/pubdate/0")


def apply_draft(settings, draft, flow_id, known_bot_ids):
    """返回新配置，校验失败不改输入。仅启用用户明确选中的已注册群。"""
    data = deepcopy(settings)
    bot_id = draft["bot_id"]
    if bot_id not in known_bot_ids:
        raise ValueError("所选 QQ 机器人尚未配置完成")
    groups = draft["groups"]
    if not groups:
        raise ValueError("请至少选择一个接收群")
    entries = {str(item["openid"]): item for item in data.get("qq_group_openids", [])}
    for group in groups:
        item = entries.get(group)
        if item is None:
            raise ValueError("接收群尚未注册审核或已被移除")
        owner = str(item.get("bot_id") or "")
        if (owner and owner != bot_id) or (not owner and len(known_bot_ids) != 1):
            raise ValueError("请选择属于当前 QQ 机器人的群；旧版未绑定群请先在现有管理页确认归属")
        item["enabled"] = True
    purpose = draft["purpose"]
    if purpose == "qq":
        users = {str(item["openid"]): item for item in data.get("qq_user_openids", [])}
        if not draft["users"] or any(not users.get(user, {}).get("enabled") for user in draft["users"]):
            raise ValueError("请先明确授权至少一位私聊操作用户")
        # 私聊 relay 使用全局已启用群，不创建群聊互转规则。
        return data
    key, ident, source = source_spec(draft, data)
    source["enabled"] = True
    items = data.setdefault(key, [])
    for index, item in enumerate(items):
        if source_id(purpose, item) == ident:
            items[index] = source
            break
    else:
        items.append(source)
    if purpose in ("x", "bilibili"):
        data[purpose + "_rsshub_base"] = rss_base(draft["rsshub"])
    route_key = {"discord": "forwarding_groups", "x": "x_forwarding_groups", "bilibili": "bilibili_forwarding_groups"}[purpose]
    member_key = "channels" if purpose == "discord" else "sources"
    # X 路由使用归一化 source key（URL 来源可能与 source 原值不同）。
    if purpose == "x":
        from plugins.config import _x_source_key
        ident = _x_source_key(ident)
    routes = data.setdefault(route_key, [])
    for route in routes:
        if route.get("enabled", True) and set(route.get(member_key, [])) == {ident} and set(route.get("groups", [])) == set(groups):
            return data
    route_id = "setup_" + flow_id
    existing = next((item for item in routes if item.get("id") == route_id), None)
    route = {"id": route_id, "name": draft.get("name") or "首次转发", "enabled": True,
             member_key: [ident], "groups": list(groups)}
    if existing is not None:
        routes[routes.index(existing)] = route
    elif len(routes) >= 10:
        raise ValueError("转发组已达到 10 个，请在现有页面中整理后重试")
    else:
        routes.append(route)
    return data
