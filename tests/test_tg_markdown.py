from app.tg.markdown import tg_markdown


def test_render_inline_formatting() -> None:
    out = "".join(tg_markdown.render_html(
        "**bold** *italic* `code` ~~strike~~ [link](https://x.com)",
    ))
    assert "<b>bold</b>" in out
    assert "<i>italic</i>" in out
    assert "<code>code</code>" in out
    assert "<s>strike</s>" in out
    assert '<a href="https://x.com">link</a>' in out


def test_render_headings_become_bold() -> None:
    out = "".join(tg_markdown.render_html("# Title\n\nbody"))
    assert "<b>Title</b>" in out
    assert "<h1>" not in out  # TG would 400 on raw heading tags


def test_fence_escapes_html_and_keeps_language() -> None:
    out = "".join(tg_markdown.render_html("```python\nif a < b & c:\n    pass\n```"))
    assert '<pre><code class="language-python">' in out
    assert "if a &lt; b &amp; c:" in out


def test_lists_use_bullet_markers_and_nesting() -> None:
    out = "".join(tg_markdown.render_html("- one\n- two\n  - nested\n- three"))
    assert "• one" in out
    assert "  • nested" in out
    assert "<ul>" not in out


def test_ordered_list_keeps_numbers() -> None:
    out = "".join(tg_markdown.render_html("1. first\n2. second"))
    assert "1. first" in out
    assert "2. second" in out
    assert "<ol>" not in out


def test_blockquote_wraps_content() -> None:
    out = "".join(tg_markdown.render_html("> a\n> b"))
    assert out.count("<blockquote>") == 1
    assert "a\nb" in out


def test_unsupported_html_block_drops_silently() -> None:
    """TG валиться 400 на raw HTML tags — рендер має дропати їх."""
    out = "".join(tg_markdown.render_html("<table><tr><td>x</td></tr></table>"))
    assert "<table>" not in out
    assert "<td>" not in out


def test_chunking_keeps_each_chunk_balanced() -> None:
    """4096+ char fence: розбиваємо по newline + перевідкриваємо <pre>, інакше TG → 400."""
    body = "x = 1\n" * 700  # > 4096 chars
    chunks = tg_markdown.render_html(f"```py\n{body}```")
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 4096, f"chunk overruns TG limit: {len(chunk)}"
        assert chunk.count("<pre>") == chunk.count("</pre>"), "unbalanced <pre>"
        assert chunk.count("<code") == chunk.count("</code>"), "unbalanced <code>"


def test_to_plain_strips_markdown_for_tts() -> None:
    plain = tg_markdown.to_plain(
        "# Title\n\n**bold** *italic* `code` ~~strike~~ [link](https://x.com)\n\n"
        "```py\nx=1\n```",
    )
    assert "**" not in plain
    assert "`" not in plain
    assert "https://x.com" not in plain
    assert "Title" in plain
    assert "bold" in plain
    assert "x=1" in plain


def test_has_unclosed_fence_detects_in_progress_code_block() -> None:
    assert tg_markdown.has_unclosed_fence("text\n```py\nx=1") is True
    assert tg_markdown.has_unclosed_fence("text\n```py\nx=1\n```") is False
    assert tg_markdown.has_unclosed_fence("plain text") is False


def test_empty_input_returns_no_chunks() -> None:
    assert tg_markdown.render_html("") == []
    assert tg_markdown.render_html("   \n\n   ") == []
    assert tg_markdown.to_plain("") == ""


def test_escape_safe_for_plain_messages() -> None:
    assert tg_markdown.escape("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_link_scheme_whitelist_drops_unsafe_href() -> None:
    """Unsafe схеми (`javascript:`, `file:`, etc) не мають створювати <a href>."""
    out = "".join(tg_markdown.render_html("see [a](https://x.com) and [b](http://y.com)"))
    assert '<a href="https://x.com">a</a>' in out
    assert '<a href="http://y.com">b</a>' in out

    # markdown-it дефолтно блокує javascript:/file: ще на парс-стадії — текст
    # лишається літерально. Наш guard — defense-in-depth для майбутніх схем.
    out = "".join(tg_markdown.render_html("[chat](tg://user?id=42) [m](mailto:a@b.c)"))
    assert '<a href="tg://user?id=42">chat</a>' in out
    assert '<a href="mailto:a@b.c">m</a>' in out
