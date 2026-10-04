"""OpenAI 兼容的公告翻译文本生成客户端。"""
import asyncio
import os
import time
from collections import OrderedDict

import httpx
from nonebot import get_driver, logger

from plugins.config import get_ai_config


class AIClientError(RuntimeError):
    """AI 服务未配置、请求失败或响应格式无效。"""


def get_last_used_model():
    """返回最近一次成功完成请求的实际模型，不包含任何凭据。"""
    return _last_used_model


_ai_client = None
_ai_request_tasks = set()
_ai_stopping = False
_MODEL_CACHE_TTL = 300
_MODEL_RECHECK_TTL = 300
_MODEL_CACHE_LIMIT = 128
_MODEL_STATE_LIMIT = 64
_model_cache = OrderedDict()
_model_overrides = OrderedDict()
_model_recheck_at = OrderedDict()
_last_used_model = None
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
    global _ai_client, _ai_stopping
    _ai_stopping = True
    request_tasks = list(_ai_request_tasks)
    for task in request_tasks:
        if not task.done():
            task.cancel()
    if request_tasks:
        await asyncio.gather(*request_tasks, return_exceptions=True)
    _ai_request_tasks.clear()
    if _ai_client is not None:
        await _ai_client.aclose()
        _ai_client = None


async def _request_ai(method, *args, **kwargs):
    """Track one outbound request so shutdown can reap it before client close."""
    if _ai_stopping:
        raise AIClientError("AI API 客户端正在关闭")
    task = asyncio.current_task()
    if task is not None:
        _ai_request_tasks.add(task)
    try:
        return await getattr(_get_ai_client(), method)(*args, **kwargs)
    finally:
        if task is not None:
            _ai_request_tasks.discard(task)


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


def _remember_model_cache(cache_key, expires_at, models):
    _model_cache.pop(cache_key, None)
    _model_cache[cache_key] = (expires_at, models)
    while len(_model_cache) > _MODEL_CACHE_LIMIT:
        _model_cache.popitem(last=False)


def _remember_model_override(override_key, model, recheck_at):
    _model_overrides.pop(override_key, None)
    _model_recheck_at.pop(override_key, None)
    _model_overrides[override_key] = model
    _model_recheck_at[override_key] = recheck_at
    while len(_model_overrides) > _MODEL_STATE_LIMIT:
        oldest_key, _ = _model_overrides.popitem(last=False)
        _model_recheck_at.pop(oldest_key, None)


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
    if error.response.status_code in (500, 503):
        # 500/503 只有在响应明确指向模型容量或服务节点不可用时才轮换；
        # 普通服务端异常不额外重试，避免放大提供商整体故障。
        return any(
            marker in detail
            for marker in (
                "no_available_workers",
                "no available workers",
                "service unavailable",
                "temporarily unavailable",
                "model unavailable",
                "model is unavailable",
                "model overloaded",
                "overloaded",
            )
        )
    return False


async def _fetch_models(base_url, api_key, timeout, force=False):
    cache_key = str(base_url or "").strip().rstrip("/")
    now = time.monotonic()
    cached = _model_cache.get(cache_key)
    if not force and cached and cached[0] > now:
        _model_cache.move_to_end(cache_key)
        return cached[1]
    if cached:
        _model_cache.pop(cache_key, None)
    response = await _request_ai(
        "get",
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
    _remember_model_cache(cache_key, now + _MODEL_CACHE_TTL, models)
    return models


def _choose_fallback_models(models, failed_models):
    failed_models = {
        str(model).strip() for model in (failed_models or ()) if str(model).strip()
    }
    available = []
    seen = set()
    for model in models:
        model = str(model).strip()
        if not model or model in failed_models or model in seen:
            continue
        lowered = model.lower()
        if "ocr" in lowered or "mineru" in lowered or "embedding" in lowered:
            continue
        seen.add(model)
        available.append(model)

    ordered = []
    for preferred in _PREFERRED_MODELS:
        if preferred in available:
            ordered.append(preferred)
    ordered.extend(model for model in available if model not in ordered)
    return ordered


def _choose_fallback_model(models, failed_model):
    """兼容旧调用方，返回排序后的第一个备用模型。"""
    candidates = _choose_fallback_models(models, {failed_model})
    return candidates[0] if candidates else ""


async def _model_for_request(config, api_key, timeout, configured_model, override_key):
    override_model = _model_overrides.get(override_key)
    if not override_model:
        return configured_model
    _model_overrides.move_to_end(override_key)
    if override_key in _model_recheck_at:
        _model_recheck_at.move_to_end(override_key)
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
    response = await _request_ai(
        "post",
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
    global _last_used_model
    config = await asyncio.to_thread(get_ai_config)
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
        except AIClientError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as models_exc:
            raise AIClientError("AI API 查询可用模型失败: %s" % models_exc) from models_exc
        fallback_models = _choose_fallback_models(models, {model})
        if not fallback_models:
            raise AIClientError("AI API 当前模型不可用，且没有可切换模型")
        last_model_error = exc
        for index, fallback_model in enumerate(fallback_models, start=1):
            _remember_model_override(
                override_key,
                fallback_model,
                time.monotonic() + _MODEL_RECHECK_TTL,
            )
            logger.warning(
                "AI API 模型不可用或繁忙，已自动切换：{} -> {}（候选 {}/{}）",
                model,
                fallback_model,
                index,
                len(fallback_models),
            )
            try:
                content = await _request_completion(
                    config.get("base_url"), api_key, fallback_model, messages,
                    output_limit, temperature, timeout,
                )
            except httpx.HTTPStatusError as fallback_exc:
                if _should_switch_model(fallback_exc):
                    last_model_error = fallback_exc
                    logger.warning(
                        "AI API 备用模型仍不可用，继续尝试下一个模型：{}",
                        fallback_model,
                    )
                    continue
                raise AIClientError("AI API 自动切换失败: %s" % fallback_exc) from fallback_exc
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as fallback_exc:
                raise AIClientError("AI API 自动切换失败: %s" % fallback_exc) from fallback_exc
            model = fallback_model
            break
        else:
            _model_overrides.pop(override_key, None)
            _model_recheck_at.pop(override_key, None)
            raise AIClientError("AI API 当前模型不可用，已尝试所有可用模型") from last_model_error
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIClientError("AI API 调用失败: %s" % exc) from exc

    if not isinstance(content, str):
        raise AIClientError("AI API 返回的文本格式无效")
    text = content.strip()
    if not text:
        raise AIClientError("AI API 未返回文本")
    _last_used_model = model
    return text
