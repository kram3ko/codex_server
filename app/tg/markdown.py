"""CommonMark/GFM → Telegram-HTML renderer."""

import html
from typing import ClassVar
from urllib.parse import urlparse

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token

_TG_MESSAGE_LIMIT = 4096
_PRE_OPEN = "<pre>"
_PRE_CLOSE = "</pre>"
# TG приймає http(s)/tg/mailto у `<a href>`. Решта (`javascript:`, `file:`,
# `data:`) — або 400, або XSS-вектор → дроп до plain text.
_ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "tg", "mailto"})


class _TelegramHtmlRenderer(RendererHTML):
    """RendererHTML restricted to TG-whitelisted tags. Unknown tokens → ''."""

    _STATIC_TAGS: ClassVar[dict[str, str]] = {
        "strong_open": "<b>", "strong_close": "</b>",
        "em_open":     "<i>", "em_close":     "</i>",
        "s_open":      "<s>", "s_close":      "</s>",
        "heading_open": "<b>", "heading_close": "</b>\n\n",
        "softbreak":   "\n", "hardbreak": "\n",
        "hr":          "─" * 12 + "\n\n",
    }

    def __init__(self) -> None:
        super().__init__()
        self.rules = {tag: self._const(html_) for tag, html_ in self._STATIC_TAGS.items()}
        for name, method in vars(type(self)).items():
            if callable(method) and not name.startswith(("_", "render")):
                self.rules[name] = getattr(self, name)

    @staticmethod
    def _const(value: str):
        def render(tokens, idx, options, env):  # noqa: ARG001
            return value
        return render

    def renderToken(self, tokens, idx, options, env=None):  # noqa: ARG002
        return ""

    def text(self, tokens, idx, options, env):  # noqa: ARG002
        return html.escape(tokens[idx].content)

    def paragraph_close(self, tokens, idx, options, env):  # noqa: ARG002
        return "\n" if env.get("list_stack") or env.get("bq_depth") else "\n\n"

    def blockquote_open(self, tokens, idx, options, env):  # noqa: ARG002
        env["bq_depth"] = env.get("bq_depth", 0) + 1
        return "<blockquote>"

    def blockquote_close(self, tokens, idx, options, env):  # noqa: ARG002
        env["bq_depth"] -= 1
        return "</blockquote>\n\n"

    def bullet_list_open(self, tokens, idx, options, env):  # noqa: ARG002
        env.setdefault("list_stack", []).append(["ul", 0])
        return ""

    def bullet_list_close(self, tokens, idx, options, env):  # noqa: ARG002
        env["list_stack"].pop()
        return "" if env["list_stack"] else "\n"

    def ordered_list_open(self, tokens, idx, options, env):  # noqa: ARG002
        start = int(tokens[idx].attrGet("start") or 1)
        env.setdefault("list_stack", []).append(["ol", start])
        return ""

    def ordered_list_close(self, tokens, idx, options, env):  # noqa: ARG002
        env["list_stack"].pop()
        return "" if env["list_stack"] else "\n"

    def list_item_open(self, tokens, idx, options, env):  # noqa: ARG002
        kind, num = env["list_stack"][-1]
        indent = "  " * (len(env["list_stack"]) - 1)
        if kind == "ol":
            env["list_stack"][-1][1] = num + 1
            return f"{indent}{num}. "
        return f"{indent}• "

    def fence(self, tokens, idx, options, env):  # noqa: ARG002
        token = tokens[idx]
        body = html.escape(token.content.rstrip("\n"))
        lang = (token.info or "").strip().split(maxsplit=1)
        klass = f' class="language-{html.escape(lang[0])}"' if lang else ""
        return f"<pre><code{klass}>{body}</code></pre>\n\n"

    def code_block(self, tokens, idx, options, env):  # noqa: ARG002
        body = html.escape(tokens[idx].content.rstrip("\n"))
        return f"<pre><code>{body}</code></pre>\n\n"

    def code_inline(self, tokens, idx, options, env):  # noqa: ARG002
        return f"<code>{html.escape(tokens[idx].content)}</code>"

    def link_open(self, tokens, idx, options, env):  # noqa: ARG002
        href = str(tokens[idx].attrGet("href") or "")
        if urlparse(href).scheme.lower() not in _ALLOWED_LINK_SCHEMES:
            env["_skip_link_close"] = env.get("_skip_link_close", 0) + 1
            return ""
        return f'<a href="{html.escape(href, quote=True)}">'

    def link_close(self, tokens, idx, options, env):  # noqa: ARG002
        if env.get("_skip_link_close"):
            env["_skip_link_close"] -= 1
            return ""
        return "</a>"

    def image(self, tokens, idx, options, env):  # noqa: ARG002
        # TG inline-картинок у тексті не підтримує — лишаємо тільки alt.
        return html.escape(tokens[idx].content or "")


