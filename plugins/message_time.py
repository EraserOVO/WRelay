"""消息时间日期的统一解析与展示格式。"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


_FORMATS = (
    "%a %b %d %H:%M:%S %z %Y",  # X Syndication created_at
    "%a %b %d %H:%M:%S %Y",     # 无时区的 X 时间文本
)


def format_message_datetime(value):
    """把 Discord/X 的时间值转换为本地时间的 ``YYYY-MM-DD HH:MM:SS``。"""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return ""
    else:
        text = str(value).strip()
        if not text:
            return ""
        parsed = None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
        if parsed is None:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                pass
        if parsed is None:
            for fmt in _FORMATS:
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    pass
        if parsed is None:
            return ""

    if parsed.tzinfo is None:
        # Discord/X 的无时区时间均按 UTC 处理，避免不同机器时区显示不一致。
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
