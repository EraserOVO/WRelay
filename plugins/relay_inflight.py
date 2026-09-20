"""Discord 实时转发与启动补发共享的消息在途标记。"""


_processing = set()


def claim_processing(channel_id, message_id):
    """登记实时路径正在处理的消息；已登记时返回 False。"""
    key = (str(channel_id), str(message_id))
    if key in _processing:
        return False
    _processing.add(key)
    return True


def release_processing(channel_id, message_id):
    """无论发送结果如何，结束实时路径的在途登记。"""
    _processing.discard((str(channel_id), str(message_id)))


def is_processing(channel_id, message_id):
    """返回消息是否正由实时路径从队列到发送全过程处理。"""
    return (str(channel_id), str(message_id)) in _processing