_PLAIN_BLOCK_BREAKS = frozenset(
    {"paragraph_close", "heading_close", "blockquote_close", "hr"},
)


def _render_plain(tokens: list[Token]) -> str:
    out: list[str] = []
    for token in tokens:
        if token.type == "inline":
            out.append(_inline_plain(token.children or []))
        elif token.type in {"fence", "code_block"}:
            out.append(token.content.rstrip("\n") + "\n")
        elif token.type in _PLAIN_BLOCK_BREAKS:
            out.append("\n")
    return "".join(out)


def _inline_plain(children: list[Token]) -> str:
    out: list[str] = []
    for token in children:
        if token.type in {"text", "code_inline"}:
            out.append(token.content)
        elif token.type == "softbreak":
            out.append(" ")
        elif token.type == "hardbreak":
            out.append("\n")
        elif token.type == "image":
            out.append(token.content or "")
    return "".join(out)


class TelegramMarkdown:
    """Render markdown → TG-HTML chunks (TG-safe tags) або flat plaintext."""

    def __init__(self) -> None:
        self._md = (
            MarkdownIt("commonmark", {"breaks": True, "html": False})
            .enable("strikethrough")
        )
        self._html = _TelegramHtmlRenderer()

    def render_html(self, text: str) -> list[str]:
        if not text.strip():
            return []
        tokens = self._md.parse(text)
        rendered = self._html.render(tokens, self._md.options, {}).strip()
        return _split_chunks(rendered, _TG_MESSAGE_LIMIT)

    def to_plain(self, text: str) -> str:
        """TTS-orient strip: ⚠ links → alt-text без URL (інакше Google озвучує
        весь URL вголос), code blocks → сирий зміст без фенсів."""
        if not text.strip():
            return ""
        return _render_plain(self._md.parse(text)).strip()

    @staticmethod
    def has_unclosed_fence(text: str) -> bool:
        """True якщо текст містить ``` без парного закриття."""
        return text.count("```") % 2 == 1

    @staticmethod
    def escape(text: str) -> str:
        """Escape-only для plain текстів (системні повідомлення без markdown)."""
        return html.escape(text, quote=False)


tg_markdown = TelegramMarkdown()


def _split_chunks(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut, force_pre_split = _safe_cut(rest, limit)
        head = rest[:cut].rstrip()
        if force_pre_split:
            head += "</code></pre>"
            rest = "<pre><code>" + rest[cut:].lstrip()
        else:
            rest = rest[cut:].lstrip()
        chunks.append(head)
    if rest:
        chunks.append(rest)
    return chunks


def _safe_cut(text: str, limit: int) -> tuple[int, bool]:
    """Знайти позицію ≤limit. Повертає (idx, force_pre_split):
    `force_pre_split=True` — cut усередині <pre>; caller балансує теги.
    """
    pre_ranges = _pre_ranges(text[: limit + len(_PRE_CLOSE)])

    def inside(idx: int) -> bool:
        return any(start < idx < end for start, end in pre_ranges)

    for sep in ("\n\n", "\n", " "):
        idx = text.rfind(sep, 0, limit)
        while idx > 0 and inside(idx):
            idx = text.rfind(sep, 0, idx)
        if idx > limit // 2:
            return idx + len(sep), False
    # Усе вікно всередині <pre> — ріжемо по newline у коді, балансуємо теги.
    safe_limit = limit - len("</code></pre>")
    nl = text.rfind("\n", 0, safe_limit)
    if nl > 0:
        return nl + 1, True
    return safe_limit, True


def _pre_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    pos = 0
    while True:
        start = text.find(_PRE_OPEN, pos)
        if start == -1:
            return ranges
        end = text.find(_PRE_CLOSE, start)
        if end == -1:
            ranges.append((start, len(text)))
            return ranges
        end += len(_PRE_CLOSE)
        ranges.append((start, end))
        pos = end
