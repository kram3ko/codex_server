"""Реекспорт усіх моделей, щоб Alembic міг знайти їх через Base.metadata."""

from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.note import Note
from app.models.upload import Upload

__all__ = ["Conversation", "Message", "MessageRole", "Note", "Upload"]
