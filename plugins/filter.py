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
# 文件链接），因此没有独立的「视频」类型。
MESSAGE_TYPE_TEXT = "文字"
MESSAGE_TYPE_IMAGE = "图片"
MESSAGE_TYPE_AUDIO = "语音"
MESSAGE_TYPE_FILE = "文件"
MESSAGE_TYPE_LINK = "链接"
MESSAGE_TYPE_EMOJI = "表情"

ALL_MESSAGE_TYPES = (
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_AUDIO,
    MESSAGE_TYPE_FILE,
    MESSAGE_TYPE_LINK,
    MESSAGE_TYPE_EMOJI,
)

X_POST_TYPE_ORIGINAL = "原创"
X_POST_TYPE_RETWEET = "转发"
X_POST_TYPE_QUOTE = "引用"
ALL_X_POST_TYPES = (
    X_POST_TYPE_ORIGINAL,
    X_POST_TYPE_RETWEET,
    X_POST_TYPE_QUOTE,
)


def get_enabled_message_types(filter_config):
    """返回当前启用的消息类型集合（build_parts 按此逐组件过滤）。

    filter_config 缺省 filter_message_types（老配置）→ 全部类型启用；
    显式空列表 → 不转发任何内容组件（过滤后为空的消息不发送）；
    未知类型名被忽略，避免脏配置造成意外全过滤。"""
    types = filter_config.get("filter_message_types")
    if not isinstance(types, (list, tuple)):
        return set(ALL_MESSAGE_TYPES)
    enabled = set(ALL_MESSAGE_TYPES) & {
        str(t).strip()
        for t in types
        if str(t).strip()
    }
    return enabled


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
    """判断 Bilibili 投稿是否符合标签、关键词、标题和作者筛选。"""
    filter_config = filter_config or {}
    post = post or {}
    tags = [
        str(tag).strip().lstrip("#").lower()
        for tag in (post.get("tags") or [])
        if str(tag).strip()
    ]
    wanted_tags = [
        str(tag).strip().lstrip("#").lower()
        for tag in (filter_config.get("filter_tags") or [])
        if str(tag).strip()
    ]
    if wanted_tags:
        if tags:
            if not any(tag in tags for tag in wanted_tags):
                return False
        else:
            fallback_text = " ".join(
                [post.get("title") or "", post.get("description") or ""]
            ).lower()
            if not any(tag in fallback_text for tag in wanted_tags):
                return False

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
                " ".join(tags),
            ]
        ).lower()
        if not any(keyword in search_text for keyword in keywords):
            return False

    title = str(post.get("title") or "").lower()
    title_keywords = [
        str(keyword).strip().lower()
        for keyword in (filter_config.get("filter_title_keywords") or [])
        if str(keyword).strip()
    ]
    if title_keywords and not any(keyword in title for keyword in title_keywords):
        return False

    exclude_keywords = [
        str(keyword).strip().lower()
        for keyword in (filter_config.get("filter_exclude_keywords") or [])
        if str(keyword).strip()
    ]
    if exclude_keywords and any(keyword in title for keyword in exclude_keywords):
        return False

    authors = [
        str(author).strip().lower()
        for author in (filter_config.get("filter_authors") or [])
        if str(author).strip()
    ]
    if authors and str(post.get("author") or "").strip().lower() not in authors:
        return False

    return True
