# =====================================================
# Discord 频道消息筛选（纯函数）
#
# 单一职责：判断一条消息是否应该被转发。
# 不碰 I/O、不碰 fetch、不碰 send、不碰日志。
# 由 relay.py 和 backfill.py 共用，保证筛选逻辑唯一。
#
# 筛选规则：
# - user_ids 为空 → 不限制用户
# - keywords 为空 → 不限制关键词
# - 两者同时存在 → AND（必须同时满足）
# - 多个 user_id 之间 → OR
# - 多个 keywords 之间 → OR
# - 关键词使用普通文本包含匹配，不区分大小写，不使用正则
# - 消息类型（filter_message_types）与用户名/关键词正交：用户名/关键词
#   先判断整条消息是否放行；类型筛选再按消息内部的各个内容组件分别过滤
#   （由 plugins/fetch.py build_parts 按 get_enabled_message_types() 渲染）。
#   老配置没有 filter_message_types 字段 → 全部类型启用。
# =====================================================

# 消息类型（与前端筛选弹窗按钮、plugins/fetch.py build_parts 的组件一一对应）。
# 注意：Discord 视频附件当前按「文件」转发（附件未匹配图片/音频扩展名时渲染为
# 文件链接），因此 Discord 没有独立的「视频」类型。
MESSAGE_TYPE_TEXT = "文字"
MESSAGE_TYPE_IMAGE = "图片"
MESSAGE_TYPE_AUDIO = "语音"
MESSAGE_TYPE_FILE = "文件"
MESSAGE_TYPE_LINK = "链接"
MESSAGE_TYPE_EMOJI = "表情"
# X/Bilibili 的媒体槽位就是视频，它们用「视频」而不是通用的「文件」。
MESSAGE_TYPE_VIDEO = "视频"

ALL_MESSAGE_TYPES = (
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_AUDIO,
    MESSAGE_TYPE_FILE,
    MESSAGE_TYPE_LINK,
    MESSAGE_TYPE_EMOJI,
)

# =====================================================
# 各链路实际支持的消息类型
#
# 六类型集合描述的是 Discord 消息的内容组件（正文 / 附件 / Embed / Emoji）。
# X 推文与 Bilibili 投稿没有这种组件划分，能筛选的只是它们各自渲染出的部分，
# 因此每条链路各自声明支持集，前端按此渲染按钮、后端按此归一化配置。
# =====================================================

# Discord：正文、附件（图片/语音/文件）、Embed 链接、自定义表情，六类齐备。
DISCORD_MESSAGE_TYPES = ALL_MESSAGE_TYPES

# X：只有正文（含链接与话题）与媒体（图片/视频）两类组件。
# 推文里没有独立链接、音频或表情组件，故不提供这三项。
X_MESSAGE_TYPES = (
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_VIDEO,
)

# Bilibili：标题/正文（文字）、封面（图片）、投稿地址（链接）、视频本体（视频）。
BILIBILI_MESSAGE_TYPES = (
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_LINK,
    MESSAGE_TYPE_VIDEO,
)

# YouTube 第一期只转发标题（文字）、封面（图片）与视频链接，不含视频本体。
YOUTUBE_MESSAGE_TYPES = (
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_LINK,
)

# 手动转发全局筛选字段 → 该链路支持的类型集。
MANUAL_RELAY_FILTER_TYPES = {
    "qq_c2c_relay_filter": DISCORD_MESSAGE_TYPES,
    "x_c2c_relay_filter": X_MESSAGE_TYPES,
    "bilibili_c2c_relay_filter": BILIBILI_MESSAGE_TYPES,
}

# 旧配置的类型别名（历史值 → 规范名）。
# X/Bilibili 早期与 Discord 共用「文件」表示视频本体；改名后它们的规范名是
# 「视频」。只在别名目标属于该链路支持集时才映射，因此 Discord 的「文件」
# （真正的附件类型）不受影响。
LEGACY_MESSAGE_TYPE_ALIASES = {
    MESSAGE_TYPE_FILE: MESSAGE_TYPE_VIDEO,
}


def normalize_message_types(values, supported):
    """把配置里的类型名归一化为该链路的规范名，并丢弃不支持项。

    返回列表，顺序按 ``supported``，保证面板显示与落盘顺序稳定。
    ``values`` 为 None/非列表时返回全部支持类型；显式空列表返回空列表
    （调用方据此区分"用户清空"与"未配置"）。
    """
    allowed = tuple(supported)
    if not isinstance(values, (list, tuple)):
        return list(allowed)
    requested = set()
    for item in values:
        name = str(item).strip()
        if not name:
            continue
        if name in allowed:
            requested.add(name)
            continue
        # 历史值：映射到规范名（仅当该规范名属于本链路）。
        alias = LEGACY_MESSAGE_TYPE_ALIASES.get(name)
        if alias and alias in allowed:
            requested.add(alias)
    return [name for name in allowed if name in requested]

X_POST_TYPE_ORIGINAL = "原创"
X_POST_TYPE_RETWEET = "转发"
X_POST_TYPE_QUOTE = "引用"
ALL_X_POST_TYPES = (
    X_POST_TYPE_ORIGINAL,
    X_POST_TYPE_RETWEET,
    X_POST_TYPE_QUOTE,
)


