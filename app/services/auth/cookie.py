"""HttpOnly JWT cookie — set / clear / read.

Per CLAUDE.md §5: JWT доставляється у `HttpOnly; SameSite=Lax; Secure (prod)`
cookie, **не** у localStorage. XSS-сценарій не може ні прочитати, ні
експортувати токен через `document.cookie`.

Reader: `read_jwt(headers)` парсить `Cookie:` header (валідація — пізніше
у `AuthService.validate_token`).
Writer: `build_set_cookie(token, ttl_s)` повертає Set-Cookie value;
        `build_clear_cookie()` — те саме але `Max-Age=0` для logout.
"""

from http.cookies import SimpleCookie

from app.config import settings

JWT_COOKIE_NAME = "codex_jwt"


def build_set_cookie(token: str, ttl_seconds: int) -> str:
    """Set-Cookie value для `codex_jwt`. HttpOnly + SameSite=Lax обов'язково,
    Secure — залежно від `settings.COOKIES_SECURE` (True у prod)."""
    attrs = [
        f"{JWT_COOKIE_NAME}={token}",
        "HttpOnly",
        "SameSite=Lax",
        "Path=/api",
        f"Max-Age={ttl_seconds}",
    ]
    if settings.COOKIES_SECURE:
        attrs.append("Secure")
    return "; ".join(attrs)


def build_clear_cookie() -> str:
    """Set-Cookie зі скиданням токена (logout / invalid session)."""
    attrs = [
        f"{JWT_COOKIE_NAME}=",
        "HttpOnly",
        "SameSite=Lax",
        "Path=/api",
        "Max-Age=0",
    ]
    if settings.COOKIES_SECURE:
        attrs.append("Secure")
    return "; ".join(attrs)


def read_jwt(cookie_header: str | None) -> str | None:
    """Парсить значення `codex_jwt` з `Cookie:` header. None якщо нема."""
    if not cookie_header:
        return None
    jar: SimpleCookie = SimpleCookie()
    jar.load(cookie_header)
    morsel = jar.get(JWT_COOKIE_NAME)
    return morsel.value if morsel else None
