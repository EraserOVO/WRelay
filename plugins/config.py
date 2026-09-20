import os
import re

# Discord频道列表（可分发版本：不内置默认频道；
# 首次运行时自动生成空配置，由用户在管理面板添加）

DISCORD_CHANNELS = {}


# QQ 接收群（默认空；实际启用的群以 data/settings.json 为准）
#
# 官方机器人使用 group_openid，不是群号。
# group_openid 无法从群号推算，需要机器人入群后获取：
# 1. 启动 bot，把机器人拉进目标群（或在群里发一条消息）
# 2. 查看控制台输出的 "[QQ group_openid] ..."，
#    或读取 data/qq_group_openids.json
# 3. 在管理面板中启用该 openid
#
QQ_GROUP_OPENIDS = []


# =====================================================
# Discord Bot Token
#
# 不在此硬编码，唯一来源是 .env.prod 的 DISCORD_BOTS，
# 运行时通过 get_discord_token() 从 NoneBot 配置读取。
# =====================================================

def get_discord_token():
    """读取 .env.prod 中 DISCORD_BOTS 的 Discord Bot Token"""
    try:
        from nonebot import get_driver
        bots = getattr(
            get_driver().config,
            "discord_bots",
            None
        )
        if bots:
            first = bots[0]
            token = getattr(
                first,
                "token",
                None
            )
            if not token and isinstance(first, dict):
                token = first.get("token")
            if token:
                return token
    except Exception:
        pass
    return ""


# =====================================================
# 运行时配置
#
# 管理面板写入 data/settings.json，实时控制：
#   - 哪些 QQ 群接收转发（qq_group_openids）
#   - 哪些 Discord 频道参与转发（discord_channels）
# 设置文件不存在时初始化默认值；文件损坏/结构无效时内存降级为
# 默认值并原样保留文件，绝不用默认值覆盖真实配置。
# =====================================================

from plugins.json_io import (
    load_json,
    atomic_write_json,
    file_revision,
    ConcurrentWriteError,
)
from plugins.settings_model import SettingsModel, validate_settings
from plugins.paths import data_path

SETTINGS_FILE = data_path("settings.json")

# 转发组：固定 id + 可重命名 name + channels[]/groups[] 集合。
# 旧配置缺省时内存归一化为默认转发组（channels/groups = 当前全局启用项），
# 路由结果与旧的"全局启用即全量转发"完全一致；面板保存后固化落盘。
FORWARDING_GROUP_DEFAULT_ID = "default"
QQ_FORWARDING_GROUP_DEFAULT_ID = "qq_default"


def _default_settings():
    return SettingsModel.default(
        DISCORD_CHANNELS,
        QQ_GROUP_OPENIDS,
    ).to_dict()


def _setting_object(name):
    """读取一个设置对象的副本，避免调用方修改内存配置缓存。"""
    value = _load_settings().get(name)
    return dict(value) if isinstance(value, dict) else {}


def get_translation_config():
    return _setting_object("translation")


def get_x_translation_config():
    """返回 X RSS 专属翻译服务路由。"""
    return _setting_object("x_translation")


def _translation_config_for_item(config_name, items_name, item_id):
    """将服务路由与单个来源的翻译开关合并为调用配置。"""
    data = _load_settings()
    config = dict(data.get(config_name) or {})
    for item in data.get(items_name) or []:
        if not isinstance(item, dict):
            continue
        candidate = item.get("source") if items_name == "x_rss_sources" else item.get("id")
        candidate_id = _x_source_key(str(candidate or "")) if items_name == "x_rss_sources" else str(candidate or "")
        if candidate_id == str(item_id):
            config["enabled"] = item.get("translation_enabled") is True
            config["append_original"] = item.get("translation_append_original") is not False
            config["show_datetime"] = item.get("translation_show_datetime") is True
            return config
    config["enabled"] = False
    config["append_original"] = True
    config["show_datetime"] = False
    return config


def get_channel_translation_config(channel_id):
    return _translation_config_for_item("translation", "discord_channels", channel_id)


def get_x_source_translation_config(source_key):
    return _translation_config_for_item("x_translation", "x_rss_sources", source_key)


