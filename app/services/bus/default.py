"""Process-wide EventBus singleton — sits on top of the shared Redis client."""

from app.services.bus.bus import EventBus
from app.services.cache.default import cache

event_bus = EventBus(cache)