def get_enabled_message_types(filter_config, supported=None):
    """返回当前启用的消息类型集合（build_parts 按此逐组件过滤）。

    缺省 ``filter_message_types`` → 全部支持类型启用；显式空列表 → 不转发任何组件。
    给定 ``supported`` 时按该链路的类型集规范化，并将 X/Bilibili 旧名「文件」
    迁移为「视频」。显式列表若只含不支持的旧值，则视为未筛选，避免旧配置
    意外让来源全部停止投递。
    """
    allowed = tuple(supported) if supported else ALL_MESSAGE_TYPES
    types = filter_config.get("filter_message_types")
    if not isinstance(types, (list, tuple)):
        return set(allowed)
    has_explicit_values = any(str(t).strip() for t in types)
    if not has_explicit_values:
        return set()
    normalized = normalize_message_types(types, allowed)
    return set(normalized) if normalized else set(allowed)


def check_message_filter(
    filter_config: dict,
    author_username: str,
    content: str,
    embeds: list = None,
) -> bool:
    """判断消息是否通过筛选，返回 True 表示应该转发。

    filter_config: {"filter_usernames": [...], "filter_keywords": [...]}
    author_username: 消息作者的 Discord username（字符串）
    content: 消息原始文本内容
    embeds: 消息嵌入列表 [{"title": "..."}, ...]，可选
    """
    usernames = filter_config.get("filter_usernames") or []
    keywords = filter_config.get("filter_keywords") or []

    # 无任何筛选条件 → 全部转发
    if not usernames and not keywords:
        return True

    # 用户筛选（OR，不区分大小写）
    if usernames:
        author_lower = (author_username or "").lower()
        if not any(
            str(u).strip().lower() == author_lower
            for u in usernames
            if str(u).strip()
        ):
            return False

    # 关键词筛选（OR，不区分大小写）
    if keywords:
        parts = [content or ""]
        for embed in (embeds or []):
            title = None
            if isinstance(embed, dict):
                title = embed.get("title")
            else:
                title = getattr(embed, "title", None)
            if title:
                parts.append(str(title))
        search_text = " ".join(parts).lower()
        if not any(kw.lower() in search_text for kw in keywords if kw):
            return False

    return True


def check_x_post_filter(filter_config: dict, post: dict) -> bool:
    """判断 X RSS 推文是否通过关键词与推文类型筛选。

    关键词匹配正文、话题标签、转发作者及引用内容；多个关键词为 OR。
    ``filter_post_types`` 缺省或包含全部类型时不过滤，支持原创、转发、引用。
    """
    filter_config = filter_config or {}
    post = post or {}
    keywords = [str(k).strip().lower() for k in (filter_config.get("filter_keywords") or []) if str(k).strip()]
    if keywords:
        parts = [post.get("text") or "", post.get("rt_author") or ""]
        hashtags = [str(tag).strip() for tag in (post.get("hashtags") or []) if str(tag).strip()]
        parts.extend(hashtags)
        parts.extend("#" + tag.lstrip("#") for tag in hashtags)
        quote = post.get("quote") or {}
        parts.extend([quote.get("author") or "", quote.get("text") or ""])
        search_text = " ".join(str(part) for part in parts).lower()
        if not any(keyword in search_text for keyword in keywords):
            return False

    post_types = filter_config.get("filter_post_types")
    if isinstance(post_types, (list, tuple)):
        enabled = {
            str(item).strip()
            for item in post_types
            if str(item).strip() in ALL_X_POST_TYPES
        }
        if enabled and len(enabled) < len(ALL_X_POST_TYPES):
            matched = set()
            if post.get("is_retweet"):
                matched.add(X_POST_TYPE_RETWEET)
            elif post.get("quote"):
                matched.add(X_POST_TYPE_QUOTE)
            else:
                matched.add(X_POST_TYPE_ORIGINAL)
            if not matched & enabled:
                return False

    return True


def check_bilibili_video_filter(filter_config: dict, post: dict) -> bool:
    """判断 Bilibili 投稿是否符合关键词和投稿类型筛选。"""
    filter_config = filter_config or {}
    post = post or {}
    tags = [
        str(tag).strip().lstrip("#")
        for tag in (post.get("tags") or [])
        if str(tag).strip()
    ]
    keywords = [
        str(keyword).strip().lower()
        for keyword in (filter_config.get("filter_keywords") or [])
        if str(keyword).strip()
    ]
    if keywords:
        search_text = " ".join(
            [
                post.get("title") or "",
                post.get("description") or "",
                post.get("author") or "",
                " ".join(tags),
            ]
        ).lower()
        if not any(keyword in search_text for keyword in keywords):
            return False

    post_types = filter_config.get("filter_bilibili_post_types")
    if isinstance(post_types, (list, tuple)) and post_types:
        post_type = str(post.get("post_type") or "视频").strip()
        if post_type not in {str(item).strip() for item in post_types if str(item).strip()}:
            return False

    return True


def check_youtube_video_filter(filter_config: dict, post: dict) -> bool:
    """判断 YouTube 视频是否通过来源关键词筛选（标题/简介/频道名）。"""
    filter_config = filter_config or {}
    post = post or {}
    keywords = [
        str(keyword).strip().lower()
        for keyword in (filter_config.get("filter_keywords") or [])
        if str(keyword).strip()
    ]
    if not keywords:
        return True
    search_text = " ".join(
        [
            str(post.get("title") or ""),
            str(post.get("description") or ""),
            str(post.get("author") or ""),
        ]
    ).lower()
    return any(keyword in search_text for keyword in keywords)
