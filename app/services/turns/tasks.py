"""TaskIQ tasks для durable turn execution. Worker (`codex-worker`) запускає
`execute_turn(turn_id, ...)` після `kiq()`-enqueue з handler-а; at-least-once
redelivery + `finalize_once` CAS = exactly-once terminal."""

from app.services.turns.runner import execute_turn_inner
from app.worker import broker


@broker.task
async def execute_turn(
    turn_id: int,
    text: str,
    image_urls: list[str],
    voice_reply: bool,
    client_id: str | None,
) -> None:
    await execute_turn_inner(
        turn_id,
        text=text,
        image_urls=tuple(image_urls),
        voice_reply=voice_reply,
        client_id=client_id,
    )
