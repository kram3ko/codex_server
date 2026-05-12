"""TG turn pipeline.

- runner.TurnRunner        — orchestration entry для bot-handler'а
- stream.stream_turn       — codex event loop
- outcomes                 — done/empty/dropped + TG send
- persistence              — user/assistant message + journal events
- control                  — cancel, auto-reset thread, steer, emit_failure
"""

from app.tg.turn.control import cancel_turn
from app.tg.turn.runner import TurnRunner

__all__ = ["TurnRunner", "cancel_turn"]
