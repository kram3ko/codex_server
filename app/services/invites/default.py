"""Module-level singleton — uniform import pattern across services."""

from app.services.invites.service import InviteService

invite_service = InviteService()
