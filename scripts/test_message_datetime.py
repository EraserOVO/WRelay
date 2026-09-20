"""验证 Discord/X 时间日期显示开关与默认关闭行为。"""

import asyncio

import nonebot

nonebot.init()

from plugins import fetch, x_rss
from plugins.message_time import format_message_datetime


def text_of(parts):
    return "".join(
        (getattr(part, "data", None) or {}).get("text", "")
        for part in parts
    )


async def main():
    discord_message = {
        "channel_name": "测试频道",
        "username": "tester",
        "content": "Hello",
        "created_at": "2026-09-20T12:34:56Z",
        "attachments": [],
    }
    parts, _media, _has_content = await fetch.build_parts(discord_message)
    assert "时间：" not in text_of(parts)
    parts, _media, _has_content = await fetch.build_parts(
        discord_message,
        show_datetime=True,
    )
    discord_time = format_message_datetime(discord_message["created_at"])
    assert "时间：" + discord_time in text_of(parts)

    post = {
        "is_retweet": False,
        "rt_author": "",
        "text": "Hello",
        "quote": None,
        "media": [],
        "pub_date": "Sun, 20 Sep 2026 12:34:56 GMT",
    }
    rendered = await x_rss._render_post(post, "tester")
    assert "时间：" not in text_of(rendered[0])
    rendered = await x_rss._render_post(post, "tester", show_datetime=True)
    x_time = format_message_datetime(post["pub_date"])
    assert "时间：" + x_time in text_of(rendered[0])
    print("PASS - Discord/X 时间日期显示开关")


if __name__ == "__main__":
    asyncio.run(main())
