"""Telegram text formatting helpers."""

import html

_TG_MESSAGE_LIMIT = 4096


def tg_html(text: str) -> str:
    return html.escape(text, quote=False)


def split_tg_message(text: str) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    rest = text
    while rest:
        chunk = rest[:_TG_MESSAGE_LIMIT]
        if len(rest) > _TG_MESSAGE_LIMIT:
            split_at = max(chunk.rfind("\n"), chunk.rfind(" "))
            if split_at > _TG_MESSAGE_LIMIT // 2:
                chunk = rest[:split_at]
        chunks.append(chunk)
        rest = rest[len(chunk) :].lstrip()
    return chunks
