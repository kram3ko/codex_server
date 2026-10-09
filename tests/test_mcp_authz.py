"""mcp_authz — per-thread identity JWT: issue/verify, thread config, header read."""

import time

import jwt
import pytest
from fastmcp.exceptions import ToolError

from app.mcp import authz as mcp_authz_dep
from app.mcp.tools import errors as errors_tool
from app.services.codex.sidecar import SidecarName
from app.services.mcp_authz.schemas import McpAuthzError, McpAuthzRole
from app.services.mcp_authz.service import AUTHZ_HEADER, MCP_SERVER_NAME, McpAuthzService

SECRET = "x" * 48


@pytest.fixture
def service() -> McpAuthzService:
    return McpAuthzService(SECRET)


def test_issue_verify_roundtrip_guest(service: McpAuthzService) -> None:
    claims = service.verify(service.issue(user_id=7, chat_id=42, sidecar=SidecarName.GUEST))
    assert (claims.user_id, claims.chat_id) == (7, 42)
    assert claims.role is McpAuthzRole.USER
    assert claims.sidecar is SidecarName.GUEST
    assert claims.iat <= int(time.time())


def test_admin_sidecar_grants_admin_role(service: McpAuthzService) -> None:
    claims = service.verify(service.issue(user_id=1, chat_id=1, sidecar=SidecarName.ADMIN))
    assert claims.role is McpAuthzRole.ADMIN


def test_token_has_no_expiry(service: McpAuthzService) -> None:
    """Заголовок фіксується на весь час життя завантаженого thread — expiry
    ламав би тули посеред довгого thread."""
    payload = jwt.decode(
        service.issue(user_id=1, chat_id=1, sidecar=SidecarName.GUEST),
        SECRET,
        algorithms=["HS256"],
        issuer="codex-server",
    )
    assert "exp" not in payload


def test_verify_rejects_bad_signature(service: McpAuthzService) -> None:
    token = service.issue(user_id=1, chat_id=1, sidecar=SidecarName.GUEST)
    with pytest.raises(McpAuthzError):
        service.verify(token[:-2] + ("AA" if token[-2:] != "AA" else "BB"))


def test_verify_rejects_wrong_issuer(service: McpAuthzService) -> None:
    payload = {
        "iss": "evil",
        "user_id": 1,
        "chat_id": 1,
        "role": "user",
        "sidecar": "guest",
        "iat": int(time.time()),
    }
    with pytest.raises(McpAuthzError):
        service.verify(jwt.encode(payload, SECRET, algorithm="HS256"))


def test_verify_rejects_injected_claims(service: McpAuthzService) -> None:
    payload = {
        "iss": "codex-server",
        "user_id": 1,
        "chat_id": 1,
        "role": "user",
        "sidecar": "guest",
        "iat": int(time.time()),
        "admin": True,
    }
    with pytest.raises(McpAuthzError, match="malformed_claims"):
        service.verify(jwt.encode(payload, SECRET, algorithm="HS256"))


def test_thread_config_uses_dotted_key_and_header(service: McpAuthzService) -> None:
    config = service.thread_config("tok")
    assert config == {f"mcp_servers.{MCP_SERVER_NAME}": {"http_headers": {AUTHZ_HEADER: "tok"}}}


def test_require_claims_reads_header(monkeypatch: pytest.MonkeyPatch) -> None:
    service = McpAuthzService(SECRET)
    token = service.issue(user_id=5, chat_id=9, sidecar=SidecarName.GUEST)
    monkeypatch.setattr(mcp_authz_dep, "mcp_authz_service", service)
    monkeypatch.setattr(
        mcp_authz_dep, "get_http_headers", lambda **_: {AUTHZ_HEADER.lower(): token}
    )
    claims = mcp_authz_dep.require_claims()
    assert (claims.user_id, claims.chat_id) == (5, 9)


def test_require_claims_without_header_is_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_authz_dep, "get_http_headers", lambda **_: {})
    with pytest.raises(ToolError, match=AUTHZ_HEADER):
        mcp_authz_dep.require_claims()


def test_error_tools_reject_guest(monkeypatch: pytest.MonkeyPatch) -> None:
    service = McpAuthzService(SECRET)
    token = service.issue(user_id=5, chat_id=9, sidecar=SidecarName.GUEST)
    monkeypatch.setattr(mcp_authz_dep, "mcp_authz_service", service)
    monkeypatch.setattr(
        mcp_authz_dep, "get_http_headers", lambda **_: {AUTHZ_HEADER.lower(): token}
    )
    with pytest.raises(ToolError, match="administrator"):
        errors_tool._require_admin()
