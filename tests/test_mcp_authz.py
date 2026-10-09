"""mcp_authz — per-thread identity JWT: issue/verify, DB-resolved role, thread config."""

import time
from types import SimpleNamespace

import jwt
import pytest
from fastmcp.exceptions import ToolError

from app.mcp import authz as mcp_authz_dep
from app.mcp.tools import errors as errors_tool
from app.models import UserRole
from app.services.codex.sidecar import SidecarName
from app.services.mcp_authz.schemas import McpAuthzError, McpAuthzRole
from app.services.mcp_authz.service import AUTHZ_HEADER, MCP_SERVER_NAME, McpAuthzService

SECRET = "x" * 48


class FakeUsers:
    def __init__(self, users: dict[int, UserRole]) -> None:
        self._users = users

    async def get(self, session: object, user_id: int) -> SimpleNamespace | None:
        role = self._users.get(user_id)
        return None if role is None else SimpleNamespace(id=user_id, role=role)


class FakeSessionLocal:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> None:
        return None


def make_service(users: dict[int, UserRole] | None = None) -> McpAuthzService:
    return McpAuthzService(SECRET, FakeUsers(users or {}))


@pytest.fixture
def service() -> McpAuthzService:
    return make_service({1: UserRole.ADMIN, 7: UserRole.USER})


def test_issue_verify_roundtrip(service: McpAuthzService) -> None:
    claims = service.verify(service.issue(user_id=7, chat_id=42, sidecar=SidecarName.GUEST))
    assert (claims.user_id, claims.chat_id) == (7, 42)
    assert claims.sidecar is SidecarName.GUEST
    assert claims.iat <= int(time.time())


def test_token_carries_no_role(service: McpAuthzService) -> None:
    payload = jwt.decode(
        service.issue(user_id=1, chat_id=1, sidecar=SidecarName.ADMIN),
        SECRET,
        algorithms=["HS256"],
        issuer="codex-server",
    )
    assert "role" not in payload
    assert "exp" not in payload


def test_verify_rejects_bad_signature(service: McpAuthzService) -> None:
    token = service.issue(user_id=1, chat_id=1, sidecar=SidecarName.GUEST)
    with pytest.raises(McpAuthzError):
        service.verify(token[:-2] + ("AA" if token[-2:] != "AA" else "BB"))


def test_verify_rejects_wrong_issuer(service: McpAuthzService) -> None:
    payload = {"iss": "evil", "user_id": 1, "chat_id": 1, "sidecar": "guest", "iat": 0}
    with pytest.raises(McpAuthzError):
        service.verify(jwt.encode(payload, SECRET, algorithm="HS256"))


def test_verify_rejects_injected_claims(service: McpAuthzService) -> None:
    payload = {
        "iss": "codex-server",
        "user_id": 1,
        "chat_id": 1,
        "sidecar": "guest",
        "iat": 0,
        "role": "admin",
    }
    with pytest.raises(McpAuthzError, match="malformed_claims"):
        service.verify(jwt.encode(payload, SECRET, algorithm="HS256"))


async def test_resolve_admin_requires_db_role_and_admin_sidecar(
    service: McpAuthzService,
) -> None:
    claims = service.verify(service.issue(user_id=1, chat_id=3, sidecar=SidecarName.ADMIN))
    identity = await service.resolve(object(), claims)
    assert identity.role is McpAuthzRole.ADMIN
    assert (identity.user_id, identity.chat_id) == (1, 3)


async def test_resolve_guest_sidecar_never_admin(service: McpAuthzService) -> None:
    claims = service.verify(service.issue(user_id=1, chat_id=3, sidecar=SidecarName.GUEST))
    identity = await service.resolve(object(), claims)
    assert identity.role is McpAuthzRole.USER


async def test_resolve_demoted_user_loses_admin() -> None:
    users = {1: UserRole.ADMIN}
    service = McpAuthzService(SECRET, FakeUsers(users))
    claims = service.verify(service.issue(user_id=1, chat_id=3, sidecar=SidecarName.ADMIN))
    users[1] = UserRole.USER
    identity = await service.resolve(object(), claims)
    assert identity.role is McpAuthzRole.USER


async def test_resolve_deleted_user_rejected(service: McpAuthzService) -> None:
    claims = service.verify(service.issue(user_id=404, chat_id=3, sidecar=SidecarName.GUEST))
    with pytest.raises(McpAuthzError, match="unknown_user"):
        await service.resolve(object(), claims)


def test_thread_config_uses_dotted_key_and_header(service: McpAuthzService) -> None:
    config = service.thread_config("tok")
    assert config == {f"mcp_servers.{MCP_SERVER_NAME}": {"http_headers": {AUTHZ_HEADER: "tok"}}}


def _install(monkeypatch: pytest.MonkeyPatch, service: McpAuthzService, token: str) -> None:
    monkeypatch.setattr(mcp_authz_dep, "mcp_authz_service", service)
    monkeypatch.setattr(mcp_authz_dep, "SessionLocal", FakeSessionLocal)
    monkeypatch.setattr(
        mcp_authz_dep, "get_http_headers", lambda **_: {AUTHZ_HEADER.lower(): token}
    )


async def test_require_identity_reads_header(
    monkeypatch: pytest.MonkeyPatch, service: McpAuthzService
) -> None:
    _install(monkeypatch, service, service.issue(user_id=7, chat_id=9, sidecar=SidecarName.GUEST))
    identity = await mcp_authz_dep.require_identity()
    assert (identity.user_id, identity.chat_id, identity.role) == (7, 9, McpAuthzRole.USER)


def test_require_claims_without_header_is_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_authz_dep, "get_http_headers", lambda **_: {})
    with pytest.raises(ToolError, match=AUTHZ_HEADER):
        mcp_authz_dep.require_claims()


async def test_require_identity_unknown_user_is_tool_error(
    monkeypatch: pytest.MonkeyPatch, service: McpAuthzService
) -> None:
    _install(monkeypatch, service, service.issue(user_id=404, chat_id=9, sidecar=SidecarName.GUEST))
    with pytest.raises(ToolError, match="unknown_user"):
        await mcp_authz_dep.require_identity()


async def test_error_tools_reject_guest(
    monkeypatch: pytest.MonkeyPatch, service: McpAuthzService
) -> None:
    _install(monkeypatch, service, service.issue(user_id=7, chat_id=9, sidecar=SidecarName.GUEST))
    with pytest.raises(ToolError, match="administrator"):
        await errors_tool._require_admin()
