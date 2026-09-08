"""验证 Discord 自动转发共用的渲染/翻译准备流程。"""
import asyncio
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import forward_service


async def main():
    build_calls = []
    translate_calls = []

    async def fake_build(message, source_label, enabled_types):
        build_calls.append((message, source_label, enabled_types))
        return ["header", "body"], ["media"], True

    async def fake_translate(parts, cache_key, skip_leading_parts):
        translate_calls.append((parts, cache_key, skip_leading_parts))
        return ["translated"]

    with mock.patch.object(forward_service, "build_parts", side_effect=fake_build), \
            mock.patch.object(forward_service, "translate_text_parts", side_effect=fake_translate):
        result = await forward_service.prepare_discord_message(
            {"content": "Hello"},
            "C1",
            "M1",
            source_label="自动补发来自",
            filter_config={"filter_message_types": ["文字"]},
        )

    assert result == (["translated"], ["media"]), result
    assert build_calls[0][1] == "自动补发来自"
    assert translate_calls == [(
        ["header", "body"],
        ("discord", "C1", "M1"),
        1,
    )]
    print("PASS - Discord 实时/补发/重试共用消息准备流程")


if __name__ == "__main__":
    asyncio.run(main())
