"""QQ 事件入站兼容处理。"""

from nonebot.adapters.qq import Adapter as QQAdapter


_installed = False


def _normalize_reply_element(
    item, default_message_type=0, fallback_content=""
):
    if not isinstance(item, dict):
        return item, False
    normalized = dict(item)
    changed = False
    if not isinstance(normalized.get("message_type"), int):
        normalized["message_type"] = default_message_type
        changed = True
    if not isinstance(normalized.get("msg_idx"), str):
        normalized["msg_idx"] = ""
        changed = True
    content = normalized.get("content")
    if not isinstance(content, str):
        normalized["content"] = fallback_content
        changed = True
    elif not content.strip() and fallback_content:
        normalized["content"] = fallback_content
        changed = True
    return normalized, changed


def _parallel_message_content(data):
    """取 QQ 新引用载荷的引用内容；缺失该字段时返回空字符串。"""
    parallel = data.get("parallel_message")
    nodes = parallel.get("msg_nodes") if isinstance(parallel, dict) else None
    if not isinstance(nodes, list):
        return ""
    return "\n".join(
        node["content"]
        for node in nodes
        if isinstance(node, dict)
        and isinstance(node.get("content"), str)
        and node["content"].strip()
    )


def _reply_ref_index(data):
    scene = data.get("message_scene")
    ext = scene.get("ext") if isinstance(scene, dict) else None
    if not isinstance(ext, list):
        return ""
    prefix = "ref_msg_idx="
    for value in ext:
        if isinstance(value, str) and value.startswith(prefix):
            return value[len(prefix):]
    return ""


def normalize_qq_dispatch_payload(payload):
    """补齐 QQ 引用消息元素的缺省字段，返回可继续交给适配器解析的 payload。"""
    data = getattr(payload, "data", None)
    if not isinstance(data, dict):
        return payload

    default_message_type = data.get("message_type")
    if not isinstance(default_message_type, int):
        default_message_type = 0
    normalized_data = dict(data)
    changed = False

    parallel_content = _parallel_message_content(data)
    ref_idx = _reply_ref_index(data)
    elements = data.get("msg_elements")
    if isinstance(elements, list):
        missing_content = [
            index
            for index, item in enumerate(elements)
            if isinstance(item, dict)
            and not (
                isinstance(item.get("content"), str)
                and item["content"].strip()
            )
        ]
        quote_index = None
        if parallel_content:
            if ref_idx:
                quote_index = next(
                    (
                        index
                        for index in missing_content
                        if elements[index].get("msg_idx") == ref_idx
                    ),
                    None,
                )
            if quote_index is None and len(missing_content) == 1:
                quote_index = missing_content[0]

        normalized_elements = []
        for index, item in enumerate(elements):
            normalized_item, item_changed = _normalize_reply_element(
                item,
                default_message_type,
                parallel_content if index == quote_index else "",
            )
            normalized_elements.append(normalized_item)
            changed = changed or item_changed
        if changed:
            normalized_data["msg_elements"] = normalized_elements

    reply = data.get("reply")
    normalized_reply, reply_changed = _normalize_reply_element(
        reply, default_message_type, parallel_content
    )
    if reply_changed:
        normalized_data["reply"] = normalized_reply
        changed = True

    if not changed:
        return payload
    if hasattr(payload, "model_copy"):
        return payload.model_copy(update={"data": normalized_data})
    payload.data = normalized_data
    return payload


def install_qq_event_compat():
    """安装一次 QQ 事件兼容钩子，不修改第三方虚拟环境文件。"""
    global _installed
    if _installed:
        return
    original_payload_to_event = QQAdapter.payload_to_event

    def payload_to_event(payload):
        return original_payload_to_event(normalize_qq_dispatch_payload(payload))

    QQAdapter.payload_to_event = staticmethod(payload_to_event)
    _installed = True
