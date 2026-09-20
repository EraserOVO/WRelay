"""QQ 事件入站兼容处理。"""

from nonebot.adapters.qq import Adapter as QQAdapter


_installed = False


def _normalize_reply_element(item, default_message_type=0):
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
    return normalized, changed


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

    elements = data.get("msg_elements")
    if isinstance(elements, list):
        normalized_elements = []
        for item in elements:
            normalized_item, item_changed = _normalize_reply_element(
                item, default_message_type
            )
            normalized_elements.append(normalized_item)
            changed = changed or item_changed
        if changed:
            normalized_data["msg_elements"] = normalized_elements

    reply = data.get("reply")
    normalized_reply, reply_changed = _normalize_reply_element(
        reply, default_message_type
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
