"""验证 plugins/fetch.py 的 Discord 回复引用 → QQ 引用行逻辑。

覆盖：
1. REST 内联 referenced_message → 引用行一步到位
2. 事件路径仅有 message_reference.message_id → 回退抓取原文 → 引用行
3. 回退抓取失败 → 静默跳过引用行，当前消息正常转发
4. 非回复消息 → 输出与改动前一致，无引用行
5. 长正文截断到 REFERENCE_TEXT_LIMIT

直接打印 build_parts 产出的 text_parts，检查实际转发格式。

用法：.venv/Scripts/python.exe scripts/test_fetch_reference.py
"""
import asyncio
import sys
import unittest.mock as mock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import nonebot

nonebot.init()

from plugins import fetch
from plugins.filter import check_message_filter


def text_parts_str(text_parts):
    """把 QQ 文本消息段拼成纯文本"""
    return "".join(
        (getattr(p, "data", None) or {}).get("text", "")
        for p in text_parts
    )


def make_event(channel_id, content, ref=None, author_name="eve"):
    """构造兼容 normalize_event 的事件替身（getattr 取值即可）"""
    author = type("A", (), {"username": author_name, "global_name": author_name.capitalize()})()
    return type(
        "Ev",
        (),
        {
            "channel_id": channel_id,
            "content": content,
            "embeds": [],
            "attachments": [],
            "message_reference": ref,
            "author": author,
        },
    )()


async def test_inline_reference():
    # REST：内联 referenced_message 一步到位，无需额外请求
    raw = {
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice", "global_name": "Alice"},
        "content": "当前消息正文",
        "embeds": [],
        "attachments": [],
        "message_reference": {"message_id": "999", "channel_id": "111", "guild_id": "1"},
        "referenced_message": {
            "id": "999",
            "channel_id": "111",
            "author": {"username": "bob", "global_name": "Bob"},
            "content": "被回复内容",
            "embeds": [],
            "attachments": [],
        },
    }
    msg = fetch.normalize_api_message(raw)
    assert msg["reference"]["username"] == "Bob", msg["reference"]
    assert msg["reference"]["content"] == "被回复内容"
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎ Bob：被回复内容" in text, text
    # 引用行在 header 之后、当前消息正文之前
    assert text.index("↩︎ Bob：被回复内容") < text.index("当前消息正文"), text
    assert has_content
    print("[OK] 内联 referenced_message →\n" + text)


async def test_inline_reference_malformed_fields():
    # 内联引用坏作者降级为 unknown，坏正文跳过引用行；当前消息继续转发。
    base = {
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice"},
        "content": "当前消息正文",
        "embeds": [],
        "attachments": [],
        "message_reference": {"message_id": "999", "channel_id": "111"},
    }
    malformed_author = dict(base)
    malformed_author["referenced_message"] = {
        "id": "999",
        "author": "invalid author object",
        "content": "被回复内容",
        "mentions": [],
    }
    msg = fetch.normalize_api_message(malformed_author)
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎ unknown：被回复内容" in text, text
    assert "当前消息正文" in text, text
    assert has_content

    malformed_content = dict(base)
    malformed_content["referenced_message"] = {
        "id": "999",
        "author": {"global_name": "Bob"},
        "content": 12345,
        "mentions": [],
    }
    msg = fetch.normalize_api_message(malformed_content)
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎" not in text, text
    assert "当前消息正文" in text, text
    assert has_content
    print("[OK] 内联引用字段结构损坏 → 安全降级，当前消息正常转发")


async def test_malformed_message_author():
    # 主消息作者不是对象时使用 unknown，正文仍正常转发。
    msg = fetch.normalize_api_message({
        "id": "1001",
        "channel_id": "111",
        "author": 7,
        "content": "当前消息正文",
        "embeds": [],
        "attachments": [],
    })
    assert msg["username"] == "unknown", msg
    assert msg["author_username"] == ""
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "[unknown]" in text, text
    assert "当前消息正文" in text, text
    assert has_content
    print("[OK] 主消息作者结构损坏 → unknown 降级，正文正常转发")


