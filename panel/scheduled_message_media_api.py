"""Scheduled-message media upload and preview endpoints."""

from urllib.parse import unquote

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from plugins.json_io import run_in_thread_to_completion
from plugins.scheduled_message_media import (
    SCHEDULED_ATTACHMENT_MAX_BYTES,
    prune_unreferenced_attachments,
    read_scheduled_attachment,
    save_scheduled_attachment,
)


def register_routes(app):
    @app.post("/api/scheduled-message-media")
    async def upload_scheduled_message_media(request: Request):
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > SCHEDULED_ATTACHMENT_MAX_BYTES:
                    return JSONResponse({"ok": False, "msg": "单个媒体文件不能超过 30 MiB"}, status_code=413)
            except ValueError:
                return JSONResponse({"ok": False, "msg": "上传长度无效"}, status_code=400)
        filename = unquote(request.headers.get("x-file-name", ""))
        if not filename:
            return JSONResponse({"ok": False, "msg": "缺少文件名"}, status_code=400)
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > SCHEDULED_ATTACHMENT_MAX_BYTES:
                return JSONResponse({"ok": False, "msg": "单个媒体文件不能超过 30 MiB"}, status_code=413)
            chunks.extend(chunk)
        try:
            descriptor = await run_in_thread_to_completion(
                save_scheduled_attachment,
                bytes(chunks),
                filename,
                request.headers.get("content-type", ""),
            )
        except (OSError, ValueError) as exc:
            return JSONResponse({"ok": False, "msg": str(exc)}, status_code=400)
        try:
            from plugins.config import load_settings
            current = await run_in_thread_to_completion(load_settings)
            await run_in_thread_to_completion(
                prune_unreferenced_attachments,
                current.get("scheduled_message") if isinstance(current, dict) else None,
                7 * 24 * 60 * 60,
            )
        except Exception:
            # Upload should still succeed if best-effort orphan cleanup cannot read configuration.
            pass
        return {"ok": True, "attachment": descriptor}

    @app.get("/api/scheduled-message-media/{attachment_id}")
    async def get_scheduled_message_media(attachment_id: str):
        try:
            data, _kind, mime_type = await run_in_thread_to_completion(
                read_scheduled_attachment,
                attachment_id,
            )
        except (OSError, ValueError) as exc:
            return JSONResponse({"ok": False, "msg": str(exc)}, status_code=404)
        return Response(
            content=data,
            media_type=mime_type,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

