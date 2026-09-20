"""验证 AI API 模型不可用时会读取模型目录并自动切换。"""
import asyncio
import os
from unittest.mock import patch

import httpx
import nonebot

nonebot.init()

from plugins import ai_client


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request, json=self._payload)
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, blocked_model, blocked_status, blocked_message, models=None):
        self.post_models = []
        self.get_paths = []
        self.blocked_model = blocked_model
        self.blocked_status = blocked_status
        self.blocked_message = blocked_message
        self.models = list(models or ["DeepSeek-V4.1-Flash"])

    async def post(self, _url, *, json, **_kwargs):
        self.post_models.append(json["model"])
        if json["model"] == self.blocked_model:
            return FakeResponse(
                self.blocked_status,
                {"error": {"message": self.blocked_message}},
            )
        return FakeResponse(200, {"choices": [{"message": {"content": "译文"}}]})

    async def get(self, url, **_kwargs):
        self.get_paths.append(url)
        return FakeResponse(200, {"data": [{"id": model} for model in self.models]})


async def main():
    scenarios = (
        ("obsolete-model", 400, "Unsupported model: obsolete-model", "unsupported"),
        (
            "busy-model", 429,
            "Model 'busy-model' is at its concurrency limit (32); please retry later",
            "concurrency",
        ),
        (
            "worker-model", 500,
            "Error from provider self-deploy: 503 Service Unavailable; no_available_workers",
            "worker-500",
        ),
        (
            "unavailable-model", 503,
            "Service Unavailable: model overloaded",
            "unavailable-503",
        ),
    )
    for blocked_model, blocked_status, blocked_message, label in scenarios:
        client = FakeClient(blocked_model, blocked_status, blocked_message)
        config = {
            "base_url": "https://example.test/v1",
            "model": blocked_model,
            "api_key_env": "WRELAY_TEST_KEY",
            "timeout_seconds": 5,
            "max_output_tokens": 16,
            "_profile_id": "profile-" + label,
        }
        ai_client._model_cache.clear()
        ai_client._model_overrides.clear()
        ai_client._model_recheck_at.clear()
        with patch.object(ai_client, "_ai_client", client), patch.object(
            ai_client, "get_ai_config", return_value=config
        ), patch.dict(os.environ, {"WRELAY_TEST_KEY": "test-key"}, clear=False):
            assert await ai_client.generate_text([{"role": "user", "content": "hello"}]) == "译文"
            assert client.post_models == [blocked_model, "DeepSeek-V4.1-Flash"]
            assert ai_client.get_last_used_model() == "DeepSeek-V4.1-Flash"
            assert client.get_paths == ["https://example.test/v1/models"]
            client.post_models.clear()
            client.get_paths.clear()
            assert await ai_client.generate_text([{"role": "user", "content": "hello"}]) == "译文"
            assert client.post_models == ["DeepSeek-V4.1-Flash"]
            assert client.get_paths == []
    client = FakeClient("quota-model", 429, "rate limit exceeded")
    config = {
        "base_url": "https://example.test/v1",
        "model": "quota-model",
        "api_key_env": "WRELAY_TEST_KEY",
        "timeout_seconds": 5,
        "max_output_tokens": 16,
        "_profile_id": "profile-quota",
    }
    ai_client._model_cache.clear()
    ai_client._model_overrides.clear()
    ai_client._model_recheck_at.clear()
    with patch.object(ai_client, "_ai_client", client), patch.object(
        ai_client, "get_ai_config", return_value=config
    ), patch.dict(os.environ, {"WRELAY_TEST_KEY": "test-key"}, clear=False):
        try:
            await ai_client.generate_text([{"role": "user", "content": "hello"}])
        except ai_client.AIClientError as exc:
            assert "AI API 调用失败" in str(exc)
        else:
            raise AssertionError("普通配额 429 不应自动切换模型")
        assert client.get_paths == []
    client = FakeClient(
        "busy-model", 429,
        "Model 'busy-model' is at its concurrency limit (32); please retry later",
        models=["DeepSeek-V4.1-Flash"],
    )
    config = {
        "base_url": "https://example.test/v1",
        "model": "busy-model",
        "api_key_env": "WRELAY_TEST_KEY",
        "timeout_seconds": 5,
        "max_output_tokens": 16,
        "_profile_id": "profile-recovery",
    }
    ai_client._model_cache.clear()
    ai_client._model_overrides.clear()
    ai_client._model_recheck_at.clear()
    with patch.object(ai_client, "_ai_client", client), patch.object(
        ai_client, "get_ai_config", return_value=config
    ), patch.dict(os.environ, {"WRELAY_TEST_KEY": "test-key"}, clear=False):
        assert await ai_client.generate_text([{"role": "user", "content": "hello"}]) == "译文"
        assert client.post_models == ["busy-model", "DeepSeek-V4.1-Flash"]
        client.post_models.clear()
        client.get_paths.clear()
        client.blocked_model = None
        client.models = ["busy-model", "DeepSeek-V4.1-Flash"]
        ai_client._model_recheck_at.clear()
        assert await ai_client.generate_text([{"role": "user", "content": "hello"}]) == "译文"
        assert client.get_paths == ["https://example.test/v1/models"]
        assert client.post_models == ["busy-model"]
        assert ai_client._model_overrides == {}
    print("PASS - AI API 模型不可用时自动切换")


if __name__ == "__main__":
    asyncio.run(main())
