"""Resolve upload_ids → typed bundle for codex-input.

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
from pydantic import BaseModel, ConfigDict, Field

from app.db.base import SessionLocal
from app.services.uploads.default import upload_service, workspace_uploads


class ResolvedUploads(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    image_urls: tuple[str, ...] = Field(description="Inline `data:` URIs для зображень.")
    image_ids: tuple[int, ...] = Field(description="DB upload ids зображень.")
    audio_ids: tuple[int, ...] = Field(description="DB upload ids аудіо (transcript у text).")
    file_paths: tuple[str, ...] = Field(
        description="Workspace-relative paths не-image файлів для shell-доступу Codex'у."
    )
    file_ids: tuple[int, ...] = Field(description="DB upload ids не-image файлів.")


async def resolve_uploads(
    upload_ids: list[int],
    *,
    user_id: int,
    chat_id: int,
) -> ResolvedUploads:
    if not upload_ids:
        return ResolvedUploads(
            image_urls=(), image_ids=(), audio_ids=(), file_paths=(), file_ids=()
        )
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
    return ResolvedUploads(
        image_urls=tuple(urls),
        image_ids=tuple(image_ids),
        audio_ids=tuple(audio_ids),
        file_paths=tuple(file_paths),
        file_ids=tuple(file_ids),
    )
