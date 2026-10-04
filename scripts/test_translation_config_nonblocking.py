"""验证异步翻译链路中的服务配置读取不会阻塞事件循环。"""
import asyncio
import os
import time
import threading
from unittest.mock import patch

import nonebot

nonebot.init()

from plugins import ai_client, tencent_tmt, translation
from plugins.media import make_text


def _slow_getter(value, finished):
    time.sleep(0.15)
    finished.set()
    return value


async def _assert_nonblocking(operation, finished, label):
    task = asyncio.create_task(operation())
    await asyncio.sleep(0.02)
    assert not finished.is_set(), "{} 的配置读取阻塞了事件循环".format(label)
    await task


async def main():
    ai_finished = threading.Event()
    ai_config = {"api_key_env": "WRELAY_TRANSLATION_BLOCK_TEST_KEY"}

    async def check_ai_client():
        try:
            await ai_client.generate_text([{"role": "user", "content": "hello"}])
        except ai_client.AIClientError as exc:
            assert "密钥未配置" in str(exc)
        else:
            raise AssertionError("未配置密钥时 AI 请求应失败")

    with patch.object(
        ai_client,
        "get_ai_config",
        side_effect=lambda: _slow_getter(ai_config, ai_finished),
    ), patch.dict(os.environ, {"WRELAY_TRANSLATION_BLOCK_TEST_KEY": ""}):
        await _assert_nonblocking(check_ai_client, ai_finished, "AI 客户端")

    tmt_finished = threading.Event()
    tmt_config = {
        "secret_id_env": "WRELAY_TRANSLATION_BLOCK_TEST_SECRET_ID",
        "secret_key_env": "WRELAY_TRANSLATION_BLOCK_TEST_SECRET_KEY",
    }

    async def check_tmt_client():
        try:
            await tencent_tmt.translate_text("hello")
        except tencent_tmt.TencentTMTError as exc:
            assert "密钥未配置" in str(exc)
        else:
            raise AssertionError("未配置密钥时 TMT 请求应失败")

    with patch.object(
        tencent_tmt,
        "get_tencent_tmt_config",
        side_effect=lambda: _slow_getter(tmt_config, tmt_finished),
    ), patch.dict(os.environ, {
        "WRELAY_TRANSLATION_BLOCK_TEST_SECRET_ID": "",
        "WRELAY_TRANSLATION_BLOCK_TEST_SECRET_KEY": "",
    }):
        await _assert_nonblocking(check_tmt_client, tmt_finished, "腾讯 TMT")

    identity_calls = 0
    identity_finished = threading.Event()
    profile_config = {
        "_profile_id": "profile-a",
        "_profile_name": "档案 A",
        "base_url": "https://example.test/v1",
        "model": "model-a",
    }

    def slow_profile_config():
        nonlocal identity_calls
        time.sleep(0.15)
        identity_calls += 1
        if identity_calls == 2:
            identity_finished.set()
        return dict(profile_config)

    async def fake_ai(_text, _target):
        return "译文"

    translation._cache.clear()
    with patch.object(
        translation,
        "get_ai_config",
        side_effect=slow_profile_config,
    ), patch.object(translation, "_translate_by_ai", side_effect=fake_ai):
        await _assert_nonblocking(
            lambda: translation.translate_for_relay(
                "Hello translation", ("test", "slow-profile", "1"),
                config={
                    "enabled": True,
                    "provider": "ai",
                    "fallback_provider": "",
                    "target_language": "zh",
                    "only_non_chinese": True,
                },
            ),
            identity_finished,
            "翻译缓存身份与 AI 状态",
        )
    assert identity_calls == 2, identity_calls

    route_finished = threading.Event()
    with patch.object(
        translation,
        "get_translation_config",
        side_effect=lambda: _slow_getter({"enabled": False}, route_finished),
    ):
        await _assert_nonblocking(
            lambda: translation.translate_for_relay(
                "Hello", ("test", "default-route", "1")
            ),
            route_finished,
            "默认翻译路由",
        )

    append_finished = threading.Event()

    async def fake_translate(*_args, **_kwargs):
        return "译文"

    with patch.object(
        translation,
        "get_translation_config",
        side_effect=lambda: _slow_getter({"append_original": True}, append_finished),
    ), patch.object(translation, "translate_for_relay", side_effect=fake_translate):
        await _assert_nonblocking(
            lambda: translation.translate_text_parts(
                [make_text("Hello")], ("test", "append-policy", "1")
            ),
            append_finished,
            "译文追加策略",
        )

    print("PASS - AI、TMT 与统一翻译路由的慢配置读取不阻塞事件循环")


if __name__ == "__main__":
    asyncio.run(main())
