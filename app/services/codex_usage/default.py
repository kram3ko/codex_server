"""Process-wide CodexUsageService singleton."""

from app.services.codex_usage.service import CodexUsageService

codex_usage_service = CodexUsageService()
