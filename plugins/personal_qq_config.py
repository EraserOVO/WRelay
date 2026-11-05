"""Personal QQ connection configuration; tokens never enter settings/browser output."""
import json
import re

from plugins.json_io import file_revision, json_file_lock, update_json
from plugins.paths import data_path

CONFIG_FILE = data_path("personal_qq.json")
PREFIX = "personal:"


def numeric_id(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{4,19}", value) is not None


def is_personal(value):
    return str(value).startswith(PREFIX)


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {"connections": []}
    if not isinstance(data, dict) or not isinstance(data.get("connections"), list):
        raise ValueError("个人 QQ 配置格式无效，请检查 personal_qq.json")
    return {"connections": prepare_connections(data["connections"], {})}


def prepare_connections(items, previous):
    if not isinstance(items, list) or len(items) > 20:
        raise ValueError("个人 QQ 连接必须是列表，最多 20 个")
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("个人 QQ 连接格式错误")
        uin = item.get("uin", "")
        if not numeric_id(uin) or uin in seen:
            raise ValueError("QQ 号须为有效数字且不能重复")
        seen.add(uin)
        implementation = item.get("implementation", "napcat")
        if implementation not in ("napcat", "snowluma"):
            raise ValueError("协议端须为 NapCat 或 SnowLuma")
        enabled = item.get("enabled", True)
        name = item.get("name", "")
        token = item.get("token") or previous.get(uin, {}).get("token", "")
        if not isinstance(enabled, bool) or not isinstance(name, str) or len(name) > 80:
            raise ValueError("连接名称或启用状态无效")
        if not isinstance(token, str) or not 16 <= len(token) <= 256 or any(c.isspace() for c in token):
            raise ValueError("访问令牌须为 16–256 位且不含空白；留空仅可保留原令牌")
        result.append(dict(uin=uin, name=name.strip(), implementation=implementation,
                           enabled=enabled, token=token))
    return result


def public_config():
    with json_file_lock(CONFIG_FILE):
        data = load_config()
        revision = file_revision(CONFIG_FILE)
    return {"connections": [{k: v for k, v in item.items() if k != "token"}
                            for item in data["connections"]], "revision": revision}


def save_config(items, revision, bound_ids):
    def mutate(data):
        if not isinstance(revision, str) or revision != file_revision(CONFIG_FILE):
            raise ValueError("个人 QQ 配置已变化，请重新打开窗口")
        old = {item["uin"]: item for item in data.get("connections", [])}
        connections = prepare_connections(items, old)
        removed = set(old) - {item["uin"] for item in connections}
        if any(PREFIX + uin in bound_ids for uin in removed):
            raise ValueError("仍有接收群绑定待删除的个人 QQ，请先删除或改绑接收群")
        data.clear()
        data["connections"] = connections
    update_json(CONFIG_FILE, mutate, default={"connections": []})
    return public_config()


def enabled_connections():
    return {item["uin"]: item for item in load_config()["connections"] if item["enabled"]}
