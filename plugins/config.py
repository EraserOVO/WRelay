import os
import re
import threading
from urllib.parse import quote

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
from plugins.filter import (
    DISCORD_MESSAGE_TYPES,
    X_MESSAGE_TYPES,
    BILIBILI_MESSAGE_TYPES,
    YOUTUBE_MESSAGE_TYPES,
    normalize_message_types,
)
from plugins.paths import data_path
from plugins.bilibili_rate_limit import normalize_protection_config, normalize_string_list
from plugins.x_rate_limit import normalize_x_protection_config
from plugins.youtube_parser import (
    channel_feed_url,
    normalize_channel_value,
    youtube_source_id,
)

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
    """返回 Discord/X 共用的翻译服务路由。

    ``x_translation`` 仅作为旧配置兼容字段保留，新的 X 转发不再使用
    独立服务路由，避免面板两处选择不一致。
    """
    return _setting_object("translation")


def _translation_config_for_item_data(
    data, config_name, items_name, item_id, manual=False
):
    """将服务路由与来源翻译开关合并为调用配置。

    ``manual=True`` 用于 QQ 手动转发：启用与否只看共享 ``translation.enabled``，
    不受来源的自动翻译开关影响；保留原文和显示时间仍沿用来源设置。
    """
    config = dict(data.get(config_name) or {})
    manual_enabled = config.get("enabled") is True
    for item in data.get(items_name) or []:
        if not isinstance(item, dict):
            continue
        candidate = item.get("source") if items_name == "x_rss_sources" else item.get("id")
        if items_name == "bilibili_sources" and not candidate:
            candidate = _bilibili_source_id(
                str(item.get("kind") or "up").strip().lower(),
                str(item.get("value") or item.get("uid") or "").strip(),
            )
        if items_name == "youtube_sources" and not candidate:
            candidate = youtube_source_id(
                item.get("value") or item.get("channel") or ""
            )
        candidate_id = _x_source_key(str(candidate or "")) if items_name == "x_rss_sources" else str(candidate or "")
        if candidate_id == str(item_id):
            config["enabled"] = (
                manual_enabled
                if manual
                else item.get("translation_enabled") is True
            )
            config["append_original"] = item.get("translation_append_original") is not False
            config["show_datetime"] = item.get("translation_show_datetime") is True
            return config
    config["enabled"] = manual_enabled if manual else False
    config["append_original"] = True
    config["show_datetime"] = False
    return config


def _translation_config_for_item(
    config_name, items_name, item_id, manual=False
):
    return _translation_config_for_item_data(
        _load_settings(), config_name, items_name, item_id, manual
    )


def get_channel_translation_config(channel_id, manual=False):
    return _translation_config_for_item(
        "translation", "discord_channels", channel_id, manual
    )


def get_x_source_translation_config(source_key, manual=False):
    return _translation_config_for_item(
        "translation", "x_rss_sources", source_key, manual
    )


def get_bilibili_source_translation_config(source_id, manual=False):
    return _translation_config_for_item(
        "translation", "bilibili_sources", source_id, manual
    )


def get_youtube_source_translation_config(source_id, manual=False):
    return _translation_config_for_item(
        "translation", "youtube_sources", source_id, manual
    )


def _source_message_types(item, supported):
    """读取来源级消息类型筛选，缺省全开，并迁移旧类型名。

    显式空列表仍作为全关筛选返回；只有非空的旧值归一化后没有任何支持项，
    才回退为未筛选，避免旧配置令来源停止投递。
    """
    values = item.get("filter_message_types")
    if not isinstance(values, list):
        return list(supported)
    explicit = any(str(value).strip() for value in values)
    if not explicit:
        return []
    normalized = normalize_message_types(values, supported)
    return normalized or list(supported)


def get_x_source_filter(source_key):
    """读取指定 X RSS 账号的消息筛选配置。"""
    return _x_source_filter(_load_settings(), source_key)


def _x_source_filter(data, source_key):
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
            "filter_message_types": _source_message_types(item, X_MESSAGE_TYPES),
        }
    return {
        "filter_keywords": [],
        "filter_post_types": None,
        "filter_message_types": list(X_MESSAGE_TYPES),
    }


def get_bilibili_source_filter(source_id):
    """返回指定 Bilibili 来源的关键词、投稿类型、消息类型和视频本体配置。"""
    return _bilibili_source_filter(_load_settings(), source_id)


def _bilibili_source_filter(data, source_id):
    for item in data.get("bilibili_sources") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "up")
        value = str(item.get("value") or item.get("uid") or "").strip()
        candidate_id = str(item.get("id") or _bilibili_source_id(kind, value)).strip()
        if candidate_id != str(source_id):
            continue
        keywords = [
            str(keyword).strip()
            for keyword in (item.get("filter_keywords") or [])
            if str(keyword).strip()
        ]
        message_types = _source_message_types(item, BILIBILI_MESSAGE_TYPES)
        allowed_types = {"视频", "专栏", "动态"}
        stored_types = item.get("filter_bilibili_post_types")
        post_types = (
            [post_type for post_type in stored_types if post_type in allowed_types]
            if isinstance(stored_types, list)
            else ["视频"]
        )
        if kind != "up":
            post_types = ["视频"]
        if not post_types:
            post_types = ["视频"]
        return {
            "filter_keywords": keywords,
            "filter_bilibili_post_types": post_types,
            "filter_message_types": message_types,
        }
    return {
        "filter_keywords": [],
        "filter_bilibili_post_types": ["视频"],
        "filter_message_types": list(BILIBILI_MESSAGE_TYPES),
    }


def get_x_source_delivery_config(source_key):
    """用同一 settings 快照生成 X 来源筛选与翻译配置。"""
    data = _load_settings()
    return {
        "filter": _x_source_filter(data, source_key),
        "translation": _translation_config_for_item_data(
            data, "translation", "x_rss_sources", source_key
        ),
    }


