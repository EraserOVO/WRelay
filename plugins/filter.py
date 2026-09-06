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
