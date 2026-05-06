from app.tg.turn import _compose_final_text


def test_compose_final_text_keeps_tool_image_when_base_has_text() -> None:
    base = "Сгенерировал картинку."
    tool_outputs = ["![image](/home/codex/.codex/generated_images/run/cat.png)"]

    assert _compose_final_text(base, "", tool_outputs) == (
        "Сгенерировал картинку.\n\n"
        "![image](/home/codex/.codex/generated_images/run/cat.png)"
    )


def test_compose_final_text_does_not_duplicate_existing_media() -> None:
    base = "![image](/home/codex/.codex/generated_images/run/cat.png)"
    tool_outputs = ["![image](/home/codex/.codex/generated_images/run/cat.png)"]

    assert _compose_final_text(base, "", tool_outputs) == base


def test_compose_final_text_uses_tool_output_for_empty_response() -> None:
    tool_outputs = ["![image](/home/codex/.codex/generated_images/run/cat.png)"]

    assert _compose_final_text("", "", tool_outputs) == tool_outputs[0]