async def test_malformed_author_username():
    # REST username 非字符串时筛选器收到空串，而不是抛错。
    msg = fetch.normalize_api_message({
        "id": "1001",
        "channel_id": "111",
        "author": {"global_name": "Alice", "username": 7},
        "content": "当前消息正文",
        "embeds": [],
        "attachments": [],
    })
    assert msg["author_username"] == "", msg
    assert not check_message_filter(
        {"filter_usernames": ["alice"]},
        msg["author_username"],
        msg["content"],
    )
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "当前消息正文" in text, text
    assert has_content
    print("[OK] REST username 类型损坏 → 筛选安全降级，正文正常渲染")


async def test_malformed_message_content():
    # REST 正文类型损坏时归一为空，不影响消息结构处理。
    msg = fetch.normalize_api_message({
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice"},
        "content": 12345,
        "embeds": [],
        "attachments": [],
    })
    assert msg["content"] == "", msg
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "12345" not in text, text
    assert not has_content
    print("[OK] 主消息正文类型损坏 → 归一为空，不触发渲染异常")


async def test_fetch_message_rejects_invalid_response():
    class InvalidJsonResponse:
        status_code = 200
        text = "<html>upstream error</html>"

        def json(self):
            raise ValueError("invalid JSON response")

    class WrongRootResponse:
        status_code = 200
        text = "[]"

        def json(self):
            return []

    with (
        mock.patch.object(
            fetch,
            "parse_discord_url",
            return_value={"channel_id": "111", "message_id": "999"},
        ),
        mock.patch.object(fetch, "get_discord_token", return_value="test-token"),
        mock.patch.object(
            fetch,
            "_discord_request",
            new_callable=mock.AsyncMock,
            side_effect=[InvalidJsonResponse(), WrongRootResponse()],
        ) as request,
        mock.patch.object(fetch.logger, "warning") as warning,
    ):
        assert await fetch.fetch_message("https://discord.example/message") == (
            None,
            "响应格式无效",
        )
        assert await fetch.fetch_message("https://discord.example/message") == (
            None,
            "响应格式无效",
        )
        assert request.await_count == 2
        assert warning.call_count == 2
    print("[OK] REST 单条消息无效 JSON/根节点 → 返回格式错误")


async def test_malformed_message_collections():
    # 非列表的媒体集合按空列表处理，正文仍可渲染。
    msg = fetch.normalize_api_message({
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice"},
        "content": "当前消息正文",
        "embeds": 7,
        "attachments": 7,
    })
    assert msg["embeds"] == [], msg
    assert msg["attachments"] == [], msg
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "当前消息正文" in text, text
    assert has_content
    print("[OK] REST 媒体集合类型损坏 → 归一为空列表，正文正常转发")


async def test_malformed_media_fields():
    # 媒体对象里的非字符串字段被清理，正文仍正常渲染。
    msg = fetch.normalize_api_message({
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice"},
        "content": "当前消息正文",
        "embeds": [{
            "title": 123,
            "url": 456,
            "image": {"url": 789},
        }],
        "attachments": [
            {"url": 987, "filename": "broken.png"},
            {"url": "https://cdn.example/file", "filename": 654},
        ],
    })
    assert msg["embeds"] == [{"title": None, "url": None, "image_url": None}], msg
    assert msg["attachments"] == [{
        "url": "https://cdn.example/file",
        "filename": "",
    }], msg
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "当前消息正文" in text, text
    assert has_content
    print("[OK] REST Embed/附件字段类型损坏 → 字段清理，正文正常转发")


