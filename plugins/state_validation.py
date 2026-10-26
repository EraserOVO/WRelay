"""Strict validation shared by verified backups and maintenance checks."""
import math


def _cursor(value):
    text = str(value) if type(value) is int else value
    return isinstance(text, str) and 0 < len(text) <= 32 and text.isascii() and text.isdecimal()


def validate_runtime(name, data):
    if not isinstance(data, dict):
        raise ValueError(name + '：顶层必须是对象')
    if name == 'discord_last.json':
        for value in data.values():
            values = value.values() if isinstance(value, dict) else [value]
            if any(v not in (None, '') and not _cursor(v) for v in values):
                raise ValueError(name + '：游标无效')
    elif name in ('x_protection.json', 'bilibili_protection.json', 'youtube_protection.json'):
        if type(data.get('version', 1)) is not int or data.get('version', 1) != 1:
            raise ValueError(name + '：队列版本不支持')
        if not isinstance(data.get('queues', {}), dict):
            raise ValueError(name + '：queues 必须是对象')
        stamps = data.get('sent_at', [])
        if not isinstance(stamps, list) or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in stamps):
            raise ValueError(name + '：发送时间记录无效')
        for bucket in data.get('queues', {}).values():
            if not isinstance(bucket, dict):
                raise ValueError(name + '：来源队列无效')
            for key in ('items', 'deferred_skips'):
                items = bucket.get(key, [])
                if not isinstance(items, list):
                    raise ValueError(name + '：队列条目必须是列表')
                for item in items:
                    if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or not _cursor(item.get('cursor_id')):
                        raise ValueError(name + '：投稿标识或游标无效')
                    if key == 'items' and not isinstance(item.get('post'), dict):
                        raise ValueError(name + '：投稿内容无效')
    elif name == 'retry_pending.json':
        if type(data.get('version', 1)) is not int or data.get('version', 1) != 1 or not isinstance(data.get('entries'), list):
            raise ValueError(name + '：重试队列结构无效')
        for item in data['entries']:
            if not isinstance(item, dict) or not isinstance(item.get('message'), dict):
                raise ValueError(name + '：重试内容无效')
            if any(not isinstance(item.get(k), str) or not item[k].strip() for k in ('channel_id', 'message_id')):
                raise ValueError(name + '：重试标识无效')
            groups = item.get('groups')
            if not isinstance(groups, list) or not groups or any(not isinstance(g, str) or not g.strip() for g in groups):
                raise ValueError(name + '：重试群无效')
            stamp = item.get('next_try_at')
            if type(item.get('attempts', 0)) is not int or item.get('attempts', 0) < 0 or type(stamp) not in (int, float) or not math.isfinite(stamp):
                raise ValueError(name + '：重试时间或次数无效')
    elif name in ('x_rss.json', 'bilibili.json', 'youtube.json'):
        if not isinstance(data.get('sources', {}), dict):
            raise ValueError(name + '：来源状态无效')
