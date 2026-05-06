"""Process-wide UserService singleton."""

from app.services.users.service import UserService

user_service = UserService()
