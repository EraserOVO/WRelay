"""验证统一翻译文本段入口的策略、缓存和失败降级。"""
import asyncio
import unittest.mock as mock

import nonebot

nonebot.init()

from plugins import translation
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

    print("PASS - 独立 X 翻译配置、统一文本段策略、缓存和关闭降级")


if __name__ == "__main__":
    asyncio.run(main())
