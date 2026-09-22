"""Process-wide CodexPrefsService singleton."""

from app.services.codex_prefs.catalog import CodexModelCatalog
from app.services.codex_prefs.service import CodexPrefsService

codex_prefs_service = CodexPrefsService(CodexModelCatalog())
