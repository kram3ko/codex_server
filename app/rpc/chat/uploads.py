"""Resolve upload_ids → (codex-input data URIs, image_ids, audio_ids, file_paths, file_ids).

Codex отримує image data URIs inline (його `url` форвардиться у OpenAI, де
Docker-internal MinIO unreachable). Audio з codex-input виключений — transcript
уже у `text` — але id зберігаємо для рендерингу `<audio>` з історії.

Non-image, non-audio файли пишуться у workspace `uploads/chat-<id>/` і
згадуються у user-тексті — Codex читає їх через свій shell tool.
"""

import base64
from io import BytesIO

from connectrpc.code import Code
from connectrpc.errors import ConnectError

from app.db.base import SessionLocal
from app.services.uploads.default import upload_service, workspace_uploads


async def resolve_uploads(
    upload_ids: list[int],
    *,
    user_id: int,
    chat_id: int,
) -> tuple[tuple[str, ...], list[int], list[int], tuple[str, ...], list[int]]:
    """Returns ``(image_data_uris, image_ids, audio_ids, file_paths, file_ids)``."""
    if not upload_ids:
        return (), [], [], (), []
    urls: list[str] = []
    image_ids: list[int] = []
    audio_ids: list[int] = []
    file_paths: list[str] = []
    file_ids: list[int] = []
    async with SessionLocal() as db:
        for uid in upload_ids:
            upload = await upload_service.get(db, uid, user_id=user_id)
            if upload is None:
                raise ConnectError(Code.NOT_FOUND, f"upload {uid} not found")
            if upload.mime.startswith("audio/"):
                audio_ids.append(uid)
                continue
            buf = BytesIO()
            await upload_service.download_to_stream(upload, buf)
            data = buf.getvalue()
            if upload.mime.startswith("image/"):
                image_ids.append(uid)
                b64 = base64.b64encode(data).decode("ascii")
                urls.append(f"data:{upload.mime};base64,{b64}")
                continue
            path = await workspace_uploads.materialize_for_chat(
                data,
                chat_id=chat_id,
                upload_id=uid,
                filename=upload.filename,
            )
            file_paths.append(path)
            file_ids.append(uid)
    return tuple(urls), image_ids, audio_ids, tuple(file_paths), file_ids
