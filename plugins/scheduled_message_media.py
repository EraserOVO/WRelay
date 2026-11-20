"""定时消息媒体附件的格式校验、Bot 隔离存储与旧图片迁移。"""

import base64
import binascii
import os
import re
import time
import uuid
from pathlib import Path

from plugins.paths import data_path


SCHEDULED_ATTACHMENT_MAX_BYTES = 30 * 1024 * 1024
SCHEDULED_ATTACHMENTS_TOTAL_MAX_BYTES = 30 * 1024 * 1024
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

# 文件扩展名决定发送类型；实际内容仍必须通过对应签名检查。
_FORMATS = {
    ".png": ("image", "image/png", _IMAGE_SIGNATURES["image/png"]),
    ".jpg": ("image", "image/jpeg", _IMAGE_SIGNATURES["image/jpeg"]),
    ".jpeg": ("image", "image/jpeg", _IMAGE_SIGNATURES["image/jpeg"]),
    ".gif": ("image", "image/gif", _IMAGE_SIGNATURES["image/gif"]),
    ".webp": ("image", "image/webp", _IMAGE_SIGNATURES["image/webp"]),
    ".mp4": ("video", "video/mp4", lambda data: len(data) >= 12 and data[4:8] == b"ftyp"),
    ".webm": ("video", "video/webm", lambda data: data.startswith(b"\x1aE\xdf\xa3")),
    ".mp3": ("audio", "audio/mpeg", lambda data: data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xff and data[1] & 0xe0 == 0xe0)),
    ".wav": ("audio", "audio/wav", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"),
    ".ogg": ("audio", "audio/ogg", lambda data: data.startswith(b"OggS")),
    ".m4a": ("audio", "audio/mp4", lambda data: len(data) >= 12 and data[4:8] == b"ftyp"),
}
_ATTACHMENT_ID = re.compile(r"^[0-9a-f]{32}\.(?:png|jpe?g|gif|webp|mp4|webm|mp3|wav|ogg|m4a)$")
_PROFILE_DIR = re.compile(r"^(?:official_[1-9][0-9]{0,19}|personal_[1-9][0-9]{4,19}|unassigned|legacy)$")


def decode_scheduled_image_data(data_url):
    """校验旧版 Data URL、实际图片签名与旧大小限制，返回 (bytes, MIME 类型)。"""
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


def attachment_type_for_mime(mime_type):
    for attachment_type, candidate_mime, _signature in _FORMATS.values():
        if candidate_mime == mime_type:
            return attachment_type
    raise ValueError("不支持的媒体格式")


def extension_for_mime(mime_type):
    for extension, (_kind, candidate_mime, _signature) in _FORMATS.items():
        if candidate_mime == mime_type:
            return ".jpg" if extension == ".jpeg" else extension
    raise ValueError("不支持的媒体格式")


def validate_attachment_bytes(data, filename, content_type=""):
    """根据扩展名确认媒体类型，并以文件头验证实际格式。"""
    if not isinstance(data, bytes) or not data:
        raise ValueError("媒体文件不能为空")
    if len(data) > SCHEDULED_ATTACHMENT_MAX_BYTES:
        raise ValueError("单个媒体文件不能超过 30 MiB")
    extension = Path(str(filename or "")).suffix.lower()
    if not extension and content_type:
        extension = extension_for_mime(content_type.lower())
    spec = _FORMATS.get(extension)
    if spec is None:
        raise ValueError("支持的格式：PNG、JPEG、GIF、WebP、MP4、WebM、MP3、WAV、OGG、M4A")
    attachment_type, mime_type, signature = spec
    claimed_type = str(content_type or "").split(";", 1)[0].strip().lower()
    claimed_major = claimed_type.split("/", 1)[0]
    if claimed_major in ("image", "video", "audio") and claimed_major != attachment_type:
        raise ValueError("文件扩展名与媒体类型不一致")
    if not signature(data):
        raise ValueError("文件格式与文件内容不匹配")
    return attachment_type, mime_type, extension


def profile_directory(bot_id=None):
    owner = bot_id
    if owner is None:
        from plugins.bot_profiles import CURRENT
        owner = CURRENT.get()
    if not owner:
        return "legacy"
    from plugins.bot_profiles import filename
    return Path(filename(str(owner))).stem


def attachment_root(bot_id=None):
    return Path(data_path("scheduled_message_media")) / profile_directory(bot_id)


def attachment_relative_path(attachment_id, bot_id=None):
    if not isinstance(attachment_id, str) or not _ATTACHMENT_ID.fullmatch(attachment_id):
        raise ValueError("定时消息附件标识无效")
    return "scheduled_message_media/%s/%s" % (profile_directory(bot_id), attachment_id)


def is_safe_archive_name(name):
    match = re.fullmatch(r"scheduled_message_media/([^/]+)/([^/]+)", str(name))
    return bool(match and _PROFILE_DIR.fullmatch(match.group(1)) and _ATTACHMENT_ID.fullmatch(match.group(2)))


def attachment_path(attachment_id, bot_id=None):
    # Reuse the archive path validator so filenames cannot escape the owner folder.
    relative = attachment_relative_path(attachment_id, bot_id)
    if not is_safe_archive_name(relative):
        raise ValueError("定时消息附件路径无效")
    return attachment_root(bot_id) / attachment_id


def _safe_display_name(filename):
    name = os.path.basename(str(filename or "附件").replace("\\", "/"))
    name = "".join(char for char in name if char >= " " and char != "\x7f").strip()
    if not name:
        name = "附件"
    return name[:255]


