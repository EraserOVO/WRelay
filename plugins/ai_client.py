"""OpenAI 兼容的公告翻译文本生成客户端。"""
import os

import httpx

from plugins.config import get_ai_config


class AIClientError(RuntimeError):
    """AI 服务未配置、请求失败或响应格式无效。"""


def _endpoint(base_url):
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise AIClientError("AI API 未配置 base_url")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


async def generate_text(messages, max_tokens=None, temperature=0.7):
    """调用已配置的 OpenAI 兼容接口，返回供翻译路由使用的纯文本。"""
    config = get_ai_config()
    api_key = os.getenv(str(config.get("api_key_env") or "WRELAY_AI_API_KEY"), "").strip()
    if not api_key:
        raise AIClientError("AI API 密钥未配置")
    model = str(config.get("model") or "").strip()
    if not model:
        raise AIClientError("AI 模型未配置")

    timeout = int(config.get("timeout_seconds") or 25)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": int(max_tokens or config.get("max_output_tokens") or 800),
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                _endpoint(config.get("base_url")),
                headers={"Authorization": "Bearer " + api_key},
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIClientError("AI API 调用失败: %s" % exc) from exc

    text = str(content or "").strip()
    if not text:
        raise AIClientError("AI API 未返回文本")
    return text
