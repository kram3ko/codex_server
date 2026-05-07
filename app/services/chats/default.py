"""Process-wide ChatService singleton."""

from app.services.chats.service import ChatService

chat_service = ChatService()
