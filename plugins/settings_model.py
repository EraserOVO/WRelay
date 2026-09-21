"""共享的 WRelay settings 模型、默认值和基础校验。

Bot 与管理面板都使用同一套默认字段和顶层结构。路由迁移仍由各自调用方
负责，因为 Bot 需要保持内存兼容，而面板保存时还要清理悬空引用。
"""

from copy import deepcopy
from datetime import date, datetime
import re


def _legacy_scheduled_entry(scheduled):
    """把旧版单条定时消息转换为新版 messages 条目。"""
    content = scheduled.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    send_time = scheduled.get("send_time")
    if not isinstance(send_time, str) or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", send_time) is None:
        send_time = "09:00"
    anchor_date = scheduled.get("anchor_date")
    if not isinstance(anchor_date, str) or not anchor_date:
        anchor_date = ""
    schedule_mode = scheduled.get("schedule_mode")
    if schedule_mode == "weekly":
        weekdays = scheduled.get("weekdays")
        if not isinstance(weekdays, list):
            weekdays = [0]
        weekdays = [
            value for value in weekdays
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6
        ] or [0]
        return {
            "id": "legacy-1",
            "content": content,
            "schedule_type": "calendar",
            "calendar_type": "weekly",
            "weekdays": weekdays,
            "day_of_month": 1,
            "month": 1,
            "day": 1,
            "send_time": send_time,
        }
    if schedule_mode == "monthly":
        day = 1
        try:
            if anchor_date:
                day = date.fromisoformat(anchor_date).day
        except ValueError:
            pass
        return {
            "id": "legacy-1",
            "content": content,
            "schedule_type": "calendar",
            "calendar_type": "monthly",
            "weekdays": [0],
            "day_of_month": day,
            "month": 1,
            "day": 1,
            "send_time": send_time,
        }
    if scheduled.get("every_days") is not None:
        try:
            total_minutes = max(1, int(scheduled.get("every_days") or 1)) * 24 * 60
        except (TypeError, ValueError):
            total_minutes = 24 * 60
    else:
        try:
            total_minutes = max(1, int(scheduled.get("interval") or 3600) + 59) // 60
        except (TypeError, ValueError):
            total_minutes = 60
    every_days, remainder = divmod(total_minutes, 24 * 60)
    every_hours, every_minutes = divmod(remainder, 60)
    return {
        "id": "legacy-1",
        "content": content,
        "schedule_type": "interval",
        "interval_days": every_days,
        "interval_hours": every_hours,
        "interval_minutes": every_minutes,
        "anchor_at": (anchor_date + "T" + send_time) if anchor_date else "",
        "calendar_type": "weekly",
        "weekdays": [0],
        "day_of_month": 1,
        "month": 1,
        "day": 1,
        "send_time": send_time,
    }


def _legacy_qq_forwarding_groups(data, default_groups=None):
    """为旧配置生成独立的 QQ→QQ 转发组。

    旧版 QQ→QQ 复用 Discord 转发组；首次读取没有
    ``qq_forwarding_groups`` 的配置时，分别复制 Discord/X 转发组中的群成员
    关系，保留“同组可互转”的既有关系。生成后 QQ→QQ 运行时不再读取这两套组。
    """
    result = []
    seen_ids = set()
    for source_key, prefix in (
        ("forwarding_groups", "discord"),
        ("x_forwarding_groups", "x"),
    ):
        groups = data.get(source_key)
        if not isinstance(groups, list):
            continue
        for index, item in enumerate(groups, start=1):
            if not isinstance(item, dict):
                continue
            members = []
            for value in item.get("groups") or []:
                value = str(value).strip()
                if value and value not in members:
                    members.append(value)
            if not members:
                continue
            old_id = str(item.get("id") or index).strip()
            group_id = "qqfg_legacy_%s_%s" % (prefix, old_id)
            if group_id in seen_ids:
                continue
            seen_ids.add(group_id)
            result.append({
                "id": group_id,
                "name": str(item.get("name") or "转发组%d" % index).strip(),
                "enabled": item.get("enabled", True) is not False,
                "groups": members,
            })
    if result:
        return result
    return [{
        "id": "qq_default",
        "name": "转发组1",
        "enabled": True,
        "groups": [str(value) for value in (default_groups or []) if str(value).strip()],
    }]


