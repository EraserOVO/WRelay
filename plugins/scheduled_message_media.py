"""QQ 定时消息图片附件的数据校验。"""

import base64
import binascii
import re


SCHEDULED_IMAGE_MAX_BYTES = 2 * 1024 * 1024
SCHEDULED_IMAGES_TOTAL_MAX_BYTES = 3 * 1024 * 1024

_IMAGE_DATA_URL = re.compile(
    r"^data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/]*={0,2})$"
)
_IMAGE_SIGNATURES = {
    "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
    "image/gif": lambda data: data.startswith((b"GIF87a", b"GIF89a")),
    "image/webp": lambda data: (
        len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    ),
}


def decode_scheduled_image_data(data_url):
    """校验 data URL、实际图片签名与大小，返回 (bytes, MIME 类型)。"""
    if not isinstance(data_url, str):
        raise ValueError("图片数据必须是字符串")
    if len(data_url) > 4 * ((SCHEDULED_IMAGE_MAX_BYTES + 2) // 3) + 32:
        raise ValueError("单张图片不能超过 2 MiB")
    match = _IMAGE_DATA_URL.fullmatch(data_url)
    if not match:
        raise ValueError("仅支持 PNG、JPEG、GIF 或 WebP 图片")
    mime_type, encoded = match.groups()
    try:
        image_data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("图片数据编码无效") from exc
    if not image_data:
        raise ValueError("图片不能为空")
    if len(image_data) > SCHEDULED_IMAGE_MAX_BYTES:
        raise ValueError("单张图片不能超过 2 MiB")
    if not _IMAGE_SIGNATURES[mime_type](image_data):
        raise ValueError("图片格式与文件内容不匹配")
    return image_data, mime_type
