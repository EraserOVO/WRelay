"""公告翻译文本生成客户端，支持 OpenAI 兼容服务和本机 Ollama。"""
import asyncio
import os
import time
from collections import OrderedDict

import httpx
from nonebot import get_driver, logger
from plugins.network_proxy import proxy_for_url

from plugins.config import get_ai_configs
from plugins.ai_protocol import (
    clean_ollama_content,
    ollama_native_chat_endpoint,
)


class AIClientError(RuntimeError):
    """AI 服务未配置、请求失败或响应格式无效。"""


def get_last_used_model():
    """返回最近一次成功完成请求的实际模型，不包含任何凭据。"""
    return _last_used_model


def get_last_used_profile():
    """返回最近一次成功完成请求的 AI 配置名称。"""
    return _last_used_profile


_ai_client = None
_ai_proxy_clients = {}
_ai_request_tasks = set()
_ai_stopping = False
_MODEL_CACHE_TTL = 300
_MODEL_CACHE_LIMIT = 128
_model_cache = OrderedDict()
_last_used_model = None
_last_used_profile = None
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
        # 与 RSS 链路一致，忽略系统/环境代理（bot.py 已清理通用代理变量，
        # 出站走 Clash 虚拟网卡）。若保留 trust_env，Windows 代理绕过列表
        # 中的裸 IPv6 条目（如 ::1）会让 httpx 在构造客户端时把 ":1]" 当作
        # 端口抛出 InvalidURL，导致翻译请求整体失败。
        _ai_client = httpx.AsyncClient(trust_env=False)
    return _ai_client


def _get_ai_client_for_url(url):
    proxy = proxy_for_url("translation", url)
    if not proxy:
        return _get_ai_client()
    client = _ai_proxy_clients.get(proxy)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(proxy=proxy, trust_env=False)
        _ai_proxy_clients[proxy] = client
    return client


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
    clients = list(_ai_proxy_clients.values())
    _ai_proxy_clients.clear()
    if clients:
        await asyncio.gather(*(client.aclose() for client in clients if not client.is_closed))


async def _request_ai(method, *args, **kwargs):
    """Track one outbound request so shutdown can reap it before client close."""
    if _ai_stopping:
        raise AIClientError("AI API 客户端正在关闭")
    task = asyncio.current_task()
    if task is not None:
        _ai_request_tasks.add(task)
    try:
        url = args[0] if args else kwargs.get("url")
        return await getattr(_get_ai_client_for_url(url), method)(*args, **kwargs)
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


