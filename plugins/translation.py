"""WRelay 转发消息的可选翻译路由。"""
import re
import time

from nonebot import logger

from plugins.ai_client import AIClientError, generate_text, get_last_used_model
from plugins.config import get_ai_config, get_translation_config
from plugins.media import make_text
from plugins.tencent_tmt import TencentTMTError, translate_text

_CHINESE = re.compile(r"[\u4e00-\u9fff]")
_FOREIGN = re.compile(r"[A-Za-z\u3040-\u30ff\uac00-\ud7af]")
_cache = {}
_CACHE_LIMIT = 512
_last_successful_provider = None
_last_successful_at = None
_last_successful_profile = None
_last_successful_model = None


def get_translation_status():
    """返回最近一次成功工作的翻译服务，不包含任何凭据。"""
    return {
        "provider": _last_successful_provider,
        "at": _last_successful_at,
        "profile": _last_successful_profile,
        "model": _last_successful_model,
    }


def _should_translate(text, config):
    if not text.strip():
        return False
    if not config.get("only_non_chinese", True):
        return True
    return len(_FOREIGN.findall(text)) > len(_CHINESE.findall(text))


async def _translate_by_ai(text, target):
    return await generate_text([
        {
            "role": "system",
            "content": "你是消息翻译器。只输出译文；保留 Markdown、链接、代码、变量、角色名和数字，不添加解释。",
        },
        {"role": "user", "content": "将以下内容翻译为%s：\n\n%s" % (target, text)},
    ], temperature=0.1)


async def translate_for_relay(text, cache_key, config=None):
    """返回译文或 None。所有失败都由这里吞掉并记录脱敏日志。"""
    config = config if isinstance(config, dict) else get_translation_config()
    if not config.get("enabled") or not _should_translate(text, config):
        return None
    if cache_key is not None:
        # 同一原消息在切换主服务、后备服务、目标语言后必须重新翻译，
        # 不能复用切换前的进程内缓存。
        cache_key = (
            cache_key,
            config.get("provider"),
            config.get("fallback_provider"),
            config.get("target_language"),
            config.get("only_non_chinese"),
        )
    if cache_key is not None and cache_key in _cache:
        return _cache[cache_key]

    providers = [config.get("provider"), config.get("fallback_provider")]
    providers = [name for index, name in enumerate(providers) if name and name not in providers[:index]]
    target = str(config.get("target_language") or "zh")
    global _last_successful_provider, _last_successful_at
    global _last_successful_profile, _last_successful_model
    for provider in providers:
        try:
            if provider == "tencent_tmt":
                translated = await translate_text(text, target=target)
            elif provider == "ai":
                translated = await _translate_by_ai(text, target)
            else:
                continue
            if translated:
                _last_successful_provider = provider
                _last_successful_at = int(time.time())
                if provider == "ai":
                    ai_config = get_ai_config()
                    _last_successful_profile = ai_config.get("_profile_name") or None
                    _last_successful_model = get_last_used_model() or ai_config.get("model") or None
                else:
                    _last_successful_profile = None
                    _last_successful_model = None
            if cache_key is not None:
                _cache[cache_key] = translated
                if len(_cache) > _CACHE_LIMIT:
                    _cache.pop(next(iter(_cache)))
            return translated
        except (TencentTMTError, AIClientError) as exc:
            logger.warning("消息翻译 {} 失败，尝试下一路由: {}", provider, exc)
    return None


def _text_from_parts(text_parts, skip_leading_parts=0):
    """提取 QQ 文本段的可翻译正文；媒体段不参与翻译。"""
    return "".join(
        (getattr(part, "data", None) or {}).get("text", "")
        for part in text_parts[skip_leading_parts:]
    )


async def translate_text_parts(
    text_parts,
    cache_key,
    skip_leading_parts=0,
    config=None,
):
    """按当前配置翻译 QQ 文本段，返回应发送的文本段。

    所有消息链路在完成自身渲染后调用此函数，因此统一保留媒体、
    原文追加策略、失败降级和缓存语义。对于带来源头的链路，调用方可
    跳过开头的来源文本，避免中文头部干扰外文正文识别。未翻译时原样
    返回传入列表。
    """
    translated = await translate_for_relay(
        _text_from_parts(text_parts, skip_leading_parts),
        cache_key,
        config=config,
    )
    if not translated:
        return text_parts

    config = config if isinstance(config, dict) else get_translation_config()
    if config.get("append_original", True):
        return [
            *text_parts,
            make_text("\n--------------------\n"),
            make_text(translated),
        ]

    # “只保留译文”只替换可翻译正文；Discord/X 的来源头部仍保留，
    # 否则接收者无法判断消息来自哪个频道或账号。QQ 手动纯文本没有
    # 来源头（skip_leading_parts=0），保持仅发送译文的原有行为。
    prefix = text_parts[:skip_leading_parts]
    body_media = [
        part for part in text_parts[skip_leading_parts:]
        if getattr(part, "type", "") != "text"
    ]
    if prefix:
        return [*prefix, make_text("\n" + translated), *body_media]
    return [make_text(translated), *body_media]
