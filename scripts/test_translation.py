"""验证统一翻译文本段入口的策略、缓存和失败降级。"""
import asyncio
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import config as config_module, translation
from plugins.media import make_text
from plugins.settings_model import SettingsModel, validate_settings


def _text(parts):
    return "".join((getattr(p, "data", None) or {}).get("text", "") for p in parts)


async def main():
    config = {
        "enabled": True,
        "provider": "ai",
        "fallback_provider": "",
        "target_language": "zh",
        "append_original": True,
        "only_non_chinese": True,
    }
    calls = []

    async def fake_ai(text, target):
        calls.append((text, target))
        return "你好，世界"

    translation._cache.clear()
    with (
        mock.patch.object(translation, "get_translation_config", return_value=config),
        mock.patch.object(translation, "_translate_by_ai", side_effect=fake_ai),
    ):
        original = [make_text("自动转发头"), make_text("Hello, world")]
        translated = await translation.translate_text_parts(
            original,
            ("discord", "C1", "M1"),
            skip_leading_parts=1,
        )
        assert _text(translated) == "自动转发头Hello, world\n--------------------\n你好，世界"

        again = await translation.translate_text_parts(
            original,
            ("discord", "C1", "M1"),
            skip_leading_parts=1,
        )
        assert _text(again) == _text(translated)
        assert calls == [("Hello, world", "zh")]

        config["append_original"] = False
        only_translation = await translation.translate_text_parts(
            [make_text("Another message")],
            ("x_rss", "source", "P1"),
        )
        assert _text(only_translation) == "你好，世界"

        header_only_translation = await translation.translate_text_parts(
            [make_text("自动转发头"), make_text("Hello again")],
            ("discord", "C1", "M2"),
            skip_leading_parts=1,
        )
        assert _text(header_only_translation) == "自动转发头\n你好，世界"

        config["enabled"] = False
        untouched = [make_text("No translation")]
        assert await translation.translate_text_parts(untouched, ("qq_group", "Q1")) is untouched

        x_config = dict(config, enabled=True, append_original=False)
        x_translated = await translation.translate_text_parts(
            [make_text("X only")],
            ("x_rss", "source", "P2"),
            config=x_config,
        )
        assert _text(x_translated) == "你好，世界"

    translation._cache.clear()
    concurrent_config = dict(config, enabled=True, append_original=False)
    translation_started = asyncio.Event()
    release_translation = asyncio.Event()
    concurrent_calls = []

    async def slow_ai(text, target):
        concurrent_calls.append((text, target))
        translation_started.set()
        await release_translation.wait()
        return "并发译文"

    first_translation = second_translation = None
    try:
        with (
            mock.patch.object(
                translation,
                "_provider_cache_identity",
                return_value=("ai", "profile", "https://ai.example", "model"),
            ),
            mock.patch.object(translation, "_translate_by_ai", side_effect=slow_ai),
        ):
            first_translation = asyncio.create_task(
                translation.translate_for_relay(
                    "Concurrent message",
                    ("discord", "C1", "M-concurrent"),
                    config=concurrent_config,
                )
            )
            await asyncio.wait_for(translation_started.wait(), timeout=1)
            second_translation = asyncio.create_task(
                translation.translate_for_relay(
                    "Concurrent message",
                    ("discord", "C1", "M-concurrent"),
                    config=concurrent_config,
                )
            )
            await asyncio.sleep(0)
            assert len(concurrent_calls) == 1, "同一消息并发触发了重复翻译请求"
            assert not second_translation.done()

            release_translation.set()
            results = await asyncio.gather(first_translation, second_translation)
            assert results == ["并发译文", "并发译文"]
            assert len(concurrent_calls) == 1
            assert translation._translation_inflight == {}
    finally:
        release_translation.set()
        for task in (first_translation, second_translation):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    translation._cache.clear()
    active_profile = {
        "_profile_id": "profile-a",
        "base_url": "https://a.example.test/v1",
        "model": "model-a",
    }
    profile_calls = []

    async def translate_profile(_text, _target):
        profile_calls.append(active_profile["_profile_id"])
        return "译文-" + active_profile["_profile_id"]

    with (
        mock.patch.object(translation, "get_translation_config", return_value=dict(
            config, enabled=True, append_original=False
        )),
        mock.patch.object(translation, "get_ai_config", side_effect=lambda: dict(active_profile)),
        mock.patch.object(translation, "_translate_by_ai", side_effect=translate_profile),
    ):
        first = await translation.translate_for_relay(
            "Profile cache", ("discord", "C1", "PROFILE-CHANGE")
        )
        active_profile.update({
            "_profile_id": "profile-b",
            "base_url": "https://b.example.test/v1",
            "model": "model-b",
        })
        second = await translation.translate_for_relay(
            "Profile cache", ("discord", "C1", "PROFILE-CHANGE")
        )
    assert first == "译文-profile-a"
    assert second == "译文-profile-b"
    assert profile_calls == ["profile-a", "profile-b"]

    translation._cache.clear()
    same_id_calls = []

    async def translate_same_id(text, _target):
        same_id_calls.append(text)
        return "译文-" + text

    same_id_config = dict(config, enabled=True, append_original=False)
    with (
        mock.patch.object(
            translation,
            "_provider_cache_identity",
            return_value=("ai", "profile", "https://ai.example", "model"),
        ),
        mock.patch.object(translation, "_translate_by_ai", side_effect=translate_same_id),
    ):
        first_text = await translation.translate_for_relay(
            "First edited post", ("bilibili", "source", "same-id"),
            config=same_id_config,
        )
        changed_text = await translation.translate_for_relay(
            "Updated edited post", ("bilibili", "source", "same-id"),
            config=same_id_config,
        )
        repeated_text = await translation.translate_for_relay(
            "Updated edited post", ("bilibili", "source", "same-id"),
            config=same_id_config,
        )
    assert first_text == "译文-First edited post"
    assert changed_text == "译文-Updated edited post"
    assert repeated_text == changed_text
    assert same_id_calls == ["First edited post", "Updated edited post"]

    settings = SettingsModel.default().to_dict()
    assert settings["x_translation"]["enabled"] is False
    assert "enabled" not in settings["ai"]
    assert "enabled" not in settings["tencent_tmt"]
    assert validate_settings(settings) is None
    legacy_settings = SettingsModel.from_data({
        **settings,
        "ai": {**settings["ai"], "enabled": False},
        "tencent_tmt": {**settings["tencent_tmt"], "enabled": False},
    }).to_dict()
    assert "enabled" not in legacy_settings["ai"]
    assert "enabled" not in legacy_settings["tencent_tmt"]
    assert validate_settings(legacy_settings) is None
    settings["x_translation"] = {"enabled": "yes"}
    assert validate_settings(settings) == "x_translation.enabled 必须是布尔值"

    shared_route = {
        "enabled": True,
        "provider": "ai",
        "fallback_provider": "tencent_tmt",
        "target_language": "zh",
        "append_original": False,
        "only_non_chinese": True,
    }
    divergent_legacy_route = {
        "enabled": True,
        "provider": "tencent_tmt",
        "fallback_provider": "ai",
        "target_language": "en",
        "append_original": True,
        "only_non_chinese": False,
    }
    route_settings = {
        "translation": shared_route,
        "x_translation": divergent_legacy_route,
        "x_rss_sources": [{"source": "IGN", "translation_enabled": True}],
    }
    with mock.patch.object(config_module, "_load_settings", return_value=route_settings):
        assert config_module.get_x_translation_config()["provider"] == "ai"
        x_route = config_module.get_x_source_translation_config("IGN")
        assert x_route["provider"] == "ai"
        assert x_route["fallback_provider"] == "tencent_tmt"

    # 手动转发只看共享 translation.enabled，自动转发仍看来源自己的开关；
    # 两种模式都保留来源的「保留原文」和「显示时间」。
    manual_settings = {
        "translation": dict(shared_route, enabled=True, append_original=False),
        "discord_channels": [{
            "id": "C-manual",
            "translation_enabled": False,
            "translation_append_original": False,
            "translation_show_datetime": True,
        }],
        "x_rss_sources": [{
            "source": "IGN",
            "translation_enabled": False,
            "translation_append_original": False,
            "translation_show_datetime": True,
        }],
        "bilibili_sources": [{
            "id": "up:1",
            "kind": "up",
            "value": "1",
            "translation_enabled": True,
        }],
    }
    with mock.patch.object(config_module, "_load_settings", return_value=manual_settings):
        auto_channel = config_module.get_channel_translation_config("C-manual")
        assert auto_channel["enabled"] is False
        manual_channel = config_module.get_channel_translation_config(
            "C-manual", manual=True
        )
        assert manual_channel["enabled"] is True
        assert manual_channel["append_original"] is False
        assert manual_channel["show_datetime"] is True

        auto_x = config_module.get_x_source_translation_config("IGN")
        assert auto_x["enabled"] is False
        manual_x = config_module.get_x_source_translation_config("IGN", manual=True)
        assert manual_x["enabled"] is True
        assert manual_x["append_original"] is False
        assert manual_x["show_datetime"] is True

        auto_bilibili = config_module.get_bilibili_source_translation_config("up:1")
        assert auto_bilibili["enabled"] is True
        manual_settings["translation"]["enabled"] = False
        manual_bilibili = config_module.get_bilibili_source_translation_config(
            "up:1", manual=True
        )
        assert manual_bilibili["enabled"] is False
        assert manual_bilibili["append_original"] is True
        assert manual_bilibili["show_datetime"] is False

        unknown_manual = config_module.get_channel_translation_config(
            "C-unknown", manual=True
        )
        assert unknown_manual["enabled"] is False
        manual_settings["translation"]["enabled"] = True
        unknown_manual = config_module.get_x_source_translation_config(
            "UNKNOWN", manual=True
        )
        assert unknown_manual["enabled"] is True

    migrated = SettingsModel.from_data({
        **SettingsModel.default().to_dict(),
        "translation": shared_route,
        "x_translation": divergent_legacy_route,
    }).to_dict()
    assert migrated["x_translation"]["provider"] == "ai"
    assert migrated["x_translation"]["fallback_provider"] == "tencent_tmt"

    print("PASS - Discord/X 共用翻译路由、统一文本段策略、缓存和关闭降级")


if __name__ == "__main__":
    asyncio.run(main())
