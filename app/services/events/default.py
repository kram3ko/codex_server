"""Process-wide EventService singleton."""

from app.services.events.service import EventService

event_service = EventService()
