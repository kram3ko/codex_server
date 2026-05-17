"""Sidecar-name enum. Codex CLI має 2 інстанси: `admin` (повний доступ до
repo) і `guest` (sandbox). Зовнішні джерела (Postgres `turns.sidecar` column
як `str`, RPC handlers від ролі користувача) дають `str | None` —
`SidecarName.normalize()` зводить до enum-value з `ADMIN` fallback.

Postgres column лишається `str` (без ENUM міграції — cost > benefit для 2
значень). StrEnum дає f-string serialization як plain string у Redis ключах
і channel names без зайвої конверсії."""

import enum


class SidecarName(enum.StrEnum):
    ADMIN = "admin"
    GUEST = "guest"

    @classmethod
    def normalize(cls, value: str | None) -> SidecarName:
        """`ADMIN` fallback на None або невідоме значення — admin sidecar є
        завжди у будь-якому setup-у, guest опціональний."""
        try:
            return cls(value) if value is not None else cls.ADMIN
        except ValueError:
            return cls.ADMIN
