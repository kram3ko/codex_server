"""Resolve upload_ids → (codex-input data URIs, image_ids, audio_ids).

Codex отримує image data URIs inline (його `url` форвардиться у OpenAI, де
Docker-internal MinIO unreachable). Audio з codex-input виключений — transcript
уже у `text` — але id зберігаємо для рендерингу `<audio>` з історії.
"""

import base64
from io import BytesIO

from connectrpc.code import Code
from connectrpc.errors import ConnectError

from app.db.base import SessionLocal
from app.services.uploads.default import upload_service


async def resolve_uploads(
    upload_ids: list[int],
    *,
    user_id: int,
) -> tuple[tuple[str, ...], list[int], list[int]]:
    if not upload_ids:
        return (), [], []
    urls: list[str] = []
    image_ids: list[int] = []
    audio_ids: list[int] = []
    async with SessionLocal() as db:
        for uid in upload_ids:
            upload = await upload_service.get(db, uid, user_id=user_id)
            if upload is None:
                raise ConnectError(Code.NOT_FOUND, f"upload {uid} not found")
            if upload.mime.startswith("audio/"):
                audio_ids.append(uid)
                continue
            image_ids.append(uid)
            buf = BytesIO()
            await upload_service.download_to_stream(upload, buf)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            urls.append(f"data:{upload.mime};base64,{b64}")
    return tuple(urls), image_ids, audio_ids