def save_scheduled_attachment(data, filename, content_type="", bot_id=None, display_name=None):
    attachment_type, mime_type, extension = validate_attachment_bytes(data, filename, content_type)
    root = attachment_root(bot_id)
    root.mkdir(parents=True, exist_ok=True)
    attachment_id = uuid.uuid4().hex + extension
    path = attachment_path(attachment_id, bot_id)
    temp = path.with_name(path.name + ".upload.tmp")
    try:
        with open(temp, "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
    return {
        "attachment_id": attachment_id,
        "attachment_type": attachment_type,
        "attachment_mime": mime_type,
        "attachment_name": _safe_display_name(display_name or filename),
        "attachment_size": len(data),
    }


def read_scheduled_attachment(attachment_id, expected_type=None, expected_mime=None, expected_size=None, bot_id=None):
    path = attachment_path(attachment_id, bot_id)
    try:
        with open(path, "rb") as stream:
            data = stream.read(SCHEDULED_ATTACHMENT_MAX_BYTES + 1)
    except OSError as exc:
        raise ValueError("定时消息附件文件不存在或无法读取") from exc
    filename = attachment_id
    attachment_type, mime_type, _extension = validate_attachment_bytes(data, filename)
    if expected_type and expected_type != attachment_type:
        raise ValueError("定时消息附件类型与文件不一致")
    if expected_mime and expected_mime != mime_type:
        raise ValueError("定时消息附件 MIME 类型与文件不一致")
    if expected_size is not None and expected_size != len(data):
        raise ValueError("定时消息附件大小与文件不一致")
    return data, attachment_type, mime_type


def validate_attachment_descriptor(message):
    attachment_id = message.get("attachment_id")
    if not isinstance(attachment_id, str) or not attachment_id:
        raise ValueError("附件标识必须是非空字符串")
    attachment_type = message.get("attachment_type")
    if attachment_type not in ("image", "video", "audio"):
        raise ValueError("附件类型必须是 image、video 或 audio")
    mime_type = message.get("attachment_mime")
    name = message.get("attachment_name")
    size = message.get("attachment_size")
    if not isinstance(mime_type, str) or not isinstance(name, str) or len(name) > 255:
        raise ValueError("附件 MIME 类型或文件名无效")
    if type(size) is not int or not 0 < size <= SCHEDULED_ATTACHMENT_MAX_BYTES:
        raise ValueError("附件大小必须大于 0 且不超过 30 MiB")
    _data, actual_type, actual_mime = read_scheduled_attachment(
        attachment_id, attachment_type, mime_type, size
    )
    return actual_type, actual_mime, size


def validate_attachment_metadata(attachment_id, attachment_type, mime_type):
    """仅验证可持久化描述字段，不依赖当前 Bot 的磁盘目录。"""
    if not isinstance(attachment_id, str) or not _ATTACHMENT_ID.fullmatch(attachment_id):
        raise ValueError("附件标识无效")
    extension = Path(attachment_id).suffix.lower()
    spec = _FORMATS.get(extension)
    if not spec or spec[0] != attachment_type or spec[1] != mime_type:
        raise ValueError("附件类型或 MIME 类型与附件标识不一致")


def migrate_legacy_scheduled_attachments(scheduled):
    """将旧配置内嵌的小图片保存为文件并返回创建的新附件列表。"""
    created = []
    if not isinstance(scheduled, dict):
        return created
    messages = []
    groups = scheduled.get("message_groups")
    if isinstance(groups, list) and groups:
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("messages"), list):
                messages.extend(group["messages"])
    elif isinstance(scheduled.get("messages"), list):
        messages.extend(scheduled["messages"])
    for message in messages:
        if not isinstance(message, dict):
            continue
        data_url = message.get("image_data")
        if not data_url:
            continue
        image_data, mime_type = decode_scheduled_image_data(data_url)
        original_name = message.get("image_name") or "scheduled-image"
        descriptor = save_scheduled_attachment(
            image_data,
            "scheduled-image" + extension_for_mime(mime_type),
            mime_type,
            display_name=original_name,
        )
        message.update(descriptor)
        message.pop("image_data", None)
        message.pop("image_name", None)
        created.append(descriptor["attachment_id"])
    return created


def prune_unreferenced_attachments(scheduled, older_than_seconds=0):
    """删除当前 Bot 配置不再引用的已保存附件。"""
    referenced = set()
    if isinstance(scheduled, dict):
        groups = scheduled.get("message_groups")
        if isinstance(groups, list) and groups:
            messages = []
            for group in groups:
                if isinstance(group, dict) and isinstance(group.get("messages"), list):
                    messages.extend(group["messages"])
        else:
            messages = list(scheduled.get("messages") or []) if isinstance(scheduled.get("messages"), list) else []
        for message in messages:
            if isinstance(message, dict) and isinstance(message.get("attachment_id"), str):
                if _ATTACHMENT_ID.fullmatch(message["attachment_id"]):
                    referenced.add(message["attachment_id"])
    root = attachment_root()
    if not root.is_dir():
        return
    minimum_mtime = time.time() - max(0, float(older_than_seconds or 0))
    for path in root.iterdir():
        if path.is_file() and _ATTACHMENT_ID.fullmatch(path.name) and path.name not in referenced:
            try:
                if path.stat().st_mtime <= minimum_mtime:
                    path.unlink()
            except OSError:
                pass
