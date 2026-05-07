from app.tg.turn import _strip_committed_prefix


def test_strip_committed_prefix_removes_streamed_chunk() -> None:
    text = "Привіт. Це мій повний відповідь."
    committed = "Привіт. "

    assert _strip_committed_prefix(text, committed) == "Це мій повний відповідь."


def test_strip_committed_prefix_returns_full_text_when_prefix_diverges() -> None:
    """Якщо модель переписала ранні токени — не глушимо final, шлемо повністю."""
    text = "Інакший фінал від моделі"
    committed = "Привіт, я ще не закінчив"

    assert _strip_committed_prefix(text, committed) == text


def test_strip_committed_prefix_returns_empty_when_text_equals_committed() -> None:
    text = "повністю заstreaml-ений вже"

    assert _strip_committed_prefix(text, text) == ""