def get_x_source_filter(source_key):
    """返回指定 X RSS 账号的消息筛选配置。"""
    data = _load_settings()
    for item in data.get("x_rss_sources") or []:
        if not isinstance(item, dict):
            continue
        if _x_source_key(str(item.get("source") or "")) != str(source_key):
            continue
        return {
            "filter_keywords": [
                str(keyword).strip()
                for keyword in (item.get("filter_keywords") or [])
                if str(keyword).strip()
            ],
            "filter_post_types": item.get("filter_post_types"),
        }
    return {"filter_keywords": [], "filter_post_types": None}


def get_ai_config():
    data = _load_settings()
    config = dict(data.get("ai") or {})
    profiles = data.get("ai_profiles")
    active_id = str(data.get("ai_active_profile") or "").strip()
    selected = None
    if isinstance(profiles, list):
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            if active_id and str(profile.get("id") or "") == active_id:
                selected = profile
                break
        if selected is None and profiles:
            selected = profiles[0] if isinstance(profiles[0], dict) else None
    if selected is not None:
        for key in (
            "base_url",
            "model",
            "api_key_env",
            "timeout_seconds",
            "max_output_tokens",
        ):
            if key in selected:
                config[key] = selected[key]
        config["_profile_id"] = str(selected.get("id") or "")
        config["_profile_name"] = str(selected.get("name") or "")
    return config


def get_tencent_tmt_config():
    return _setting_object("tencent_tmt")


def _ensure_settings_file():
    """settings.json 完全不存在时用默认值初始化（首跑引导）。

    文件存在但损坏/结构无效时绝不写盘：用默认值覆盖会清空真实
    配置（群/频道/白名单）。此处只保留原文件，由 _load_settings
    记错误日志并内存降级，管理员修复文件后 mtime 变化自动恢复。"""
    if os.path.exists(SETTINGS_FILE):
        return
    try:
        atomic_write_json(
            SETTINGS_FILE,
            _default_settings(),
            indent=2,
            expected_revision="missing",
        )
    except ConcurrentWriteError:
        # 另一个进程可能已在本次初始化期间创建文件；只要目标现在
        # 存在，就保留对方的配置，不用默认值覆盖。
        if not os.path.exists(SETTINGS_FILE):
            raise


# settings.json 高频读取（每条消息、每个群都会调用），
# 用内存缓存 + 文件版本指纹校验，避免每次重新解析 JSON；
# 版本指纹同时包含 mtime_ns、ctime_ns 和 size，避免同一秒内或
# 原地改写时缓存误判为新鲜。
_settings_cache = None
_settings_cache_mtime = None
_settings_cache_size = None
_settings_cache_revision = None


def _load_settings():

    global _settings_cache, _settings_cache_mtime
    global _settings_cache_size, _settings_cache_revision

    if not os.path.exists(SETTINGS_FILE):
        _ensure_settings_file()

    try:
        st = os.stat(SETTINGS_FILE)
        mtime, size = st.st_mtime_ns, st.st_size
    except OSError:
        mtime = size = None
    revision = file_revision(SETTINGS_FILE)

    if (
        _settings_cache is not None
        and mtime == _settings_cache_mtime
        and size == _settings_cache_size
        and revision == _settings_cache_revision
    ):
        return _settings_cache

    raw_data = load_json(
        SETTINGS_FILE,
        default=None
    )

    if isinstance(raw_data, dict):
        data = SettingsModel.from_data(
            raw_data,
            DISCORD_CHANNELS,
            QQ_GROUP_OPENIDS,
        )
        validation_error = validate_settings(data)
    else:
        data = None
        validation_error = validate_settings(raw_data)
    if validation_error:
        # 文件损坏/结构无效：绝不用默认值覆盖原文件（会清空真实配置），
        # 记错误日志并内存降级为安全默认值，原文件原样保留；
        # 修复后 mtime 变化会自动重新加载
        # 延迟导入：面板进程也会导入本模块，避免模块级引入 nonebot
        from nonebot import logger
        logger.error(
            "settings.json 损坏或结构无效，已临时改用内存默认配置"
            "（原文件未改动，修复后自动恢复）: {}",
            "{} ({})".format(SETTINGS_FILE, validation_error),
        )
        data = None
    if data is None:
        _settings_cache = _default_settings()
    else:
        _settings_cache = data
    # 转发组内存归一化（不写盘）：旧配置缺省/损坏时生成默认组，
    # 保证路由函数始终拿到合法结构；与下方 qq_user_openids 的内存
    # 补齐同理，绝不把归一化结果当作面板配置写回文件
    _ensure_forwarding_groups(_settings_cache)
    _ensure_qq_forwarding_groups(_settings_cache)
    _settings_cache_mtime = mtime
    _settings_cache_size = size
    _settings_cache_revision = revision

    return _settings_cache


