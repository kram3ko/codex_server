"""Process-wide singletons для `TurnService` і `TurnStream`."""

from app.services.turns.service import TurnService
from app.services.turns.stream import TurnStream

turn_service = TurnService()
turn_stream = TurnStream()