def get_bilibili_source_delivery_config(source_id):
    """用同一 settings 快照生成 Bilibili 来源筛选与翻译配置。"""
    data = _load_settings()
    return {
        "filter": _bilibili_source_filter(data, source_id),
        "translation": _translation_config_for_item_data(
            data, "translation", "bilibili_sources", source_id
        ),
    }


def get_bilibili_protection():
    """返回 Bilibili → QQ 全局限速与积压保护配置。"""
    return normalize_protection_config(_load_settings().get("bilibili_protection"))


def get_bilibili_auto_resolve_enabled():
    """Bilibili 自动解析总开关（默认关闭）。

    配置缺失、结构损坏或字段类型异常都按关闭处理，避免损坏配置
    让机器人在群里自发消息。"""
    return _bilibili_auto_resolve_enabled_from(_load_settings())


def _bilibili_auto_resolve_enabled_from(data):
    config = data.get("bilibili_auto_resolve")
    if not isinstance(config, dict):
        return False
    return config.get("enabled") is True


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
_settings_cache_lock = threading.Lock()


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

    with _settings_cache_lock:
        cached_data = _settings_cache
        cached_mtime = _settings_cache_mtime
        cached_size = _settings_cache_size
        cached_revision = _settings_cache_revision

    if (
        cached_data is not None
        and mtime == cached_mtime
        and size == cached_size
        and revision == cached_revision
    ):
        return cached_data

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
        data = _default_settings()
    # 转发组内存归一化（不写盘）：旧配置缺省/损坏时生成默认组，
    # 保证路由函数始终拿到合法结构；与下方 qq_user_openids 的内存
    # 补齐同理，绝不把归一化结果当作面板配置写回文件
    _ensure_forwarding_groups(data)
    _ensure_qq_forwarding_groups(data)

    # 配置可能同时由事件循环与线程池读取。只在线程安全的短临界区
    # 发布完整快照，避免一个旧读取覆盖另一个更新读取的缓存版本。
    with _settings_cache_lock:
        if _settings_cache is not cached_data:
            if (
                _settings_cache is not None
                and _settings_cache_mtime == mtime
                and _settings_cache_size == size
                and _settings_cache_revision == revision
            ):
                return _settings_cache
            return data

        _settings_cache = data
        _settings_cache_mtime = mtime
        _settings_cache_size = size
        _settings_cache_revision = revision

    return data


def _active_channels_from(data):
    items = data.get("discord_channels")
    if isinstance(items, list):
        active = {}
        for item in items:
            if item.get("enabled") and item.get("id"):
                active[str(item["id"])] = str(item.get("name") or "")
        return active
    return dict(DISCORD_CHANNELS)


def get_active_channels():
    """返回当前启用的 Discord 频道 {频道ID: 名称}。"""
    return _active_channels_from(_load_settings())


def _active_groups_from(data):
    items = data.get("qq_group_openids")
    if isinstance(items, list):
        active = []
        seen = set()
        for item in items:
            if item.get("enabled") and item.get("openid"):
                openid = str(item["openid"])
                if openid in seen:
                    continue
                seen.add(openid)
                active.append(openid)
        return active
    return list(QQ_GROUP_OPENIDS)


def get_active_groups():
    """返回当前启用的 QQ 群 openid 列表

    设置文件中存在群列表时，按勾选状态返回（全部未勾选 = 空 list，
    即暂停转发，不再回退默认值）；仅当列表缺失或损坏时才回退默认值。"""
    return _active_groups_from(_load_settings())


def _group_bot_ids_from(data, group_openids):
    requested = {str(value) for value in group_openids if str(value).strip()}
    if not requested:
        return {}
    result = {openid: "" for openid in requested}
    first_binding = {}
    enabled_binding = {}
    for item in data.get("qq_group_openids", []):
        if not isinstance(item, dict):
            continue
        openid = str(item.get("openid") or "").strip()
        if openid in result:
            bot_id = str(item.get("bot_id") or "").strip()
            first_binding.setdefault(openid, bot_id)
            if item.get("enabled") is True and openid not in enabled_binding:
                enabled_binding[openid] = bot_id
    for openid in requested:
        result[openid] = enabled_binding.get(
            openid,
            first_binding.get(openid, ""),
        )
    return result


def get_group_bot_ids(group_openids):
    """返回目标群绑定的 QQ Bot AppID。

    空字符串表示使用第一个已连接 Bot，兼容所有旧版单 Bot 配置。调用方
    只传本轮目标群，因此不把未参与投递的群带入路由判断。
    """
    requested = {str(value) for value in group_openids if str(value).strip()}
    if not requested:
        return {}
    return _group_bot_ids_from(_load_settings(), requested)