def get_active_channels():
    """返回当前启用的 Discord 频道 {频道ID: 名称}

    设置文件中存在频道列表时，按勾选状态返回（全部未勾选 = 空 dict，
    即暂停转发，不再回退默认值）；仅当列表缺失或损坏时才回退默认值。"""
    data = _load_settings()
    items = data.get("discord_channels")
    if isinstance(items, list):
        active = {}
        for item in items:
            if item.get("enabled") and item.get("id"):
                active[str(item["id"])] = str(item.get("name") or "")
        return active
    return dict(DISCORD_CHANNELS)


def get_active_groups():
    """返回当前启用的 QQ 群 openid 列表

    设置文件中存在群列表时，按勾选状态返回（全部未勾选 = 空 list，
    即暂停转发，不再回退默认值）；仅当列表缺失或损坏时才回退默认值。"""
    data = _load_settings()
    items = data.get("qq_group_openids")
    if isinstance(items, list):
        active = []
        for item in items:
            if item.get("enabled") and item.get("openid"):
                active.append(str(item["openid"]))
        return active
    return list(QQ_GROUP_OPENIDS)


# =====================================================
# 转发组路由
#
# forwarding_groups 只负责"当前路由"，不管理去重游标：
# 从转发组取消某个群只影响该频道此后转发到哪些群，
# 不触碰 data/discord_last.json 里该频道×群已有的历史游标
# （游标结构/生命周期由 plugins/dedup.py + plugins/history.py
# 管理，本模块不参与）。
#
# 实际发送必须同时满足（两级开关）：
#   - 频道全局 enabled（discord_channels 条目的 enabled）
#   - 群全局 enabled（qq_group_openids 条目的 enabled）
#   - 频道与群同属于至少一个转发组
# =====================================================


def _enabled_channel_ids(data):
    """settings dict 内全局启用的频道 id 列表（转发组归一化/路由共用）"""
    items = data.get("discord_channels")
    if isinstance(items, list):
        return [
            str(item["id"])
            for item in items
            if item.get("enabled") and item.get("id")
        ]
    return [str(k) for k in DISCORD_CHANNELS]


def _enabled_group_openids(data):
    """settings dict 内全局启用的群 openid 列表（转发组归一化/路由共用）"""
    items = data.get("qq_group_openids")
    if isinstance(items, list):
        return [
            str(item["openid"])
            for item in items
            if item.get("enabled") and item.get("openid")
        ]
    return [str(o) for o in QQ_GROUP_OPENIDS]


def _ensure_forwarding_groups(data):
    """转发组内存归一化（只改内存 data，不写盘）。

    - 结构合法 → 清洗保留（id 去重、字段转 str、name 缺省回退 id）
    - 缺省/空/结构无效（旧配置升级）→ 生成默认转发组（当前全部启用频道/群），
      与旧的"全局启用即全量转发"行为一致。
    - 已存在的转发组原样保留（含普通 id 组），不合并、不重建、不删除。
    - 旧 is_test 属性废弃：读取后从条目中移除（不再有测试组概念）。"""
    groups = data.get("forwarding_groups")
    if not isinstance(groups, list):
        groups = []

    # 旧 is_test 属性废弃：一律从频道/群条目移除（不并入任何"测试组"）
    for item in data.get("qq_group_openids", []):
        if isinstance(item, dict):
            item.pop("is_test", None)
    for item in data.get("discord_channels", []):
        if isinstance(item, dict):
            item.pop("is_test", None)

    normalized = []
    seen_ids = set()
    for item in groups:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("id") or "").strip()
        if not gid or gid in seen_ids:
            continue
        seen_ids.add(gid)
        normalized.append({
            "id": gid,
            "name": str(item.get("name") or "").strip() or gid,
            "enabled": item.get("enabled", True) is not False,
            "channels": [str(c) for c in (item.get("channels") or []) if c],
            "groups": [str(o) for o in (item.get("groups") or []) if o],
        })

    if not normalized:
        normalized = [{
            "id": FORWARDING_GROUP_DEFAULT_ID,
            "name": "转发组1",
            "enabled": True,
            "channels": _enabled_channel_ids(data),
            "groups": _enabled_group_openids(data),
        }]

    data["forwarding_groups"] = normalized
    return normalized


