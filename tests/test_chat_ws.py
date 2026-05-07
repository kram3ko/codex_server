from app.ws.chat import _final_text_for_done_frame


def test_done_frame_omits_final_text_when_it_matches_streamed_text() -> None:
    """Frontend уже зібрав full text з token-deltas — повертаємо "" для dedup."""
    assert _final_text_for_done_frame("готово", "готово") == ""


def test_done_frame_keeps_final_text_when_it_differs_from_streamed_text() -> None:
    """Sidecar emit'ить agentMessage одним item'ом → final != streamed → повний текст."""
    assert _final_text_for_done_frame("готово додатковий рядок", "готово") == (
        "готово додатковий рядок"
    )


def test_done_frame_keeps_final_text_when_streamed_was_empty() -> None:
    """Без token-стрімінгу (deltas не приходили) frontend нічого не знає — шлемо все."""
    assert _final_text_for_done_frame("повний результат", "") == "повний результат"