async def test_event_reference_fallback_success():
    # 事件：只有 message_reference.message_id，回退抓取原文成功
    ref = type("R", (), {"message_id": 999, "channel_id": 111})()
    event = make_event("111", "事件正文", ref=ref, author_name="carol")

    msg = fetch.normalize_event(event, "频道A")
    assert msg["reference"]["message_id"] == "999", msg["reference"]
    assert msg["reference"]["content"] == "", msg["reference"]

    async def fake_fetch(channel_id, message_id):
        assert channel_id == "111" and message_id == "999"
        return {
            "id": "999",
            "channel_id": "111",
            "author": {"username": "dave", "global_name": "Dave"},
            "content": "回退抓取到的原文",
            "embeds": [],
            "attachments": [],
        }

    with mock.patch.object(fetch, "_fetch_reference_message", new=fake_fetch):
        parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎ Dave：回退抓取到的原文" in text, text
    assert text.index("↩︎ Dave") < text.index("事件正文"), text
    assert has_content
    print("[OK] 事件回退抓取成功 →\n" + text)


async def test_reference_fetch_failure():
    # 回退抓取失败 → 静默跳过引用行，当前消息仍正常转发
    ref = type("R", (), {"message_id": 999, "channel_id": 111})()
    event = make_event("111", "当前消息仍转发", ref=ref, author_name="eve")

    msg = fetch.normalize_event(event, "频道A")
    assert msg["reference"] is not None

    async def fake_fetch(channel_id, message_id):
        return None  # 抓取失败

    with mock.patch.object(fetch, "_fetch_reference_message", new=fake_fetch):
        parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎" not in text, text
    assert "当前消息仍转发" in text, text
    assert has_content
    print("[OK] 回退抓取失败 → 无引用行，当前消息正常 →\n" + text)


async def test_malformed_reference_author():
    # 作者结构损坏时以 unknown 显示引用，当前消息仍正常转发。
    ref = type("R", (), {"message_id": 999, "channel_id": 111})()
    event = make_event("111", "当前消息仍转发", ref=ref, author_name="eve")
    msg = fetch.normalize_event(event, "频道A")

    async def fake_fetch(channel_id, message_id):
        return {
            "author": "invalid author object",
            "content": "被回复内容",
            "mentions": [],
        }

    with mock.patch.object(fetch, "_fetch_reference_message", new=fake_fetch):
        parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎ unknown：被回复内容" in text, text
    assert "当前消息仍转发" in text, text
    assert has_content
    print("[OK] 引用作者结构损坏 → unknown 降级，当前消息正常转发")


async def test_malformed_reference_mentions():
    # 提及字段类型损坏时忽略映射，引用和当前正文仍正常转发。
    ref = type("R", (), {"message_id": 999, "channel_id": 111})()
    event = make_event("111", "当前消息仍转发", ref=ref, author_name="eve")
    msg = fetch.normalize_event(event, "频道A")

    async def fake_fetch(channel_id, message_id):
        return {
            "author": {"global_name": "Bob"},
            "content": "被回复内容",
            "mentions": 7,
        }

    with mock.patch.object(fetch, "_fetch_reference_message", new=fake_fetch):
        parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎ Bob：被回复内容" in text, text
    assert "当前消息仍转发" in text, text
    assert has_content
    print("[OK] 引用提及字段结构损坏 → 忽略映射，当前消息正常转发")


async def test_malformed_reference_content():
    # 非字符串引用正文被跳过，不影响当前消息。
    ref = type("R", (), {"message_id": 999, "channel_id": 111})()
    event = make_event("111", "当前消息仍转发", ref=ref, author_name="eve")
    msg = fetch.normalize_event(event, "频道A")

    async def fake_fetch(channel_id, message_id):
        return {
            "author": {"global_name": "Bob"},
            "content": 12345,
            "mentions": [],
        }

    with mock.patch.object(fetch, "_fetch_reference_message", new=fake_fetch):
        parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎" not in text, text
    assert "当前消息仍转发" in text, text
    assert has_content
    print("[OK] 引用正文类型损坏 → 跳过引用行，当前消息正常转发")