def _ensure_qq_forwarding_groups(data):
    """QQ→QQ 独立转发组归一化（只改内存 data，不写盘）。"""
    raw_groups = data.get("qq_forwarding_groups")
    # 空列表是用户明确删除全部 QQ→QQ 转发组后的有效配置，不能再回退到旧版迁移。
    if isinstance(raw_groups, list) and not raw_groups:
        data["qq_forwarding_groups"] = []
        return []
    groups = raw_groups
    if not isinstance(groups, list):
        groups = []
    known_groups = {
        str(item.get("openid"))
        for item in data.get("qq_group_openids", [])
        if isinstance(item, dict) and item.get("openid")
    }
    normalized = []
    seen_ids = set()
    for item in groups:
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("id") or "").strip()
        if not group_id or group_id in seen_ids:
            continue
        seen_ids.add(group_id)
        members = []
        for value in item.get("groups") or []:
            value = str(value)
            if value and value in known_groups and value not in members:
                members.append(value)
        normalized.append({
            "id": group_id,
            "name": str(item.get("name") or "").strip() or group_id,
            "enabled": item.get("enabled", True) is not False,
            "groups": members,
        })
    if not normalized:
        normalized = [{
            "id": QQ_FORWARDING_GROUP_DEFAULT_ID,
            "name": "转发组1",
            "enabled": True,
            "groups": [
                str(item.get("openid"))
                for item in data.get("qq_group_openids", [])
                if isinstance(item, dict) and item.get("openid") and item.get("enabled")
            ],
        }]
    data["qq_forwarding_groups"] = normalized
    return normalized


def get_qq_forwarding_groups():
    """返回 QQ→QQ 独立转发组。"""
    return _ensure_qq_forwarding_groups(_load_settings())


def get_forwarding_groups():
    """返回归一化后的转发组列表（[{id, name, channels[], groups[]}]）"""
    return _ensure_forwarding_groups(
        _load_settings()
    )


def get_groups_for_channel(channel_id):
    """Discord 频道 → QQ 目标群 的唯一路由入口（两级开关）。

    同时满足才转发：
      - 频道全局 enabled
      - 群全局 enabled
      - 频道与群同属于至少一个转发组
    不在任何转发组的频道不转发（全局启用但未勾选任何组 = 该频道暂停）。
    返回值是去重后的 openid 列表，顺序与全局启用群一致；
    同一群被多个转发组勾选不会重复出现（下游 inflight 判重兜底）。"""
    data = _load_settings()
    channel_id = str(channel_id)

    enabled_channels = set(
        _enabled_channel_ids(data)
    )

    if channel_id not in enabled_channels:
        return []

    enabled_groups = _enabled_group_openids(data)

    if not enabled_groups:
        return []

    targets = set()
    for fg in _ensure_forwarding_groups(data):
        if fg.get("enabled", True) is False:
            continue
        if channel_id not in fg["channels"]:
            continue
        targets.update(fg["groups"])

    # 群必须全局 enabled；按启用顺序返回即天然去重
    return [
        group
        for group in enabled_groups
        if group in targets
    ]


def get_all_channels():
    """返回设置文件中全部 Discord 频道 {频道ID: 名称}（不论是否启用）

    供私聊 relay 链接模式补频道显示名：链接指向的频道可能未启用，
    也要能显示面板配置的名字。缺失/损坏时回退默认值（空 dict）。"""
    data = _load_settings()
    items = data.get("discord_channels")
    if isinstance(items, list):
        all_channels = {}
        for item in items:
            if item.get("id"):
                all_channels[str(item["id"])] = str(item.get("name") or "")
        return all_channels
    return dict(DISCORD_CHANNELS)


def get_qq_c2c_relay_filter():
    """返回私聊 Discord 链接 relay 的全局筛选配置。"""
    data = _load_settings()
    value = data.get("qq_c2c_relay_filter")
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("filter_message_types",):
        item = value.get(key)
        if isinstance(item, list):
            result[key] = [str(entry).strip() for entry in item if str(entry).strip()]
    return result