async def _request_completion(base_url, api_key, model, messages, max_tokens, temperature, timeout):
    ollama_endpoint = ollama_native_chat_endpoint(base_url)
    if ollama_endpoint:
        endpoint = ollama_endpoint
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
    else:
        endpoint = _endpoint(base_url)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    response = await _request_ai(
        "post",
        endpoint,
        headers={"Authorization": "Bearer " + api_key},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if ollama_endpoint:
        return clean_ollama_content(data["message"]["content"])
    return data["choices"][0]["message"]["content"]

def _profile_description(config):
    return str(config.get("_profile_name") or config.get("model") or "AI 配置")


async def _generate_with_profile(messages, config, max_tokens, temperature):
    api_key = os.getenv(str(config.get("api_key_env") or "WRELAY_AI_API_KEY"), "").strip()
    if not api_key:
        raise AIClientError("AI API 密钥未配置")
    configured_model = str(config.get("model") or "").strip()
    if not configured_model:
        raise AIClientError("AI 模型未配置")

    timeout = int(config.get("timeout_seconds") or 25)
    if ollama_native_chat_endpoint(config.get("base_url")):
        timeout = max(90, timeout)
    output_limit = int(max_tokens or config.get("max_output_tokens") or 800)
    try:
        content = await _request_completion(
            config.get("base_url"), api_key, configured_model, messages,
            output_limit, temperature, timeout,
        )
    except httpx.HTTPStatusError as exc:
        raise AIClientError("AI API 调用失败: %s" % exc) from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIClientError("AI API 调用失败: %s" % exc) from exc

    if not isinstance(content, str):
        raise AIClientError("AI API 返回的文本格式无效")
    text = content.strip()
    if not text:
        raise AIClientError("AI API 未返回文本")
    return text, configured_model


def _switchable_cause(error):
    current = error
    while current is not None:
        if _should_switch_model(current):
            return True
        current = getattr(current, "__cause__", None)
    return False


async def _try_discovered_fallback(messages, config, max_tokens, temperature, excluded_models):
    api_key = os.getenv(str(config.get("api_key_env") or "WRELAY_AI_API_KEY"), "").strip()
    if not api_key:
        return None
    timeout = int(config.get("timeout_seconds") or 25)
    if ollama_native_chat_endpoint(config.get("base_url")):
        timeout = max(90, timeout)
    output_limit = int(max_tokens or config.get("max_output_tokens") or 800)
    try:
        models = await _fetch_models(config.get("base_url"), api_key, timeout, force=True)
    except (AIClientError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        logger.warning("AI API 查询目录模型失败：{}", exc)
        return None
    candidates = _choose_fallback_models(models, excluded_models)
    for index, model in enumerate(candidates, start=1):
        logger.warning(
            "已配置的 AI 模型链均失败，尝试服务目录备用模型：{}（候选 {}/{}）",
            model,
            index,
            len(candidates),
        )
        try:
            content = await _request_completion(
                config.get("base_url"), api_key, model, messages,
                output_limit, temperature, timeout,
            )
        except httpx.HTTPStatusError as exc:
            if _should_switch_model(exc):
                continue
            logger.warning("AI API 目录备用模型调用失败：{}", exc)
            return None
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("AI API 目录备用模型调用失败：{}", exc)
            return None
        if isinstance(content, str) and content.strip():
            return content.strip(), model
        logger.warning("AI API 目录备用模型返回空内容：{}", model)
    return None


async def generate_text(messages, max_tokens=None, temperature=0.7):
    """按用户配置的 AI 模型优先级链调用，返回翻译纯文本。"""
    global _last_used_model, _last_used_profile
    configs = await asyncio.to_thread(get_ai_configs)
    errors = []
    models_by_endpoint = {}
    fallback_sources = OrderedDict()

    for index, config in enumerate(configs, start=1):
        model = str(config.get("model") or "").strip()
        endpoint_key = (
            str(config.get("base_url") or "").strip().rstrip("/"),
            str(config.get("api_key_env") or "WRELAY_AI_API_KEY"),
        )
        if model:
            models_by_endpoint.setdefault(endpoint_key, set()).add(model)
        try:
            text, used_model = await _generate_with_profile(
                messages, config, max_tokens, temperature
            )
        except AIClientError as exc:
            errors.append((config, exc))
            logger.warning(
                "AI 优先级模型失败，尝试下一项（{}/{}）：{} · {}",
                index,
                len(configs),
                _profile_description(config),
                exc,
            )
            if _switchable_cause(exc) and endpoint_key[0]:
                fallback_sources.setdefault(endpoint_key, config)
            continue

        _last_used_model = used_model
        _last_used_profile = _profile_description(config)
        return text

    # 用户配置的链路始终优先。仅在全部配置项失败后，才保留原有的服务端模型目录兜底。
    for endpoint_key, config in fallback_sources.items():
        fallback = await _try_discovered_fallback(
            messages,
            config,
            max_tokens,
            temperature,
            models_by_endpoint.get(endpoint_key, set()),
        )
        if fallback:
            _last_used_model = fallback[1]
            _last_used_profile = _profile_description(config) + "（服务目录兜底）"
            return fallback[0]

    if errors:
        _, last_error = errors[-1]
        summary = "；".join(
            "%s：%s" % (_profile_description(config), error)
            for config, error in errors
        )
        raise AIClientError("AI 模型优先级链全部失败：%s" % summary) from last_error
    raise AIClientError("AI 模型优先级链为空")
