"""Process-wide NoteService singleton."""

from app.services.notes.service import NoteService

note_service = NoteService()