def get_x_c2c_relay_filter():
    """返回私聊 X 链接手动转发的全局消息类型筛选配置。"""
    data = _load_settings()
    value = data.get("x_c2c_relay_filter")
    if not isinstance(value, dict):
        return {}
    types = value.get("filter_message_types")
    if not isinstance(types, list):
        return {}
    return {
        "filter_message_types": [
            str(entry).strip() for entry in types if str(entry).strip()
        ],
    }


def get_active_user_openids():
    """返回当前启用（允许私聊 relay）的 QQ 用户 openid 列表

    设置文件中存在用户列表时，按勾选状态返回；缺失/损坏时返回空列表
    （默认所有人不可用，需在管理面板勾选放行）。"""
    data = _load_settings()
    items = data.get("qq_user_openids")
    if isinstance(items, list):
        active = []
        for item in items:
            if item.get("enabled") and item.get("openid"):
                active.append(str(item["openid"]))
        return active
    return []


def get_failure_notification_config():
    """返回失败通知设置；配置缺失或异常时安全地保持关闭。"""
    data = _load_settings()
    value = data.get("failure_notification")
    if not isinstance(value, dict):
        return {"enabled": False, "cooldown_seconds": 900}
    enabled = value.get("enabled") is True
    try:
        cooldown = int(value.get("cooldown_seconds", 900))
    except (TypeError, ValueError):
        cooldown = 900
    return {
        "enabled": enabled,
        "cooldown_seconds": min(86400, max(60, cooldown)),
    }


def get_failure_notification_recipients():
    """返回明确勾选接收失败通知的已启用私聊用户。"""
    data = _load_settings()
    items = data.get("qq_user_openids")
    if not isinstance(items, list):
        return []
    return [
        str(item["openid"])
        for item in items
        if isinstance(item, dict)
        and item.get("enabled") is True
        and item.get("failure_notification") is True
        and item.get("openid")
    ]


def get_related_group_openids(group_openid):
    """返回与指定 QQ 群至少共同属于一个转发组的其他 QQ 群 openid 集合。

    QQ→QQ 手动转发只读取独立的 qq_forwarding_groups；候选目标 = 与当前群
    同属至少一个已启用 QQ 转发组的群（跨多个组并去重）。
    当前群不属于任何转发组时返回空集合；不包含当前群自身。"""
    data = _load_settings()
    gid = str(group_openid)
    registered = data.get("qq_group_openids")
    if isinstance(registered, list):
        source = next(
            (item for item in registered
             if isinstance(item, dict) and str(item.get("openid")) == gid),
            None,
        )
        if source is None or source.get("enabled") is not True:
            return set()
    result = set()
    for fg in _ensure_qq_forwarding_groups(data):
        if fg.get("enabled", True) is False:
            continue
        members = [str(o) for o in fg["groups"]]
        if gid in members:
            result.update(members)
    result.discard(gid)
    return result


# =====================================================
# QQ→QQ 手动转发配置：独立于 Discord / X 转发组。
# =====================================================

def get_qq_group_entries():
    """返回 settings 中全部已注册 QQ 群条目（原样 dict 列表）。

    仅用于枚举候选目标群（openid/name/enabled），不参与
    Discord→QQ 权限判断；缺失/损坏时回退空列表。"""
    data = _load_settings()
    items = data.get("qq_group_openids")
    if isinstance(items, list):
        return [
            dict(item)
            for item in items
            if isinstance(item, dict) and item.get("openid")
        ]
    return []


def get_qq_fwd_recency_limit():
    """“上一条消息”可接受的最大时间差（秒），默认 1800（30 分钟）"""
    data = _load_settings()
    try:
        value = int(data.get("qq_fwd_recency_limit") or 1800)
    except (TypeError, ValueError):
        return 1800
    return value if value > 0 else 1800


def get_qq_fwd_select_timeout():
    """交互选择超时（秒），默认 60；超时后该用户的待转发请求作废"""
    data = _load_settings()
    try:
        value = int(data.get("qq_fwd_select_timeout") or 60)
    except (TypeError, ValueError):
        return 60
    return value if value > 0 else 60


