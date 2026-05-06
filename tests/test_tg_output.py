from app.tg.output import PhotoChunk, TextChunk, parse_final_text


def test_parse_final_text_treats_markdown_image_as_photo() -> None:
    chunks = parse_final_text("готово\n\n![кот](/tmp/cat.png)")

    assert chunks == [
        TextChunk(text="готово\n\n"),
        PhotoChunk(src="/tmp/cat.png", caption="кот"),
    ]


def test_parse_final_text_treats_image_link_as_photo() -> None:
    chunks = parse_final_text("[cat.png](/tmp/cat.png)")

    assert chunks == [PhotoChunk(src="/tmp/cat.png", caption="cat.png")]