async def test_malformed_reference_author_name():
    # 坏 global_name 回退到有效 username；两个昵称都坏时显示 unknown。
    raw = {
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice"},
        "content": "当前消息正文",
        "embeds": [],
        "attachments": [],
        "message_reference": {"message_id": "999", "channel_id": "111"},
        "referenced_message": {
            "id": "999",
            "author": {"global_name": 123, "username": "Bob"},
            "content": "内联引用正文",
            "mentions": [],
        },
    }
    msg = fetch.normalize_api_message(raw)
    msg["channel_name"] = "频道A"
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎ Bob：内联引用正文" in text, text
    assert has_content

    event = make_event(
        "111",
        "当前消息仍转发",
        ref=type("R", (), {"message_id": 999, "channel_id": 111})(),
    )
    msg = fetch.normalize_event(event, "频道A")

    async def fake_fetch(channel_id, message_id):
        return {
            "author": {"global_name": 7, "username": []},
            "content": "回退引用正文",
            "mentions": [],
        }

    with mock.patch.object(fetch, "_fetch_reference_message", new=fake_fetch):
        parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    assert "↩︎ unknown：回退引用正文" in text, text
    assert "当前消息仍转发" in text, text
    assert has_content
    print("[OK] 引用作者昵称类型损坏 → 选有效别名或 unknown")


async def test_non_reply_unchanged():
    # 非回复消息：reference 为 None，输出与改动前完全一致
    raw = {
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice"},
        "content": "普通消息",
        "embeds": [],
        "attachments": [],
    }
    assert fetch.normalize_api_message(raw)["reference"] is None

    msg = {
        "username": "alice",
        "channel_id": "111",
        "channel_name": "频道A",
        "content": "普通消息",
        "embeds": [],
        "attachments": [],
    }
    parts, _media, has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    expected = (
        "自动转发来自\n"
        "Discord [#频道A] 中 [alice]的消息：\n"
        "普通消息"
    )
    assert text == expected, repr(text)
    assert "↩︎" not in text
    assert has_content
    print("[OK] 非回复消息输出不变 →\n" + text)


async def test_long_content_truncated():
    # 长正文截断到 REFERENCE_TEXT_LIMIT + "…"
    raw = {
        "id": "1001",
        "channel_id": "111",
        "author": {"username": "alice"},
        "content": "当前消息",
        "embeds": [],
        "attachments": [],
        "message_reference": {"message_id": "999", "channel_id": "111"},
        "referenced_message": {
            "id": "999",
            "channel_id": "111",
            "author": {"username": "bob"},
            "content": "很长的被回复内容" * 20,
            "embeds": [],
            "attachments": [],
        },
    }
    msg = fetch.normalize_api_message(raw)
    msg["channel_name"] = "频道A"
    parts, _media, _has_content = await fetch.build_parts(msg)
    text = text_parts_str(parts)
    line = next(l for l in text.splitlines() if l.startswith("↩︎ "))
    prefix = "↩︎ bob："
    assert len(line) == len(prefix) + fetch.REFERENCE_TEXT_LIMIT + 1, repr(line)
    assert line.endswith("…"), repr(line)
    print(f"[OK] 长正文截断 → {line}")


async def main():
    await test_inline_reference()
    print()
    await test_inline_reference_malformed_fields()
    print()
    await test_malformed_message_author()
    print()
    await test_malformed_author_username()
    print()
    await test_malformed_message_content()
    print()
    await test_fetch_message_rejects_invalid_response()
    print()
    await test_malformed_message_collections()
    print()
    await test_malformed_media_fields()
    print()
    await test_event_reference_fallback_success()
    print()
    await test_reference_fetch_failure()
    print()
    await test_malformed_reference_author()
    print()
    await test_malformed_reference_mentions()
    print()
    await test_malformed_reference_content()
    print()
    await test_malformed_reference_author_name()
    print()
    await test_non_reply_unchanged()
    print()
    await test_long_content_truncated()
    print("\n全部通过")


if __name__ == "__main__":
    asyncio.run(main())