def get_backfill_limit():
    """每次启动补发时，每个频道最多补发的消息条数（默认 10）"""
    data = _load_settings()
    try:
        value = int(data.get("backfill_limit") or 10)
    except (TypeError, ValueError):
        return 10
    return value if value > 0 else 10


def get_backfill_enabled():
    """补发功能总开关（默认开启）"""
    data = _load_settings()
    value = data.get("backfill_enabled")
    if value is None:
        return True
    return bool(value)


def get_channel_filter(channel_id):
    """返回指定频道的消息筛选配置

    {"filter_usernames": [...], "filter_keywords": [...],
     "filter_message_types": [...] | None}
    旧配置没有筛选字段时返回空列表（= 不筛选）；filter_message_types
    缺省为 None（= 全部消息类型启用，由 plugins/filter.py 解释）。
    兼容旧字段 filter_user_ids：如果存在则转换为 filter_usernames。
    """
    data = _load_settings()
    items = data.get("discord_channels")
    if isinstance(items, list):
        for item in items:
            if str(item.get("id")) == str(channel_id):
                # 优先读新字段 filter_usernames，兼容旧字段 filter_user_ids
                usernames = item.get("filter_usernames")
                if usernames is None:
                    usernames = item.get("filter_user_ids") or []
                return {
                    "filter_usernames": [
                        str(u).strip()
                        for u in usernames
                        if str(u).strip()
                    ],
                    "filter_keywords": [
                        str(k)
                        for k in (item.get("filter_keywords") or [])
                        if k
                    ],
                    "filter_message_types": item.get("filter_message_types"),
                }
    return {
        "filter_usernames": [],
        "filter_keywords": [],
        "filter_message_types": None,
    }


# =====================================================
# X（Twitter）RSS 订阅（RSSHub → WRelay）
#
# 与 Discord 转发完全独立的链路（不读不写 forwarding_groups）：
#   - 源列表 x_rss_sources：[{source, name, enabled}]
#     source 填 X 账号 screen_name（如 "xx_Chon_xx"），也兼容直接填
#     RSSHub 完整 URL；轮询时拼 {x_rsshub_base}/twitter/user/{source}
#   - 目标群：X 转发组 x_forwarding_groups（X 源 → X 组 → QQ 群，
#     见下方 X 转发组路由）；旧配置缺省时内存生成默认组，
#     默认组 groups 取 x_rss_target_groups（非空）否则全部启用群，
#     与旧的"全局白名单/全量投递"行为完全一致
#   - 去重游标复用 data/discord_last.json（伪频道键 "x_rss:{handle}"，
#     tweet ID 与 Discord 消息 ID 一样是单调递增整数，dedup 纯函数原样可用）
# =====================================================

# X 转发组：与 Discord forwarding_groups 完全独立的一套组配置。
# 旧配置缺省时内存归一化为默认 X 转发组（sources/groups = 当前启用项 /
# 旧全局白名单），路由结果与旧版完全一致；面板保存后固化落盘。
# x_default 是 X 的默认/旧配置兼容组，保留为普通 X 转发组。
X_FORWARDING_GROUP_DEFAULT_ID = "x_default"
X_FORWARDING_GROUP_DEFAULT_NAME = "转发组1"

def _x_source_key(raw):
    """x_rss_sources 的原始 source 字符串 → 去重状态 key（与游标键一致）。

    填 screen_name 时为去 @ 的 handle（稳定不随 RSSHub 地址变化）；
    直接填 URL 时尝试提取 handle，提取不到则用整个 URL。"""
    if raw.startswith("http://") or raw.startswith("https://"):
        m = re.search(r"/twitter/user/([^/?#]+)", raw)
        return m.group(1) if m else raw
    return raw.lstrip("@")


def get_x_rss_sources():
    """返回归一化的 X RSS 订阅源列表 [{key, url, name, enabled}]

    key 是去重状态键（见 _x_source_key）。enabled 缺省视为 False
    （不发送）。"""
    data = _load_settings()
    base = get_x_rsshub_base()
    items = data.get("x_rss_sources")
    sources = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("source") or "").strip()
            if not raw:
                continue
            key = _x_source_key(raw)
            if key.startswith("http://") or key.startswith("https://"):
                url = raw
            else:
                url = f"{base}/twitter/user/{key}"
            sources.append({
                "key": key,
                "url": url,
                "name": str(item.get("name") or "").strip() or key,
                "enabled": bool(item.get("enabled")),
            })
    return sources