def get_active_group_routing_snapshot():
    """在同一 settings 快照中返回默认目标群及其 QQ Bot 绑定。"""
    data = _load_settings()
    groups = _active_groups_from(data)
    return groups, _group_bot_ids_from(data, groups)


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
        channels = item.get("channels")
        if not isinstance(channels, list):
            channels = []
        group_openids = item.get("groups")
        if not isinstance(group_openids, list):
            group_openids = []
        normalized.append({
            "id": gid,
            "name": str(item.get("name") or "").strip() or gid,
            "enabled": item.get("enabled", True) is not False,
            "channels": [str(c) for c in channels if c],
            "groups": [str(o) for o in group_openids if o],
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
        raw_members = item.get("groups")
        if not isinstance(raw_members, list):
            raw_members = []
        for value in raw_members:
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


def _groups_for_channel_from(data, channel_id):
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


def get_groups_for_channel(channel_id):
    """返回频道命中的已启用 QQ 群；多个转发组交集按 OpenID 去重。"""
    return _groups_for_channel_from(_load_settings(), channel_id)


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


def _c2c_relay_filter(data, key, supported):
    """读取一条手动转发的类型筛选，并按该链路支持的类型归一化。

    X/Bilibili 早期与 Discord 共用六类型集合，存量配置里可能残留「语音」
    「表情」「链接」等它们不渲染的类型，以及旧名「文件」（现规范名为
    「视频」）；这里按链路支持集归一化，保证面板显示、保存值与实际渲染
    一致。

    归一化后为空是个危险边界：老配置若只存了本链路不支持的类型（例如 X
    只剩「语音」），会得到空列表，而空列表的语义是「不转发任何内容」，
    会让用户的转发突然全部失效。这种情况按"未筛选"处理（返回 {}），
    即全部支持类型启用，与老配置缺省字段的行为一致。

    注意与真正的空列表区分：面板允许用户取消勾选全部消息类型，此时
    ``filter_message_types: []`` 是用户明确选择的"不转发"，必须原样保留。
    只有「原列表非空、归一化后为空」才回退到未筛选。
    """
    value = data.get(key)
    if not isinstance(value, dict):
        return {}
    types = value.get("filter_message_types")
    if not isinstance(types, list):
        return {}
    has_explicit_values = any(str(item).strip() for item in types)
    if not has_explicit_values:
        return {"filter_message_types": []}
    normalized = normalize_message_types(types, supported)
    if not normalized:
        return {}
    return {"filter_message_types": normalized}


def get_qq_c2c_relay_filter():
    """返回私聊 Discord 链接 relay 的全局筛选配置。"""
    return _c2c_relay_filter(
        _load_settings(), "qq_c2c_relay_filter", DISCORD_MESSAGE_TYPES
    )


def get_x_c2c_relay_filter():
    """返回私聊 X 链接手动转发的全局消息类型筛选配置。"""
    return _c2c_relay_filter(
        _load_settings(), "x_c2c_relay_filter", X_MESSAGE_TYPES
    )


def get_bilibili_c2c_relay_filter():
    """返回私聊 Bilibili 投稿链接手动转发的全局消息类型筛选配置。"""
    return _c2c_relay_filter(
        _load_settings(), "bilibili_c2c_relay_filter", BILIBILI_MESSAGE_TYPES
    )


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


# =====================================================
# QQ 功能准入提示
#
# 准入判断以「当前实际授权/登记状态」为准，绝不把待审核注册申请当作
# 已授权/已登记：
#   - 用户是否可用 = 是否在 qq_user_openids（settings.json）勾选放行
#   - 群是否已登记 = 当前 Bot 对应 group_openid 是否已写入 qq_group_openids
# /注册 /注册群 仅提交申请，申请 ≠ 授权；审核通过 ≠ 自动启用。
# =====================================================

USER_NOT_AUTHORIZED_TEXT = (
    "尚未获准使用该功能。请先私聊发送 /注册 提交用户注册申请，"
    "等待管理员审核通过并启用后再试。"
)

GROUP_NOT_REGISTERED_TEXT = (
    "本群尚未登记，暂不能使用该功能。请发送 /注册群 提交群注册申请，"
    "等待管理员审核通过并启用后再试。"
)


def is_user_authorized(user_openid):
    """返回用户当前是否获准使用 QQ 功能（唯一来源 qq_user_openids 勾选状态）。"""
    return str(user_openid or "").strip() in set(get_active_user_openids())


def is_user_registered(user_openid):
    """返回用户 OpenID 是否已在 qq_user_openids 登记。

    与群注册的 is_group_registered 对应：登记来源只有 settings.json 的
    qq_user_openids，不看勾选状态，也不把 data/qq_registrations.json 的待审核
    申请当作已登记。"""
    openid = str(user_openid or "").strip()
    if not openid:
        return False
    for item in _load_settings().get("qq_user_openids", []):
        if isinstance(item, dict) and str(item.get("openid") or "").strip() == openid:
            return True
    return False


def _failure_notification_config_from(data):
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


def _failure_notification_recipients_from(data):
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


def get_failure_notification_config():
    """返回失败通知设置；配置缺失或异常时安全地保持关闭。"""
    return _failure_notification_config_from(_load_settings())


def get_failure_notification_recipients():
    """返回明确勾选接收失败通知的已启用私聊用户。"""
    return _failure_notification_recipients_from(_load_settings())


def get_failure_notification_snapshot():
    """从同一 settings 版本返回失败通知设置与已授权收件人。"""
    data = _load_settings()
    return (
        _failure_notification_config_from(data),
        _failure_notification_recipients_from(data),
    )


def _related_group_openids_from(data, group_openid):
    gid = str(group_openid)
    registered = data.get("qq_group_openids")
    if isinstance(registered, list):
        source_enabled = any(
            isinstance(item, dict)
            and str(item.get("openid")) == gid
            and item.get("enabled") is True
            for item in registered
        )
        if not source_enabled:
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


def get_related_group_openids(group_openid):
    """返回与指定 QQ 群至少共同属于一个转发组的其他 QQ 群 openid 集合。

    QQ→QQ 手动转发只读取独立的 qq_forwarding_groups；候选目标 = 与当前群
    同属至少一个已启用 QQ 转发组的群（跨多个组并去重）。
    当前群不属于任何转发组时返回空集合；不包含当前群自身。"""
    return _related_group_openids_from(_load_settings(), group_openid)


# =====================================================
# QQ→QQ 手动转发配置：独立于 Discord / X 转发组。
# =====================================================

def _qq_group_entries_from(data):
    items = data.get("qq_group_openids")
    if isinstance(items, list):
        return [
            dict(item)
            for item in items
            if isinstance(item, dict) and item.get("openid")
        ]
    return []


def get_qq_group_entries():
    """返回 settings 中全部已注册 QQ 群条目（原样 dict 列表）。

    仅用于枚举候选目标群（openid/name/enabled），不参与
    Discord→QQ 权限判断；缺失/损坏时回退空列表。"""
    return _qq_group_entries_from(_load_settings())


def is_group_registered(group_openid, bot_id=""):
    """返回当前 Bot 对应的 QQ 群是否已登记。

    以 qq_group_openids（settings.json）为唯一登记来源；同一 openid
    存在未绑定 Bot（空 bot_id 兼容旧配置）或绑定为当前 Bot 的条目即视为
    已登记。绝不以 data/qq_registrations.json 的待审核申请作为登记依据。
    """
    openid = str(group_openid or "").strip()
    bot = str(bot_id or "").strip()
    if not openid:
        return False
    for item in get_qq_group_entries():
        if not isinstance(item, dict):
            continue
        if str(item.get("openid") or "").strip() != openid:
            continue
        if not bot or not str(item.get("bot_id") or "").strip() or str(item.get("bot_id") or "").strip() == bot:
            return True
    return False


def get_qq_fwd_routing_snapshot(group_openid):
    """在同一 settings 版本返回群手动转发目标条目与关联群集合。"""
    data = _load_settings()
    return (
        _qq_group_entries_from(data),
        _related_group_openids_from(data, group_openid),
    )


def get_qq_fwd_recency_limit():
    """“上一条消息”可接受的最大时间差（秒），默认 1800（30 分钟）"""
    data = _load_settings()
    try:
        value = int(data.get("qq_fwd_recency_limit") or 1800)
    except (OverflowError, TypeError, ValueError):
        return 1800
    return value if value > 0 else 1800


def _qq_fwd_select_timeout_from(data):
    try:
        value = int(data.get("qq_fwd_select_timeout") or 60)
    except (OverflowError, TypeError, ValueError):
        return 60
    return value if value > 0 else 60


def get_qq_fwd_select_timeout():
    """交互选择超时（秒），默认 60；超时后该用户的待转发请求作废"""
    return _qq_fwd_select_timeout_from(_load_settings())


def get_backfill_limit():
    """每次启动补发时，每个频道最多补发的消息条数（默认 10）"""
    return _backfill_limit_from(_load_settings())


def _backfill_limit_from(data):
    try:
        value = int(data.get("backfill_limit") or 10)
    except (TypeError, ValueError):
        return 10
    return value if value > 0 else 10


def get_backfill_enabled():
    """补发功能总开关（默认开启）"""
    return _backfill_enabled_from(_load_settings())


def _backfill_enabled_from(data):
    value = data.get("backfill_enabled")
    if value is None:
        return True
    return bool(value)


def _backfill_routing_from(data):
    channels = _active_channels_from(data)
    return {
        "channels": channels,
        "groups_by_channel": {
            channel_id: _groups_for_channel_from(data, channel_id)
            for channel_id in channels
        },
    }


def get_backfill_routing_snapshot():
    """用同一份 settings 快照生成启用频道与目标群映射。"""
    return _backfill_routing_from(_load_settings())


def get_backfill_runtime_snapshot():
    """生成启动补发一轮所需的开关、额度、频道和路由配置。"""
    data = _load_settings()
    snapshot = _backfill_routing_from(data)
    snapshot.update({
        "enabled": _backfill_enabled_from(data),
        "limit": _backfill_limit_from(data),
    })
    return snapshot


def _channel_filter_from(data, channel_id):
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


def get_channel_filter(channel_id):
    """返回指定频道的筛选配置（兼容旧字段 filter_user_ids）。"""
    return _channel_filter_from(_load_settings(), channel_id)


def get_discord_relay_config(channel_id):
    """Return one coherent channel/group/filter snapshot for a live relay event."""
    data = _load_settings()
    channel_id = str(channel_id)
    channels = _active_channels_from(data)
    if channel_id not in channels:
        return None
    return {
        "channel_name": channels[channel_id],
        "groups": _groups_for_channel_from(data, channel_id),
        "filter": _channel_filter_from(data, channel_id),
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


def _get_x_rss_sources(data):
    """返回归一化的 X RSS 订阅源列表 [{key, url, name, enabled}]

    key 是去重状态键（见 _x_source_key）。enabled 缺省视为 False
    （不发送）。"""
    base = _x_rsshub_base(data)
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


def get_x_rss_sources():
    """返回归一化的 X RSS 订阅源列表 [{key, url, name, enabled}]。"""
    return _get_x_rss_sources(_load_settings())


def get_x_rss_status_snapshot():
    """用同一份 settings 快照生成 X 状态接口所需配置。"""
    data = _load_settings()
    return {
        "sources": _get_x_rss_sources(data),
        "protection": normalize_x_protection_config(data.get("x_protection")),
    }


def get_x_rss_poll_interval():
    """X RSS 轮询间隔（秒），默认 300（对齐 RSSHub 缓存 max-age=300）；
    下限 30 秒，防误配打爆 RSSHub"""
    data = _load_settings()
    try:
        value = int(data.get("x_rss_poll_interval") or 300)
    except (OverflowError, TypeError, ValueError):
        return 300
    return value if value >= 30 else 300


def get_x_protection():
    """返回 X → QQ 全局限速与积压保护配置。"""
    return normalize_x_protection_config(_load_settings().get("x_protection"))


def get_scheduled_message_config():
    """返回多条定时消息计划；读取时仍做安全兜底。

    每条消息可自带目标（target_mode / target_groups / forward_groups）；
    缺少这些字段的旧消息按「指定群」处理并沿用顶层 target_groups。"""
    data = _load_settings()
    value = data.get("scheduled_message")
    if not isinstance(value, dict):
        return {
            "enabled": False,
            "target_groups": [],
            "messages": [],
        }
    default_targets = _scheduled_group_list(value.get("target_groups"))
    messages = []
    for index, raw in enumerate(value.get("messages") or []):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        image_data = raw.get("image_data")
        if not content and not isinstance(image_data, str):
            continue
        if not content and not image_data:
            continue
        item = {
            "id": str(raw.get("id") or "message-" + str(index + 1)),
            "content": content,
            "schedule_type": raw.get("schedule_type"),
        }
        item.update(_scheduled_message_targets(raw, default_targets))
        if isinstance(image_data, str) and image_data:
            item["image_data"] = image_data
            item["image_name"] = str(raw.get("image_name") or "")
        if item["schedule_type"] == "interval":
            def _int_value(key, default, maximum):
                try:
                    parsed = int(raw.get(key, default))
                except (OverflowError, TypeError, ValueError):
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
                except (OverflowError, TypeError, ValueError):
                    item["day_of_month"] = 1
            elif calendar_type == "yearly":
                try:
                    item["month"] = max(1, min(12, int(raw.get("month") or 1)))
                except (OverflowError, TypeError, ValueError):
                    item["month"] = 1
                try:
                    item["day"] = max(1, min(31, int(raw.get("day") or 1)))
                except (OverflowError, TypeError, ValueError):
                    item["day"] = 1
            else:
                continue
        else:
            continue
        messages.append(item)
    return {
        "enabled": value.get("enabled") is True,
        "target_groups": default_targets,
        "messages": messages,
    }


SCHEDULED_TARGET_GROUP = "groups"
SCHEDULED_TARGET_FORWARD_GROUP = "forward_group"
SCHEDULED_TARGET_ALL = "all"
SCHEDULED_TARGET_MODES = (
    SCHEDULED_TARGET_GROUP,
    SCHEDULED_TARGET_FORWARD_GROUP,
    SCHEDULED_TARGET_ALL,
)


def _scheduled_group_list(value):
    """把群 openid 列表归一为非空字符串列表（保持顺序、去重）。"""
    if not isinstance(value, list):
        return []
    result = []
    for openid in value:
        text = str(openid or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _scheduled_forward_group_list(value):
    if not isinstance(value, list):
        return []
    result = []
    for group_id in value:
        text = str(group_id or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _scheduled_message_targets(raw, default_targets):
    """解析单条定时消息的目标设置。

    - 缺少 target_mode：旧消息，按「指定群」并沿用顶层默认目标；
    - groups：使用消息自己的 target_groups（缺省回落到默认目标）；
    - forward_group：使用 QQ→QQ 转发组 id，发送时动态取组成员；
    - all：全部已启用群。
    """
    mode = str(raw.get("target_mode") or "").strip()
    if mode not in SCHEDULED_TARGET_MODES:
        mode = SCHEDULED_TARGET_GROUP
    legacy = "target_mode" not in raw
    if mode == SCHEDULED_TARGET_FORWARD_GROUP:
        groups = []
    elif mode == SCHEDULED_TARGET_ALL:
        groups = []
    elif legacy or "target_groups" not in raw:
        # 旧消息或未写消息级列表：沿用顶部「默认目标」。
        groups = list(default_targets)
    else:
        groups = _scheduled_group_list(raw.get("target_groups"))
    return {
        "target_mode": mode,
        "target_groups": groups,
        "forward_groups": _scheduled_forward_group_list(raw.get("forward_groups")),
    }


def resolve_scheduled_targets(message, qq_groups, qq_forwarding_groups, active_groups):
    """返回一条定时消息本次应发送的群 openid 列表。

    - 所有模式最终都只保留「全局启用」的群；
    - 转发组模式取所选组中已启用组的成员并集，组被暂停或成员为空即无目标。
    """
    active = set(str(openid) for openid in (active_groups or []))
    mode = str((message or {}).get("target_mode") or SCHEDULED_TARGET_GROUP)
    if mode == SCHEDULED_TARGET_ALL:
        candidates = [str(item.get("openid") or "").strip() for item in (qq_groups or [])]
    elif mode == SCHEDULED_TARGET_FORWARD_GROUP:
        wanted = set(
            str(group_id) for group_id in (message or {}).get("forward_groups") or []
        )
        candidates = []
        for group in qq_forwarding_groups or []:
            if not isinstance(group, dict) or group.get("enabled") is False:
                continue
            if str(group.get("id") or "") not in wanted:
                continue
            for openid in group.get("groups") or []:
                candidates.append(str(openid))
    else:
        candidates = list((message or {}).get("target_groups") or [])
    result = []
    for openid in candidates:
        if openid and openid in active and openid not in result:
            result.append(openid)
    return result


def scheduled_message_has_targets(message, qq_forwarding_groups):
    """配置层面的目标有效性（不看群启用状态），供面板/命令给出明确提示。"""
    mode = str((message or {}).get("target_mode") or SCHEDULED_TARGET_GROUP)
    if mode == SCHEDULED_TARGET_ALL:
        # 「全部群」表示所有已启用群，是否有群取决于当前群配置。
        return True
    if mode == SCHEDULED_TARGET_FORWARD_GROUP:
        wanted = set(
            str(group_id) for group_id in (message or {}).get("forward_groups") or []
        )
        return any(
            isinstance(group, dict)
            and str(group.get("id") or "") in wanted
            and group.get("enabled") is not False
            and bool(group.get("groups"))
            for group in qq_forwarding_groups or []
        )
    return bool((message or {}).get("target_groups"))


def _x_rsshub_base(data):
    base = str(data.get("x_rsshub_base") or "").strip()
    return (base or "http://localhost:1200").rstrip("/")


def get_x_rsshub_base():
    """RSSHub 服务地址，默认 http://localhost:1200（末尾斜杠归一化）"""
    return _x_rsshub_base(_load_settings())


def _x_rss_target_groups(data):
    items = data.get("x_rss_target_groups")
    if not isinstance(items, list):
        return []
    return [str(o) for o in items if str(o).strip()]


def get_x_rss_target_groups():
    """X RSS 投递目标群白名单（openid 列表）——旧配置字段。

    仅在 x_forwarding_groups 缺省时作为默认组的 groups 来源；
    字段本身保留不删，新配置存在后路由不再读取它。
    空列表（默认/缺省）= 默认组回退为全部启用 QQ 群（原行为）。"""
    return _x_rss_target_groups(_load_settings())


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
        raw_sources = item.get("sources")
        if not isinstance(raw_sources, list):
            raw_sources = []
        for key in raw_sources:
            key = str(key)
            if key and key in valid_sources and key not in sources:
                sources.append(key)
        xgroups = []
        raw_group_openids = item.get("groups")
        if not isinstance(raw_group_openids, list):
            raw_group_openids = []
        for openid in raw_group_openids:
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
        target = [o for o in _x_rss_target_groups(data) if o in valid_groups]
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


def _get_x_groups_by_source(data):
    enabled_sources = _x_source_keys(data, only_enabled=True)
    enabled_groups = _enabled_group_openids(data)
    if not enabled_sources or not enabled_groups:
        return {}

    targets_by_source = {source_key: set() for source_key in enabled_sources}
    for fg in _ensure_x_forwarding_groups(data):
        if fg.get("enabled", True) is False:
            continue
        for source_key in fg["sources"]:
            if source_key in targets_by_source:
                targets_by_source[source_key].update(fg["groups"])

    # 群必须全局 enabled；按启用顺序返回即天然去重。
    return {
        source_key: [group for group in enabled_groups if group in targets]
        for source_key, targets in targets_by_source.items()
    }


def get_x_groups_for_source(source_key):
    """X 源 → QQ 目标群的唯一实时路由入口。"""
    data = _load_settings()
    source_key = str(source_key or "").strip()
    if not source_key or source_key not in _x_source_keys(data, only_enabled=True):
        return []
    enabled_groups = _enabled_group_openids(data)
    if not enabled_groups:
        return []
    targets = set()
    for forwarding_group in _ensure_x_forwarding_groups(data):
        if forwarding_group.get("enabled", True) is not False and source_key in forwarding_group["sources"]:
            targets.update(forwarding_group["groups"])
    return [group for group in enabled_groups if group in targets]


def get_x_rss_polling_snapshot():
    """用同一份 settings 快照生成一轮 X 来源及目标群路由。"""
    data = _load_settings()
    return _get_x_rss_sources(data), _get_x_groups_by_source(data)


# =====================================================
# Bilibili 投稿路由（独立于 Discord/X）
#
# source kind:
#   - up      : RSSHub /bilibili/user/video/{uid}
#   - tag     : RSSHub /bilibili/vsearch/{tag}/pubdate/0
#   - keyword : RSSHub /bilibili/vsearch/{keyword}/pubdate/0
# =====================================================

BILIBILI_FORWARDING_GROUP_DEFAULT_ID = "bilibili_default"
BILIBILI_FORWARDING_GROUP_DEFAULT_NAME = "转发组1"


def _bilibili_source_id(kind, value):
    return "%s:%s" % (str(kind or "up").strip(), str(value or "").strip())


def get_bilibili_rsshub_base():
    """Bilibili RSSHub 地址。"""
    return _bilibili_rsshub_base(_load_settings())


def _bilibili_rsshub_base(data):
    base = str(data.get("bilibili_rsshub_base") or "").strip()
    return (base or "http://localhost:1200").rstrip("/")


def get_bilibili_poll_interval():
    """Bilibili 轮询间隔，最短 30 秒。"""
    data = _load_settings()
    try:
        value = int(data.get("bilibili_poll_interval") or 300)
    except (OverflowError, TypeError, ValueError):
        return 300
    return value if value >= 30 else 300


def _get_bilibili_sources(data):
    base = _bilibili_rsshub_base(data)
    items = data.get("bilibili_sources")
    sources = []
    if not isinstance(items, list):
        return sources
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "up").strip().lower()
        value = str(item.get("value") or item.get("uid") or "").strip()
        if kind not in {"up", "tag", "keyword"} or not value:
            continue
        source_id = str(item.get("id") or _bilibili_source_id(kind, value)).strip()
        if not source_id:
            continue
        encoded = quote(value, safe="")
        allowed_types = {
            "视频": ("video", "/bilibili/user/video/" + encoded),
            "专栏": ("article", "/bilibili/user/article/" + encoded),
            "动态": ("dynamic", "/bilibili/user/dynamic/" + encoded),
        }
        raw_types = item.get("filter_bilibili_post_types")
        post_types = (
            [post_type for post_type in raw_types if post_type in allowed_types]
            if isinstance(raw_types, list)
            else ["视频"]
        )
        if kind != "up":
            post_types = ["视频"]
        if not post_types:
            post_types = ["视频"]
        feeds = []
        for post_type in post_types:
            suffix, path = allowed_types[post_type]
            if kind != "up":
                path = "/bilibili/vsearch/" + encoded + "/pubdate/0"
            feeds.append({
                "type": post_type,
                "url": base + path,
                "state_key": source_id if post_type == "视频" else source_id + "::" + suffix,
            })
        sources.append({
            "id": source_id,
            "kind": kind,
            "value": value,
            "url": feeds[0]["url"],
            "feeds": feeds,
            "name": value,
            "enabled": item.get("enabled") is True,
            "filter_bilibili_post_types": post_types,
            "filter_message_types": normalize_message_types(
                item.get("filter_message_types"), BILIBILI_MESSAGE_TYPES
            ),
        })
    return sources


def get_bilibili_sources():
    """返回归一化 Bilibili 来源列表。"""
    return _get_bilibili_sources(_load_settings())


def _bilibili_source_ids(data, only_enabled=False):
    ids = set()
    for item in data.get("bilibili_sources") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "up").strip().lower()
        value = str(item.get("value") or item.get("uid") or "").strip()
        if kind not in {"up", "tag", "keyword"} or not value:
            continue
        if only_enabled and item.get("enabled") is not True:
            continue
        ids.add(str(item.get("id") or _bilibili_source_id(kind, value)).strip())
    return ids


def _ensure_bilibili_forwarding_groups(data):
    """Bilibili 转发组内存归一化，不在读取时写盘。"""
    raw_groups = data.get("bilibili_forwarding_groups")
    groups = raw_groups if isinstance(raw_groups, list) else []
    valid_sources = _bilibili_source_ids(data)
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
        group_id = str(item.get("id") or "").strip()
        if not group_id or group_id in seen_ids:
            continue
        seen_ids.add(group_id)
        sources = []
        raw_sources = item.get("sources")
        if not isinstance(raw_sources, list):
            raw_sources = []
        for source_id in raw_sources:
            source_id = str(source_id).strip()
            if source_id and source_id in valid_sources and source_id not in sources:
                sources.append(source_id)
        target_groups = []
        raw_group_openids = item.get("groups")
        if not isinstance(raw_group_openids, list):
            raw_group_openids = []
        for openid in raw_group_openids:
            openid = str(openid).strip()
            if openid and openid in valid_groups and openid not in target_groups:
                target_groups.append(openid)
        normalized.append({
            "id": group_id,
            "name": str(item.get("name") or "").strip() or group_id,
            "enabled": item.get("enabled", True) is not False,
            "sources": sources,
            "groups": target_groups,
        })

    if not normalized:
        normalized = [{
            "id": BILIBILI_FORWARDING_GROUP_DEFAULT_ID,
            "name": BILIBILI_FORWARDING_GROUP_DEFAULT_NAME,
            "enabled": True,
            "sources": sorted(_bilibili_source_ids(data, only_enabled=True)),
            "groups": _enabled_group_openids(data),
        }]
    data["bilibili_forwarding_groups"] = normalized
    return normalized


def get_bilibili_forwarding_groups():
    return _ensure_bilibili_forwarding_groups(_load_settings())


def _get_bilibili_groups_by_source(data):
    enabled_sources = _bilibili_source_ids(data, only_enabled=True)
    enabled_groups = _enabled_group_openids(data)
    if not enabled_sources or not enabled_groups:
        return {}
    targets_by_source = {source_id: set() for source_id in enabled_sources}
    for forwarding_group in _ensure_bilibili_forwarding_groups(data):
        if forwarding_group.get("enabled", True) is False:
            continue
        for source_id in forwarding_group["sources"]:
            if source_id in targets_by_source:
                targets_by_source[source_id].update(forwarding_group["groups"])
    return {
        source_id: [group for group in enabled_groups if group in targets]
        for source_id, targets in targets_by_source.items()
    }


def get_bilibili_groups_for_source(source_id):
    """Bilibili 来源 → QQ 目标群的唯一实时路由入口。"""
    data = _load_settings()
    source_id = str(source_id or "").strip()
    if not source_id or source_id not in _bilibili_source_ids(data, only_enabled=True):
        return []
    enabled_groups = _enabled_group_openids(data)
    if not enabled_groups:
        return []
    targets = set()
    for forwarding_group in _ensure_bilibili_forwarding_groups(data):
        if forwarding_group.get("enabled", True) is not False and source_id in forwarding_group["sources"]:
            targets.update(forwarding_group["groups"])
    return [group for group in enabled_groups if group in targets]


def get_bilibili_polling_snapshot():
    """用同一份 settings 快照生成一轮 Bilibili 来源及目标群路由。"""
    data = _load_settings()
    return _get_bilibili_sources(data), _get_bilibili_groups_by_source(data)


def get_bilibili_status_snapshot():
    """用同一份 settings 快照生成 Bilibili 状态接口所需配置。"""
    data = _load_settings()
    return {
        "sources": _get_bilibili_sources(data),
        "protection": normalize_protection_config(data.get("bilibili_protection")),
        "forwarding_groups": _ensure_bilibili_forwarding_groups(data),
    }


# =====================================================
# YouTube 频道路由（独立于 Discord/X/Bilibili）
#
# 来源只支持频道：value 为 UC 频道 ID 或 @handle；@handle 由运行时
# 解析为频道 ID 后读取 YouTube 官方 feeds/videos.xml。
# =====================================================

YOUTUBE_FORWARDING_GROUP_DEFAULT_ID = "youtube_default"
YOUTUBE_FORWARDING_GROUP_DEFAULT_NAME = "转发组1"


def _youtube_source_id(value):
    return youtube_source_id(value)


def get_youtube_poll_interval():
    """YouTube 轮询间隔，最短 30 秒。"""
    data = _load_settings()
    try:
        value = int(data.get("youtube_poll_interval") or 300)
    except (OverflowError, TypeError, ValueError):
        return 300
    return value if value >= 30 else 300


def _get_youtube_sources(data):
    items = data.get("youtube_sources")
    sources = []
    if not isinstance(items, list):
        return sources
    for item in items:
        if not isinstance(item, dict):
            continue
        value = normalize_channel_value(
            item.get("value") or item.get("channel") or ""
        )
        if not value:
            continue
        source_id = str(item.get("id") or youtube_source_id(value)).strip()
        if not source_id:
            continue
        sources.append({
            "id": source_id,
            "kind": "channel",
            "value": value,
            "name": str(item.get("name") or value).strip() or value,
            "enabled": item.get("enabled") is True,
            "url": channel_feed_url(value) if value.startswith("UC") else "",
            "filter_message_types": normalize_message_types(
                item.get("filter_message_types"), YOUTUBE_MESSAGE_TYPES
            ),
        })
    return sources


def get_youtube_sources():
    """返回归一化 YouTube 来源列表。"""
    return _get_youtube_sources(_load_settings())


def _youtube_source_ids(data, only_enabled=False):
    ids = set()
    for item in data.get("youtube_sources") or []:
        if not isinstance(item, dict):
            continue
        value = normalize_channel_value(
            item.get("value") or item.get("channel") or ""
        )
        if not value:
            continue
        if only_enabled and item.get("enabled") is not True:
            continue
        ids.add(str(item.get("id") or youtube_source_id(value)).strip())
    return ids


def _ensure_youtube_forwarding_groups(data):
    """YouTube 转发组内存归一化，不在读取时写盘。"""
    raw_groups = data.get("youtube_forwarding_groups")
    groups = raw_groups if isinstance(raw_groups, list) else []
    valid_sources = _youtube_source_ids(data)
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
        group_id = str(item.get("id") or "").strip()
        if not group_id or group_id in seen_ids:
            continue
        seen_ids.add(group_id)
        sources = []
        raw_sources = item.get("sources")
        if not isinstance(raw_sources, list):
            raw_sources = []
        for source_id in raw_sources:
            source_id = str(source_id).strip()
            if source_id and source_id in valid_sources and source_id not in sources:
                sources.append(source_id)
        target_groups = []
        raw_group_openids = item.get("groups")
        if not isinstance(raw_group_openids, list):
            raw_group_openids = []
        for openid in raw_group_openids:
            openid = str(openid).strip()
            if openid and openid in valid_groups and openid not in target_groups:
                target_groups.append(openid)
        normalized.append({
            "id": group_id,
            "name": str(item.get("name") or "").strip() or group_id,
            "enabled": item.get("enabled", True) is not False,
            "sources": sources,
            "groups": target_groups,
        })

    if not normalized:
        normalized = [{
            "id": YOUTUBE_FORWARDING_GROUP_DEFAULT_ID,
            "name": YOUTUBE_FORWARDING_GROUP_DEFAULT_NAME,
            "enabled": True,
            "sources": sorted(_youtube_source_ids(data, only_enabled=True)),
            "groups": _enabled_group_openids(data),
        }]
    data["youtube_forwarding_groups"] = normalized
    return normalized


def get_youtube_forwarding_groups():
    return _ensure_youtube_forwarding_groups(_load_settings())


def _get_youtube_groups_by_source(data):
    enabled_sources = _youtube_source_ids(data, only_enabled=True)
    enabled_groups = _enabled_group_openids(data)
    if not enabled_sources or not enabled_groups:
        return {}
    targets_by_source = {source_id: set() for source_id in enabled_sources}
    for forwarding_group in _ensure_youtube_forwarding_groups(data):
        if forwarding_group.get("enabled", True) is False:
            continue
        for source_id in forwarding_group["sources"]:
            if source_id in targets_by_source:
                targets_by_source[source_id].update(forwarding_group["groups"])
    return {
        source_id: [group for group in enabled_groups if group in targets]
        for source_id, targets in targets_by_source.items()
    }


def get_youtube_groups_for_source(source_id):
    """YouTube 来源 → QQ 目标群的唯一实时路由入口。"""
    data = _load_settings()
    source_id = str(source_id or "").strip()
    if not source_id or source_id not in _youtube_source_ids(data, only_enabled=True):
        return []
    enabled_groups = _enabled_group_openids(data)
    if not enabled_groups:
        return []
    targets = set()
    for forwarding_group in _ensure_youtube_forwarding_groups(data):
        if forwarding_group.get("enabled", True) is not False and source_id in forwarding_group["sources"]:
            targets.update(forwarding_group["groups"])
    return [group for group in enabled_groups if group in targets]


def _youtube_source_filter(data, source_id):
    for item in data.get("youtube_sources") or []:
        if not isinstance(item, dict):
            continue
        value = normalize_channel_value(
            item.get("value") or item.get("channel") or ""
        )
        candidate_id = str(item.get("id") or youtube_source_id(value)).strip()
        if not candidate_id or candidate_id != str(source_id):
            continue
        return {
            "filter_keywords": [
                str(keyword).strip()
                for keyword in (item.get("filter_keywords") or [])
                if str(keyword).strip()
            ],
            "filter_message_types": _source_message_types(item, YOUTUBE_MESSAGE_TYPES),
        }
    return {
        "filter_keywords": [],
        "filter_message_types": list(YOUTUBE_MESSAGE_TYPES),
    }


def get_youtube_source_filter(source_id):
    """返回指定 YouTube 来源的关键词与消息类型筛选。"""
    return _youtube_source_filter(_load_settings(), source_id)


def get_youtube_source_delivery_config(source_id):
    """用同一 settings 快照生成 YouTube 来源筛选与翻译配置。"""
    data = _load_settings()
    return {
        "filter": _youtube_source_filter(data, source_id),
        "translation": _translation_config_for_item_data(
            data, "translation", "youtube_sources", source_id
        ),
    }


def get_youtube_protection():
    """返回 YouTube → QQ 全局限速与积压保护配置。"""
    return normalize_protection_config(_load_settings().get("youtube_protection"))


def get_youtube_polling_snapshot():
    """用同一份 settings 快照生成一轮 YouTube 来源及目标群路由。"""
    data = _load_settings()
    return _get_youtube_sources(data), _get_youtube_groups_by_source(data)


def get_youtube_status_snapshot():
    """用同一份 settings 快照生成 YouTube 状态接口所需配置。"""
    data = _load_settings()
    return {
        "sources": _get_youtube_sources(data),
        "protection": normalize_protection_config(data.get("youtube_protection")),
        "forwarding_groups": _ensure_youtube_forwarding_groups(data),
    }
