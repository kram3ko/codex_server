"""Process-wide MessageService singleton."""

from app.services.messages.service import MessageService

message_service = MessageService()
