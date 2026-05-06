"""Реекспорт DB session dependency (щоб імпорт був з app.deps.db)."""

from app.db.deps import get_session

__all__ = ["get_session"]