class SettingsModel(dict):
    """兼容旧 dict 调用方式的 settings 模型。"""

    @classmethod
    def default(cls, discord_channels=None, qq_group_openids=None):
        channels = [
            {
                "id": str(key),
                "name": value,
                "enabled": True,
                "translation_show_datetime": False,
            }
            for key, value in (discord_channels or {}).items()
        ]
        groups = [
            {"openid": str(openid), "enabled": True, "remark": "", "bot_id": ""}
            for openid in (qq_group_openids or [])
        ]
        return cls({
            "qq_group_openids": groups,
            "qq_user_openids": [],
            "qq_forwarding_groups": [{
                "id": "qq_default",
                "name": "转发组1",
                "enabled": True,
                "groups": [str(group.get("openid")) for group in groups if group.get("openid")],
            }],
            "discord_channels": channels,
            "qq_c2c_relay_filter": {
                "filter_message_types": ["文字", "图片", "语音", "文件", "链接", "表情"],
            },
            "x_c2c_relay_filter": {
                "filter_message_types": ["文字", "图片", "语音", "文件", "链接", "表情"],
            },
            "backfill_enabled": True,
            "backfill_limit": 10,
            "failure_notification": {
                "enabled": False,
                "cooldown_seconds": 900,
            },
            "qq_appid": "",
            "qq_fwd_recency_limit": 1800,
            "qq_fwd_select_timeout": 60,
            "x_rss_sources": [],
            "x_rss_poll_interval": 300,
            "x_rsshub_base": "http://localhost:1200",
            "scheduled_message": {
                "enabled": False,
                "content": "",
                "interval": 3600,
                "target_groups": [],
                "schedule_mode": "days",
                "every_days": 1,
                "every_weeks": 1,
                "every_months": 1,
                "weekdays": [0],
                "send_time": "09:00",
                "anchor_date": "",
                "messages": [],
            },
            # 翻译服务路由：是否翻译及是否保留原文由每个来源条目决定。
            "translation": {
                "enabled": False,
                "provider": "tencent_tmt",
                "fallback_provider": "ai",
                "target_language": "zh",
                "append_original": True,
                "only_non_chinese": True,
            },
            # X RSS 沿用 translation 的服务路由；保留该字段仅为旧配置兼容。
            "x_translation": {
                "enabled": False,
                "provider": "tencent_tmt",
                "fallback_provider": "ai",
                "target_language": "zh",
                "append_original": True,
                "only_non_chinese": True,
            },
            "ai": {
                "base_url": "",
                "model": "",
                "api_key_env": "WRELAY_AI_API_KEY",
                "timeout_seconds": 25,
                "max_output_tokens": 800,
            },
            # AI API 配置档案：保留上面的 ai 字段作为旧版兼容镜像，
            # 当前请求由 ai_active_profile 指向的档案决定。
            "ai_profiles": [],
            "ai_active_profile": "",
            "tencent_tmt": {
                "secret_id_env": "WRELAY_TENCENT_SECRET_ID",
                "secret_key_env": "WRELAY_TENCENT_SECRET_KEY",
                "region": "ap-guangzhou",
                "project_id": 0,
            },
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
        qq_forwarding_groups_missing = "qq_forwarding_groups" not in result
        # AI 对话不属于 WRelay 的功能范围。兼容曾保存过该字段的配置：
        # 读取时忽略，后续由管理面板保存时自然从磁盘配置移除。
        result.pop("ai_chat", None)
        # 服务级 enabled 从未参与实际调用；链路级 translation.enabled /
        # x_translation.enabled 才是唯一开关。读取时移除遗留字段，避免
        # 用户误以为它能关闭某个服务，后续保存时会自然完成迁移。
        for service_name in ("ai", "tencent_tmt"):
            service = result.get(service_name)
            if isinstance(service, dict):
                service.pop("enabled", None)
        for key, value in defaults.items():
            if key not in result:
                result[key] = deepcopy(value)
        # Discord/X 共用服务路由。将旧的 x_translation 路由字段同步为
        # translation 的值，保留字段本身以兼容旧配置格式。
        shared_translation = result.get("translation")
        legacy_x_translation = result.get("x_translation")
        if isinstance(shared_translation, dict) and isinstance(legacy_x_translation, dict):
            for key in (
                "provider",
                "fallback_provider",
                "target_language",
                "append_original",
                "only_non_chinese",
            ):
                if key in shared_translation:
                    legacy_x_translation[key] = deepcopy(shared_translation[key])

        # 自动翻译原为 Discord/X 各一组全局开关。迁移到来源条目时，
        # 未单独设置过的频道/账号继承共享 translation 的旧值，避免升级后转发内容突变。
        for item_key, config_key in (("discord_channels", "translation"), ("x_rss_sources", "translation")):
            translation = result.get(config_key)
            if not isinstance(translation, dict):
                continue
            legacy_enabled = translation.get("enabled") is True
            legacy_append_original = translation.get("append_original") is not False
            for item in result.get(item_key, []):
                if isinstance(item, dict):
                    item.setdefault("translation_enabled", legacy_enabled)
                    item.setdefault("translation_append_original", legacy_append_original)
                    item.setdefault("translation_show_datetime", False)
        if qq_forwarding_groups_missing:
            result["qq_forwarding_groups"] = _legacy_qq_forwarding_groups(
                result,
                [
                    item.get("openid")
                    for item in result.get("qq_group_openids", [])
                    if isinstance(item, dict) and item.get("openid") and item.get("enabled")
                ],
            )
        # bot_id 为空表示沿用第一个已连接 QQ Bot。旧配置补上空值，
        # 以保持原有单 Bot 投递行为不变。
        for item in result.get("qq_group_openids", []):
            if isinstance(item, dict):
                item.setdefault("bot_id", "")
        # 定时消息配置曾只有 enabled/content/interval 三个字段；新增
        # 周期字段时只补缺失的嵌套字段，旧配置安全迁移为每日计划。
        scheduled = result.get("scheduled_message")
        if isinstance(scheduled, dict):
            if "messages" not in scheduled:
                legacy = _legacy_scheduled_entry(scheduled)
                scheduled["messages"] = [legacy] if legacy else []
            for key, value in defaults["scheduled_message"].items():
                if key not in scheduled:
                    scheduled[key] = deepcopy(value)
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
            for translation_key in (
                "translation_enabled",
                "translation_append_original",
                "translation_show_datetime",
            ):
                if translation_key in item and not isinstance(item[translation_key], bool):
                    return "%s.%s 必须是布尔值" % (key, translation_key)
            if key == "qq_group_openids" and (
                not isinstance(item.get("bot_id", ""), str)
            ):
                return "qq_group_openids.bot_id 必须是字符串"

    x_sources = data.get("x_rss_sources")
    if not isinstance(x_sources, list):
        return "x_rss_sources 必须是列表"
    for item in x_sources:
        if not isinstance(item, dict):
            return "x_rss_sources 的每一项必须是对象"
        for translation_key in (
            "translation_enabled",
            "translation_append_original",
            "translation_show_datetime",
        ):
            if translation_key in item and not isinstance(item[translation_key], bool):
                return "x_rss_sources.%s 必须是布尔值" % translation_key
        if "filter_keywords" in item:
            keywords = item["filter_keywords"]
            if not isinstance(keywords, list) or any(not isinstance(keyword, str) for keyword in keywords):
                return "x_rss_sources.filter_keywords 必须是字符串列表"
        if "filter_post_types" in item:
            post_types = item["filter_post_types"]
            allowed_post_types = {"原创", "转发", "引用"}
            if not isinstance(post_types, list) or any(
                not isinstance(post_type, str) or post_type not in allowed_post_types
                for post_type in post_types
            ):
                return "x_rss_sources.filter_post_types 包含不支持的类型"

    relay_filter = data.get("qq_c2c_relay_filter")
    if not isinstance(relay_filter, dict):
        return "qq_c2c_relay_filter 必须是对象"
    values = relay_filter.get("filter_message_types")
    if not isinstance(values, list):
        return "qq_c2c_relay_filter.filter_message_types 必须是列表"
    allowed_types = {"文字", "图片", "语音", "文件", "链接", "表情"}
    if any(not isinstance(value, str) or value not in allowed_types for value in values):
        return "qq_c2c_relay_filter.filter_message_types 包含不支持的类型"

    x_relay_filter = data.get("x_c2c_relay_filter")
    if not isinstance(x_relay_filter, dict):
        return "x_c2c_relay_filter 必须是对象"
    x_values = x_relay_filter.get("filter_message_types")
    if not isinstance(x_values, list):
        return "x_c2c_relay_filter.filter_message_types 必须是列表"
    if any(not isinstance(value, str) or value not in allowed_types for value in x_values):
        return "x_c2c_relay_filter.filter_message_types 包含不支持的类型"

    if "backfill_enabled" in data and not isinstance(data["backfill_enabled"], bool):
        return "backfill_enabled 必须是布尔值"
    if "backfill_limit" in data:
        limit = data["backfill_limit"]
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            return "backfill_limit 必须是正整数"

    failure_notification = data.get("failure_notification")
    if not isinstance(failure_notification, dict):
        return "failure_notification 必须是对象"
    if not isinstance(failure_notification.get("enabled"), bool):
        return "failure_notification.enabled 必须是布尔值"
    cooldown = failure_notification.get("cooldown_seconds")
    if (
        not isinstance(cooldown, int)
        or isinstance(cooldown, bool)
        or not 60 <= cooldown <= 86400
    ):
        return "failure_notification.cooldown_seconds 必须是 60 到 86400 之间的整数"

    scheduled = data.get("scheduled_message")
    if not isinstance(scheduled, dict):
        return "scheduled_message 必须是对象"
    if not isinstance(scheduled.get("enabled"), bool):
        return "scheduled_message.enabled 必须是布尔值"
    if not isinstance(scheduled.get("content"), str):
        return "scheduled_message.content 必须是字符串"
    if len(scheduled["content"]) > 20000:
        return "scheduled_message.content 不能超过 20000 个字符"
    target_groups = scheduled.get("target_groups")
    if not isinstance(target_groups, list):
        return "scheduled_message.target_groups 必须是列表"
    for group_openid in target_groups:
        if not isinstance(group_openid, str) or not group_openid.strip():
            return "scheduled_message.target_groups 的每一项必须是非空字符串"
    messages = scheduled.get("messages")
    if not isinstance(messages, list):
        return "scheduled_message.messages 必须是列表"
    for index, message in enumerate(messages, start=1):
        prefix = "scheduled_message.messages[%d]" % index
        if not isinstance(message, dict):
            return prefix + " 必须是对象"
        if "id" in message and (
            not isinstance(message["id"], str) or not message["id"].strip()
        ):
            return prefix + ".id 必须是非空字符串"
        content = message.get("content")
        if not isinstance(content, str):
            return prefix + ".content 必须是字符串"
        if not content.strip():
            return prefix + ".content 不能为空"
        if len(content) > 20000:
            return prefix + ".content 不能超过 20000 个字符"
        schedule_type = message.get("schedule_type")
        if schedule_type not in ("interval", "calendar"):
            return prefix + ".schedule_type 必须是 interval 或 calendar"
        if schedule_type == "interval":
            for key, maximum in (("interval_days", 3650), ("interval_hours", 23), ("interval_minutes", 59)):
                value = message.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > maximum:
                    return "%s.%s 数值无效" % (prefix, key)
            if (
                message.get("interval_days", 0) == 0
                and message.get("interval_hours", 0) == 0
                and message.get("interval_minutes", 0) == 0
            ):
                return prefix + " 的间隔至少需要 1 分钟"
            anchor_at = message.get("anchor_at", "")
            if not isinstance(anchor_at, str):
                return prefix + ".anchor_at 必须是日期时间字符串"
            if anchor_at:
                try:
                    datetime.strptime(anchor_at, "%Y-%m-%dT%H:%M")
                except ValueError:
                    return prefix + ".anchor_at 必须是 YYYY-MM-DD HH:MM 格式"
        else:
            calendar_type = message.get("calendar_type")
            if calendar_type not in ("weekly", "monthly", "yearly"):
                return prefix + ".calendar_type 必须是 weekly、monthly 或 yearly"
            send_time = message.get("send_time")
            if (
                not isinstance(send_time, str)
                or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", send_time) is None
            ):
                return prefix + ".send_time 必须是 HH:MM 格式"
            if calendar_type == "weekly":
                weekdays = message.get("weekdays")
                if not isinstance(weekdays, list) or not weekdays:
                    return prefix + ".weekdays 必须至少选择一天"
                for weekday in weekdays:
                    if not isinstance(weekday, int) or isinstance(weekday, bool) or weekday < 0 or weekday > 6:
                        return prefix + ".weekdays 必须是 0 到 6 之间的整数"
                if len(set(weekdays)) != len(weekdays):
                    return prefix + ".weekdays 不能有重复值"
            elif calendar_type == "monthly":
                day_of_month = message.get("day_of_month")
                if not isinstance(day_of_month, int) or isinstance(day_of_month, bool) or not 1 <= day_of_month <= 31:
                    return prefix + ".day_of_month 必须是 1 到 31 之间的整数"
            else:
                month = message.get("month")
                day = message.get("day")
                if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
                    return prefix + ".month 必须是 1 到 12 之间的整数"
                if not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 31:
                    return prefix + ".day 必须是 1 到 31 之间的整数"
    for name in ("translation", "x_translation"):
        translation = data.get(name)
        if not isinstance(translation, dict):
            return "%s 必须是对象" % name
        if not isinstance(translation.get("enabled"), bool):
            return "%s.enabled 必须是布尔值" % name
        if translation.get("provider") not in ("tencent_tmt", "ai"):
            return "%s.provider 不支持" % name
        if translation.get("fallback_provider") not in ("", "tencent_tmt", "ai"):
            return "%s.fallback_provider 不支持" % name
        if not isinstance(translation.get("target_language"), str):
            return "%s.target_language 必须是字符串" % name
        for key in ("append_original", "only_non_chinese"):
            if not isinstance(translation.get(key), bool):
                return "%s.%s 必须是布尔值" % (name, key)

    ai = data.get("ai")
    if not isinstance(ai, dict):
        return "ai 必须是对象"
    for key in ("base_url", "model", "api_key_env"):
        if not isinstance(ai.get(key), str):
            return "ai.%s 必须是字符串" % key
    for key in ("timeout_seconds", "max_output_tokens"):
        value = ai.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return "ai.%s 必须是正整数" % key

    ai_profiles = data.get("ai_profiles", [])
    if not isinstance(ai_profiles, list):
        return "ai_profiles 必须是列表"
    if len(ai_profiles) > 20:
        return "ai_profiles 最多保留 20 个配置"
    profile_ids = set()
    for index, profile in enumerate(ai_profiles):
        prefix = "ai_profiles[%d]" % index
        if not isinstance(profile, dict):
            return "%s 必须是对象" % prefix
        for key in ("id", "name", "base_url", "model", "api_key_env"):
            if not isinstance(profile.get(key), str):
                return "%s.%s 必须是字符串" % (prefix, key)
        profile_id = profile.get("id", "").strip()
        if not profile_id:
            return "%s.id 不能为空" % prefix
        if profile_id in profile_ids:
            return "%s.id 不能重复" % prefix
        profile_ids.add(profile_id)
        if not profile.get("name", "").strip():
            return "%s.name 不能为空" % prefix
        for key in ("timeout_seconds", "max_output_tokens"):
            value = profile.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                return "%s.%s 必须是正整数" % (prefix, key)
    ai_active_profile = data.get("ai_active_profile", "")
    if not isinstance(ai_active_profile, str):
        return "ai_active_profile 必须是字符串"
    if ai_active_profile and ai_active_profile not in profile_ids:
        return "ai_active_profile 不存在"

    tmt = data.get("tencent_tmt")
    if not isinstance(tmt, dict):
        return "tencent_tmt 必须是对象"
    for key in ("secret_id_env", "secret_key_env", "region"):
        if not isinstance(tmt.get(key), str):
            return "tencent_tmt.%s 必须是字符串" % key
    if not isinstance(tmt.get("project_id"), int) or isinstance(tmt.get("project_id"), bool):
        return "tencent_tmt.project_id 必须是整数"

    for key, fields in (
        ("forwarding_groups", ("channels", "groups")),
        ("x_forwarding_groups", ("sources", "groups")),
        ("qq_forwarding_groups", ("groups",)),
    ):
        if not validate_forwarding_groups:
            continue
        if key not in data:
            continue
        groups = data[key]
        if not isinstance(groups, list):
            return "%s 必须是列表" % key
        if key == "forwarding_groups" and not groups and require_nonempty_forwarding_groups:
            return "%s 至少保留 1 个转发组" % key
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