def get_x_rss_poll_interval():
    """X RSS 轮询间隔（秒），默认 300（对齐 RSSHub 缓存 max-age=300）；
    下限 30 秒，防误配打爆 RSSHub"""
    data = _load_settings()
    try:
        value = int(data.get("x_rss_poll_interval") or 300)
    except (TypeError, ValueError):
        return 300
    return value if value >= 30 else 300


def get_scheduled_message_config():
    """返回多条定时消息计划；读取时仍做安全兜底。"""
    data = _load_settings()
    value = data.get("scheduled_message")
    if not isinstance(value, dict):
        return {
            "enabled": False,
            "target_groups": [],
            "messages": [],
        }
    messages = []
    for index, raw in enumerate(value.get("messages") or []):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        item = {
            "id": str(raw.get("id") or "message-" + str(index + 1)),
            "content": content,
            "schedule_type": raw.get("schedule_type"),
        }
        if item["schedule_type"] == "interval":
            def _int_value(key, default, maximum):
                try:
                    parsed = int(raw.get(key, default))
                except (TypeError, ValueError):
                    parsed = default
                return max(0, min(maximum, parsed))

            item.update({
                "interval_days": _int_value("interval_days", 0, 3650),
                "interval_hours": _int_value("interval_hours", 0, 23),
                "interval_minutes": _int_value("interval_minutes", 1, 59),
                "anchor_at": str(raw.get("anchor_at") or ""),
            })
            if not (item["interval_days"] or item["interval_hours"] or item["interval_minutes"]):
                item["interval_minutes"] = 1
        elif item["schedule_type"] == "calendar":
            calendar_type = raw.get("calendar_type")
            item["calendar_type"] = calendar_type
            item["send_time"] = raw.get("send_time") if isinstance(raw.get("send_time"), str) else "09:00"
            if calendar_type == "weekly":
                item["weekdays"] = sorted({
                    value for value in raw.get("weekdays") or []
                    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6
                })
            elif calendar_type == "monthly":
                try:
                    item["day_of_month"] = max(1, min(31, int(raw.get("day_of_month") or 1)))
                except (TypeError, ValueError):
                    item["day_of_month"] = 1
            elif calendar_type == "yearly":
                try:
                    item["month"] = max(1, min(12, int(raw.get("month") or 1)))
                except (TypeError, ValueError):
                    item["month"] = 1
                try:
                    item["day"] = max(1, min(31, int(raw.get("day") or 1)))
                except (TypeError, ValueError):
                    item["day"] = 1
            else:
                continue
        else:
            continue
        messages.append(item)
    return {
        "enabled": value.get("enabled") is True,
        "target_groups": [
            str(openid).strip()
            for openid in value.get("target_groups") or []
            if str(openid).strip()
        ],
        "messages": messages,
    }


def get_x_rsshub_base():
    """RSSHub 服务地址，默认 http://localhost:1200（末尾斜杠归一化）"""
    data = _load_settings()
    base = str(data.get("x_rsshub_base") or "").strip()
    return (base or "http://localhost:1200").rstrip("/")


def get_x_rss_target_groups():
    """X RSS 投递目标群白名单（openid 列表）——旧配置字段。

    仅在 x_forwarding_groups 缺省时作为默认组的 groups 来源；
    字段本身保留不删，新配置存在后路由不再读取它。
    空列表（默认/缺省）= 默认组回退为全部启用 QQ 群（原行为）。"""
    data = _load_settings()
    items = data.get("x_rss_target_groups")
    if not isinstance(items, list):
        return []
    return [str(o) for o in items if str(o).strip()]


# =====================================================
# X 转发组路由（x_forwarding_groups）
#
# 与 Discord forwarding_groups 完全独立的一套组配置：
#   X 源（x_rss:{handle} 的 handle） → X 组 → QQ 群
#
# 只负责"当前路由"，不管理去重游标：游标仍是
# data/discord_last.json 里 "x_rss:{handle}" 按群记录的
# {group_openid: tweet_id}，转发组只决定本轮哪些群是目标。
#
# 实际发送必须同时满足（两级开关，与 get_groups_for_channel 同语义）：
#   - 源全局 enabled（x_rss_sources 条目的 enabled）
#   - 群全局 enabled（qq_group_openids 条目的 enabled）
#   - 源与群同属于至少一个 X 转发组
# =====================================================


