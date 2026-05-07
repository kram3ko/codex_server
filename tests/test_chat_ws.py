from app.ws.chat import _final_text_for_done_frame


def test_done_frame_omits_final_text_when_it_matches_streamed_text() -> None:
    assert _final_text_for_done_frame("готово", "готово") == ""


def test_done_frame_keeps_final_text_when_it_differs_from_streamed_text() -> None:
    assert _final_text_for_done_frame("готово\n\n![image](/tmp/x.png)", "готово") == (
        "готово\n\n![image](/tmp/x.png)"
    )
