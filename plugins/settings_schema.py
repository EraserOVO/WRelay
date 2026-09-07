"""settings.json 的共享结构校验。

面板负责返回用户可读的错误，Bot 负责损坏配置时安全降级；两边共用这里
的结构规则，但不共用各自的迁移/内存归一化行为。
"""


def validate_settings(data, max_forwarding_groups=10,
                      require_nonempty_forwarding_groups=False,
                      validate_forwarding_groups=False):
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
