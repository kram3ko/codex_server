"""Model catalog from the sidecar (`model/list`), cached per sidecar in Redis."""

import orjson
import structlog
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.services.cache.default import cache
from app.services.codex.codex_remote import one_shot_client
from app.services.codex.sidecar import SidecarName
from app.services.codex_prefs.schemas import CodexModel, CodexModelIn

log = structlog.get_logger(__name__)

_CACHE_KEY_PREFIX = "codex:models:"
_CACHE_TTL_S = 600


def _cache_key(sidecar: SidecarName) -> str:
    return f"{_CACHE_KEY_PREFIX}{sidecar.value}"


class CodexModelCatalog:
    async def list(self, sidecar: SidecarName) -> list[CodexModel]:
        cached = await self._read_cache(sidecar)
        if cached is not None:
            return cached
        async with one_shot_client(sidecar is SidecarName.ADMIN) as client:
            raw = await client.list_models()
        models = self._parse(raw)
        await self._write_cache(sidecar, models)
        return models

    async def find(self, sidecar: SidecarName, model_id: str) -> CodexModel | None:
        return next((m for m in await self.list(sidecar) if m.id == model_id), None)

    @staticmethod
    def _parse(raw: list[dict[str, object]]) -> list[CodexModel]:
        models: list[CodexModel] = []
        for item in raw:
            try:
                parsed = CodexModelIn.model_validate(item)
            except ValidationError as exc:
                log.warning("codex_model_parse_failed", error=str(exc))
                continue
            if not parsed.hidden:
                models.append(parsed.to_domain())
        return models

    @staticmethod
    async def _read_cache(sidecar: SidecarName) -> list[CodexModel] | None:
        try:
            raw = await cache.get(_cache_key(sidecar))
        except RedisError as exc:
            log.warning("codex_models_cache_read_failed", error=str(exc))
            return None
        if raw is None:
            return None
        try:
            return [CodexModel.model_validate(m) for m in orjson.loads(raw)]
        except orjson.JSONDecodeError, ValidationError:
            return None

    @staticmethod
    async def _write_cache(sidecar: SidecarName, models: list[CodexModel]) -> None:
        payload = orjson.dumps([m.model_dump() for m in models])
        try:
            await cache.set(_cache_key(sidecar), payload, ex=_CACHE_TTL_S)
        except RedisError as exc:
            log.warning("codex_models_cache_write_failed", error=str(exc))
