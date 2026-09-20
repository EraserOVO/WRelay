"""OpenAI 兼容的公告翻译文本生成客户端。"""
import os
import time

import httpx
from nonebot import get_driver, logger

from plugins.config import get_ai_config


class AIClientError(RuntimeError):
    """AI 服务未配置、请求失败或响应格式无效。"""


_ai_client = None
_MODEL_CACHE_TTL = 300
_MODEL_RECHECK_TTL = 300
_model_cache = {}
_model_overrides = {}
_model_recheck_at = {}
_PREFERRED_MODELS = (
    "DeepSeek-V4-Flash",
    "DeepSeek-V4.1-Flash",
    "DeepSeek-V4-Flash-Vision-Exp",
    "Qwen3.8-Flash-Next",
    "Qwen3.8-27B",
    "MiniCPM5-2B",
)


def _get_ai_client():
    global _ai_client
    if _ai_client is None:
        _ai_client = httpx.AsyncClient()
    return _ai_client


@get_driver().on_shutdown
async def _close_ai_client():
    global _ai_client
    if _ai_client is not None:
        await _ai_client.aclose()
        _ai_client = None


def _endpoint(base_url):
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise AIClientError("AI API 未配置 base_url")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _models_endpoint(base_url):
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise AIClientError("AI API 未配置 base_url")
    suffix = "/chat/completions"
    if base.endswith(suffix):
        base = base[:-len(suffix)].rstrip("/")
    return base + "/models"


def _model_override_key(config):
    return (
        str(config.get("_profile_id") or ""),
        str(config.get("base_url") or "").strip().rstrip("/"),
        str(config.get("model") or "").strip(),
    )


def _should_switch_model(error):
    if not isinstance(error, httpx.HTTPStatusError):
        return False
    try:
        detail = error.response.text.lower()
    except Exception:
        return False
    if error.response.status_code == 400:
        return "model" in detail and (
            "unsupported" in detail or "not supported" in detail or "not found" in detail
        )
    if error.response.status_code == 429:
        return "model" in detail and "concurr" in detail and (
            "limit" in detail or "rate_limit" in detail
        )
    return False


async def _fetch_models(base_url, api_key, timeout, force=False):
    cache_key = str(base_url or "").strip().rstrip("/")
    now = time.monotonic()
    cached = _model_cache.get(cache_key)
    if not force and cached and cached[0] > now:
        return cached[1]
    response = await _get_ai_client().get(
        _models_endpoint(base_url),
        headers={"Authorization": "Bearer " + api_key},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    entries = data.get("data") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    models = []
    for item in entries:
        if isinstance(item, dict):
            model_id = str(item.get("id") or "").strip()
        else:
            model_id = str(item or "").strip()
        if model_id and model_id not in models:
            models.append(model_id)
    _model_cache[cache_key] = (now + _MODEL_CACHE_TTL, models)
    return models


def _choose_fallback_model(models, failed_model):
    available = [str(model).strip() for model in models if str(model).strip()]
    available = [model for model in available if model != failed_model]
    for preferred in _PREFERRED_MODELS:
        if preferred in available:
            return preferred
    for model in available:
        lowered = model.lower()
        if "ocr" not in lowered and "mineru" not in lowered and "embedding" not in lowered:
            return model
    return ""


async def _model_for_request(config, api_key, timeout, configured_model, override_key):
    override_model = _model_overrides.get(override_key)
    if not override_model:
        return configured_model
    now = time.monotonic()
    if _model_recheck_at.get(override_key, 0) > now:
        return override_model
    _model_recheck_at[override_key] = now + _MODEL_RECHECK_TTL
    try:
        models = await _fetch_models(
            config.get("base_url"), api_key, timeout, force=True
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return override_model
    if configured_model not in models:
        return override_model
    return configured_model


async def _request_completion(base_url, api_key, model, messages, max_tokens, temperature, timeout):
    response = await _get_ai_client().post(
        _endpoint(base_url),
        headers={"Authorization": "Bearer " + api_key},
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


async def generate_text(messages, max_tokens=None, temperature=0.7):
    """调用已配置的 OpenAI 兼容接口，返回供翻译路由使用的纯文本。"""
    config = get_ai_config()
    api_key = os.getenv(str(config.get("api_key_env") or "WRELAY_AI_API_KEY"), "").strip()
    if not api_key:
        raise AIClientError("AI API 密钥未配置")
    configured_model = str(config.get("model") or "").strip()
    if not configured_model:
        raise AIClientError("AI 模型未配置")

    timeout = int(config.get("timeout_seconds") or 25)
    output_limit = int(max_tokens or config.get("max_output_tokens") or 800)
    override_key = _model_override_key(config)
    model = await _model_for_request(
        config, api_key, timeout, configured_model, override_key
    )
    try:
        content = await _request_completion(
            config.get("base_url"), api_key, model, messages,
            output_limit, temperature, timeout,
        )
        if model == configured_model and _model_overrides.pop(override_key, None):
            _model_recheck_at.pop(override_key, None)
            logger.info("AI API 原选模型已恢复：{}", configured_model)
    except httpx.HTTPStatusError as exc:
        if not _should_switch_model(exc):
            raise AIClientError("AI API 调用失败: %s" % exc) from exc
        try:
            models = await _fetch_models(
                config.get("base_url"), api_key, timeout, force=True
            )
            fallback_model = _choose_fallback_model(models, model)
            if not fallback_model:
                raise AIClientError("AI API 当前模型不可用，且没有可切换模型")
            _model_overrides[override_key] = fallback_model
            _model_recheck_at[override_key] = time.monotonic() + _MODEL_RECHECK_TTL
            logger.warning("AI API 模型不可用或繁忙，已自动切换：{} -> {}", model, fallback_model)
            content = await _request_completion(
                config.get("base_url"), api_key, fallback_model, messages,
                output_limit, temperature, timeout,
            )
        except AIClientError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as fallback_exc:
            raise AIClientError("AI API 自动切换失败: %s" % fallback_exc) from fallback_exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIClientError("AI API 调用失败: %s" % exc) from exc

    text = str(content or "").strip()
    if not text:
        raise AIClientError("AI API 未返回文本")
    return text