def _x_source_keys(data, only_enabled=False):
    """settings dict 内 X 源 key 集合（归一化清理悬空引用 / 路由共用）。

    only_enabled=True 时只返回 enabled 条目的 key（与
    get_x_rss_sources 的 enabled 缺省视为 False 一致）。"""
    keys = set()
    items = data.get("x_rss_sources")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("source") or "").strip()
            if not raw:
                continue
            if only_enabled and not item.get("enabled"):
                continue
            keys.add(_x_source_key(raw))
    return keys


def _ensure_x_forwarding_groups(data):
    """X 转发组内存归一化（只改内存 data，不写盘）。

    - 结构合法 → 清洗保留（id 去重、字段转 str、name 缺省回退 id、
      sources/groups 去重并剔除悬空引用——源已删除/群已注销的引用）
    - 缺省/空/结构无效（旧配置升级）→ 生成默认组：
      sources = 当前全部 enabled 的 X 源 key；
      groups = x_rss_target_groups（非空，剔除未注册群）否则全部启用群，
      与旧的"全局白名单/全量投递"行为完全一致
    - 不按 enabled 裁剪组成员：enabled 只影响运行，成员关系独立保存"""
    groups = data.get("x_forwarding_groups")
    if not isinstance(groups, list):
        groups = []

    valid_sources = _x_source_keys(data)
    valid_groups = {
        str(item["openid"])
        for item in data.get("qq_group_openids", [])
        if isinstance(item, dict) and item.get("openid")
    }

    normalized = []
    seen_ids = set()
    for item in groups:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("id") or "").strip()
        if not gid or gid in seen_ids:
            continue
        seen_ids.add(gid)
        sources = []
        for key in (item.get("sources") or []):
            key = str(key)
            if key and key in valid_sources and key not in sources:
                sources.append(key)
        xgroups = []
        for openid in (item.get("groups") or []):
            openid = str(openid)
            if openid and openid in valid_groups and openid not in xgroups:
                xgroups.append(openid)
        normalized.append({
            "id": gid,
            "name": str(item.get("name") or "").strip() or gid,
            "enabled": item.get("enabled", True) is not False,
            "sources": sources,
            "groups": xgroups,
        })

    if not normalized:
        target = [
            o for o in get_x_rss_target_groups()
            if o in valid_groups
        ]
        if not target:
            target = _enabled_group_openids(data)
        normalized = [{
            "id": X_FORWARDING_GROUP_DEFAULT_ID,
            "name": X_FORWARDING_GROUP_DEFAULT_NAME,
            "enabled": True,
            "sources": sorted(_x_source_keys(data, only_enabled=True)),
            "groups": target,
        }]

    data["x_forwarding_groups"] = normalized
    return normalized


def get_x_forwarding_groups():
    """返回归一化后的 X 转发组列表（[{id, name, sources[], groups[]}]）"""
    return _ensure_x_forwarding_groups(
        _load_settings()
    )


def get_x_groups_for_source(source_key):
    """X 源 → QQ 目标群 的唯一路由入口（与 get_groups_for_channel
    同语义，但独立实现，不读 Discord forwarding_groups）。

    同时满足才投递：
      - 源全局 enabled（x_rss_sources 条目的 enabled）
      - 群全局 enabled（qq_group_openids 条目的 enabled）
      - 源与群同属于至少一个 X 转发组
    源未启用或未加入任何 X 转发组返回 []（不回退全量投递）。
    返回值是去重后的 openid 列表，顺序与全局启用群一致；
    同一群被多个 X 转发组勾选不会重复出现（下游 inflight 判重兜底）。"""
    data = _load_settings()
    source_key = str(source_key or "").strip()

    if not source_key or source_key not in _x_source_keys(data, only_enabled=True):
        return []

    enabled_groups = _enabled_group_openids(data)
    if not enabled_groups:
        return []

    targets = set()
    for fg in _ensure_x_forwarding_groups(data):
        if fg.get("enabled", True) is False:
            continue
        if source_key in fg["sources"]:
            targets.update(fg["groups"])

    # 群必须全局 enabled；按启用顺序返回即天然去重
    return [
        group
        for group in enabled_groups
        if group in targets
    ]
