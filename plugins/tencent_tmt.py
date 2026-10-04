"""腾讯机器翻译（TMT）TextTranslate 适配器。

腾讯 TMT 的旧文本接口目前处于变更期，因此这里把签名和接口隔离在
单一模块中。若账号或接口不可用，调用方会降级到 AI 或原文。
"""
import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

import httpx
from nonebot import get_driver

from plugins.config import get_tencent_tmt_config


class TencentTMTError(RuntimeError):
    """腾讯 TMT 未配置或调用失败。"""


_HOST = "tmt.tencentcloudapi.com"
_SERVICE = "tmt"
_VERSION = "2018-03-21"
_ALGORITHM = "TC3-HMAC-SHA256"
_tmt_client = None
_tmt_request_tasks = set()
_tmt_stopping = False


def _get_tmt_client():
    global _tmt_client
    if _tmt_client is None:
        _tmt_client = httpx.AsyncClient()
    return _tmt_client


@get_driver().on_shutdown
async def _close_tmt_client():
    global _tmt_client, _tmt_stopping
    _tmt_stopping = True
    request_tasks = list(_tmt_request_tasks)
    for task in request_tasks:
        if not task.done():
            task.cancel()
    if request_tasks:
        await asyncio.gather(*request_tasks, return_exceptions=True)
    _tmt_request_tasks.clear()
    if _tmt_client is not None:
        await _tmt_client.aclose()
        _tmt_client = None


async def _request_tmt(method, *args, **kwargs):
    """Track one outbound request so shutdown can reap it before client close."""
    if _tmt_stopping:
        raise TencentTMTError("腾讯 TMT 客户端正在关闭")
    task = asyncio.current_task()
    if task is not None:
        _tmt_request_tasks.add(task)
    try:
        return await getattr(_get_tmt_client(), method)(*args, **kwargs)
    finally:
        if task is not None:
            _tmt_request_tasks.discard(task)


def _sign(key, message):
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _authorization(secret_key, secret_id, timestamp, payload):
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_headers = "content-type:application/json; charset=utf-8\nhost:%s\n" % _HOST
    signed_headers = "content-type;host"
    canonical_request = "POST\n/\n\n%s\n%s\n%s" % (
        canonical_headers, signed_headers, payload_hash,
    )
    credential_scope = "%s/%s/tc3_request" % (date, _SERVICE)
    string_to_sign = "%s\n%s\n%s\n%s" % (
        _ALGORITHM,
        timestamp,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    )
    secret_date = _sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _sign(secret_date, _SERVICE)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return "%s Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (
        _ALGORITHM, secret_id, credential_scope, signed_headers, signature,
    )


async def translate_text(text, source="auto", target="zh"):
    """调用腾讯 TMT 文本翻译，返回译文；不记录原文或密钥。"""
    config = await asyncio.to_thread(get_tencent_tmt_config)
    secret_id = os.getenv(str(config.get("secret_id_env") or "WRELAY_TENCENT_SECRET_ID"), "").strip()
    secret_key = os.getenv(str(config.get("secret_key_env") or "WRELAY_TENCENT_SECRET_KEY"), "").strip()
    if not secret_id or not secret_key:
        raise TencentTMTError("腾讯 TMT 密钥未配置")

    payload = json.dumps({
        "SourceText": text,
        "Source": source or "auto",
        "Target": target or "zh",
        "ProjectId": int(config.get("project_id") or 0),
    }, ensure_ascii=False, separators=(",", ":"))
    timestamp = int(datetime.now(tz=timezone.utc).timestamp())
    headers = {
        "Authorization": _authorization(secret_key, secret_id, timestamp, payload),
        "Content-Type": "application/json; charset=utf-8",
        "Host": _HOST,
        "X-TC-Action": "TextTranslate",
        "X-TC-Version": _VERSION,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": str(config.get("region") or "ap-guangzhou"),
    }
    try:
        response = await _request_tmt(
            "post",
            "https://%s/" % _HOST,
            headers=headers,
            content=payload.encode("utf-8"),
            timeout=20,
        )
        response.raise_for_status()
        body = response.json().get("Response") or {}
        if body.get("Error"):
            raise TencentTMTError("腾讯 TMT 返回错误: %s" % body["Error"].get("Code", "unknown"))
        translated = str(body.get("TargetText") or "").strip()
    except (httpx.HTTPError, ValueError) as exc:
        raise TencentTMTError("腾讯 TMT 调用失败: %s" % exc) from exc
    if not translated:
        raise TencentTMTError("腾讯 TMT 未返回译文")
    return translated
