"""共享的 WRelay settings 模型、默认值和基础校验。

Bot 与管理面板都使用同一套默认字段和顶层结构。路由迁移仍由各自调用方
负责，因为 Bot 需要保持内存兼容，而面板保存时还要清理悬空引用。
"""

from copy import deepcopy


class SettingsModel(dict):
    """兼容旧 dict 调用方式的 settings 模型。"""

    @classmethod
    def default(cls, discord_channels=None, qq_group_openids=None):
        channels = [
            {"id": str(key), "name": value, "enabled": True}
            for key, value in (discord_channels or {}).items()
        ]
        groups = [
            {"openid": str(openid), "enabled": True, "remark": ""}
            for openid in (qq_group_openids or [])
        ]
        return cls({
            "qq_group_openids": groups,
            "qq_user_openids": [],
            "discord_channels": channels,
            "backfill_enabled": True,
            "backfill_limit": 10,
            "qq_appid": "",
            "qq_fwd_recency_limit": 1800,
            "qq_fwd_select_timeout": 60,
            "x_rss_sources": [],
            "x_rss_poll_interval": 300,
            "x_rsshub_base": "http://localhost:1200",
        })

    @classmethod
    def from_data(cls, data, discord_channels=None, qq_group_openids=None):
        """把磁盘数据转换为模型，并补齐缺失的旧版字段。

        已存在但类型错误的字段不在这里静默修复，由 validate_settings
        报错后交给调用方按安全默认值处理，避免掩盖损坏配置。
        """
        defaults = cls.default(discord_channels, qq_group_openids)
        if not isinstance(data, dict):
            return cls(deepcopy(defaults))
        result = cls(deepcopy(dict(data)))
        for key, value in defaults.items():
            if key not in result:
                result[key] = deepcopy(value)
        return result

    def to_dict(self):
        """返回不与模型共享可变子对象的普通 dict。"""
        return deepcopy(dict(self))


def validate_settings(
    data,
    max_forwarding_groups=10,
    require_nonempty_forwarding_groups=False,
    validate_forwarding_groups=False,
):
    """返回错误文本；返回 None 表示结构通过。"""
    if not isinstance(data, dict):
        return "配置必须是 JSON 对象"

    for key in ("qq_group_openids", "qq_user_openids", "discord_channels"):
        items = data.get(key)
        if not isinstance(items, list):
            return "%s 必须是列表" % key
        for item in items:
            if not isinstance(item, dict):
                return "%s 的每一项必须是对象" % key

    if "backfill_enabled" in data and not isinstance(data["backfill_enabled"], bool):
        return "backfill_enabled 必须是布尔值"
    if "backfill_limit" in data:
        limit = data["backfill_limit"]
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            return "backfill_limit 必须是正整数"
    for key, fields in (
        ("forwarding_groups", ("channels", "groups")),
        ("x_forwarding_groups", ("sources", "groups")),
    ):
        if not validate_forwarding_groups:
            continue
        if key not in data:
            continue
        groups = data[key]
        if not isinstance(groups, list):
            return "%s 必须是列表" % key
        if key == "forwarding_groups" and not groups and require_nonempty_forwarding_groups:
            return "forwarding_groups 至少保留 1 个转发组"
        if len(groups) > max_forwarding_groups:
            return "%s 最多 %d 个" % (key, max_forwarding_groups)
        ids = set()
        for group in groups:
            if not isinstance(group, dict):
                return "%s 的每一项必须是对象" % key
            group_id = str(group.get("id") or "").strip()
            if not group_id:
                return "%s 的每一项必须有非空 id" % key
            if group_id in ids:
                return "%s 存在重复 id: %s" % (key, group_id)
            ids.add(group_id)
            if not isinstance(group.get("name"), str):
                return "%s 的每一项 name 必须是字符串" % key
            if "enabled" in group and not isinstance(group["enabled"], bool):
                return "%s 的 enabled 必须是布尔值" % key
            for field in fields:
                if not isinstance(group.get(field), list):
                    return "%s 的每一项 %s 必须是列表" % (key, field)

    return None
