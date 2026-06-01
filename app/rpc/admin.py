"""AdminService — invites + manual user management. Admin-only RPCs.

Захист через `require_admin(ctx)` на кожен метод. Бізнес-логіка живе у
`invite_service` + `user_service`. Mapper helpers тут (локальні до сервісу).
"""

import asyncio
from typing import override

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import admin_pb2, common_pb2
from app.grpc_generated.codex.v1.admin_connect import AdminService as AdminProtocol
from app.models import Invite, User, UserRole
from app.rpc._auth import require_admin
from app.rpc._mappers import to_ts
from app.services.auth.default import auth_service
from app.services.invites.default import invite_service
from app.services.ssh_vault.default import git_token_service, ssh_vault_service
from app.services.ssh_vault.service import SshKeyMeta, SshVaultError
from app.services.ssh_vault.tokens import ApiTokenMeta
from app.services.users.default import user_service

_email_adapter = TypeAdapter(EmailStr)

_PB_TO_ROLE = {
    admin_pb2.USER_ROLE_USER: UserRole.USER,
    admin_pb2.USER_ROLE_ADMIN: UserRole.ADMIN,
}
_ROLE_TO_PB = {v: k for k, v in _PB_TO_ROLE.items()}


def _invite_to_pb(inv: Invite) -> admin_pb2.Invite:
    msg = admin_pb2.Invite(
        id=inv.id,
        token=inv.token,
        created_by_user_id=inv.created_by_user_id,
        expires_at=to_ts(inv.expires_at),
        created_at=to_ts(inv.created_at),
    )
    if inv.used_by_user_id is not None:
        msg.used_by_user_id = inv.used_by_user_id
    if inv.used_at is not None:
        msg.used_at.CopyFrom(to_ts(inv.used_at))
    return msg


def _ssh_key_to_pb(meta: SshKeyMeta) -> admin_pb2.SshKey:
    return admin_pb2.SshKey(
        name=meta.name,
        host=meta.host,
        user=meta.user,
        created_at=to_ts(meta.created_at),
    )


def _api_token_to_pb(meta: ApiTokenMeta) -> admin_pb2.ApiToken:
    return admin_pb2.ApiToken(
        name=meta.name,
        host=meta.host,
        user=meta.user,
        created_at=to_ts(meta.created_at),
    )


def _admin_user_to_pb(u: User) -> admin_pb2.AdminUser:
    msg = admin_pb2.AdminUser(
        id=u.id,
        role=_ROLE_TO_PB.get(u.role, admin_pb2.USER_ROLE_UNSPECIFIED),
        created_at=to_ts(u.created_at),
    )
    if u.email is not None:
        msg.email = u.email
    if u.tg_user_id is not None:
        msg.tg_user_id = u.tg_user_id
    if u.display_name is not None:
        msg.display_name = u.display_name
    return msg


class AdminRPC(AdminProtocol):
    @override
    async def create_invite(
        self,
        request: admin_pb2.CreateInviteRequest,
        ctx: RequestContext,
    ) -> admin_pb2.CreateInviteResponse:
        admin = await require_admin(ctx)
        async with SessionLocal() as db:
            invite = await invite_service.create(
                db,
                created_by_user_id=admin.id,
                ttl_days=request.ttl_days,
            )
            await db.commit()
            await db.refresh(invite)
        return admin_pb2.CreateInviteResponse(invite=_invite_to_pb(invite))

    @override
    async def list_invites(
        self,
        request: admin_pb2.ListInvitesRequest,
        ctx: RequestContext,
    ) -> admin_pb2.ListInvitesResponse:
        await require_admin(ctx)
        async with SessionLocal() as db:
            invites = (
                await invite_service.list_all(db)
                if request.include_used
                else await invite_service.list_active(db)
            )
        return admin_pb2.ListInvitesResponse(invites=[_invite_to_pb(i) for i in invites])

    @override
    async def revoke_invite(
        self,
        request: admin_pb2.RevokeInviteRequest,
        ctx: RequestContext,
    ) -> common_pb2.Empty:
        await require_admin(ctx)
        async with SessionLocal() as db:
            ok = await invite_service.revoke(db, request.invite_id)
            if not ok:
                raise ConnectError(Code.NOT_FOUND, "invite not found or already used")
            await db.commit()
        return common_pb2.Empty()

    @override
    async def create_user(
        self,
        request: admin_pb2.CreateUserRequest,
        ctx: RequestContext,
    ) -> admin_pb2.AdminUser:
        await require_admin(ctx)
        try:
            email = _email_adapter.validate_python(request.email.strip().lower())
        except ValidationError as exc:
            raise ConnectError(Code.INVALID_ARGUMENT, "valid email required") from exc
        if len(request.password) < 8:
            raise ConnectError(Code.INVALID_ARGUMENT, "password must be ≥ 8 chars")
        role = _PB_TO_ROLE.get(request.role, UserRole.USER)
        display_name = request.display_name.strip() or None

        async with SessionLocal() as db:
            existing = await user_service.get_by_email(db, email)
            if existing is not None and existing.password_hash:
                raise ConnectError(Code.ALREADY_EXISTS, "email already registered")
            user = existing or await user_service.get_or_create_by_email(
                db, email, display_name=display_name
            )
            user.role = role
            if display_name and not user.display_name:
                user.display_name = display_name
            await user_service.set_password_hash(
                db, user, await auth_service.hash_password(request.password)
            )
            await db.commit()
            await db.refresh(user)
        return _admin_user_to_pb(user)

    @override
    async def list_users(
        self,
        request: admin_pb2.ListUsersRequest,
        ctx: RequestContext,
    ) -> admin_pb2.ListUsersResponse:
        del request
        await require_admin(ctx)
        async with SessionLocal() as db:
            users = await user_service.list_all(db)
        return admin_pb2.ListUsersResponse(users=[_admin_user_to_pb(u) for u in users])

    @override
    async def add_ssh_key(
        self,
        request: admin_pb2.AddSshKeyRequest,
        ctx: RequestContext,
    ) -> admin_pb2.SshKey:
        await require_admin(ctx)
        try:
            meta = await asyncio.to_thread(
                ssh_vault_service.add,
                name=request.name,
                host=request.host,
                user=request.user,
                private_key=request.private_key,
            )
        except SshVaultError as exc:
            raise ConnectError(Code.INVALID_ARGUMENT, str(exc)) from exc
        return _ssh_key_to_pb(meta)

    @override
    async def list_ssh_keys(
        self,
        request: admin_pb2.ListSshKeysRequest,
        ctx: RequestContext,
    ) -> admin_pb2.ListSshKeysResponse:
        del request
        await require_admin(ctx)
        keys = await asyncio.to_thread(ssh_vault_service.list)
        return admin_pb2.ListSshKeysResponse(keys=[_ssh_key_to_pb(k) for k in keys])

    @override
    async def delete_ssh_key(
        self,
        request: admin_pb2.DeleteSshKeyRequest,
        ctx: RequestContext,
    ) -> common_pb2.Empty:
        await require_admin(ctx)
        try:
            await asyncio.to_thread(ssh_vault_service.delete, request.name)
        except SshVaultError as exc:
            raise ConnectError(Code.NOT_FOUND, str(exc)) from exc
        return common_pb2.Empty()

    @override
    async def add_api_token(
        self,
        request: admin_pb2.AddApiTokenRequest,
        ctx: RequestContext,
    ) -> admin_pb2.ApiToken:
        await require_admin(ctx)
        try:
            meta = await asyncio.to_thread(
                git_token_service.add,
                name=request.name,
                host=request.host,
                user=request.user,
                token=request.token,
            )
        except SshVaultError as exc:
            raise ConnectError(Code.INVALID_ARGUMENT, str(exc)) from exc
        return _api_token_to_pb(meta)

    @override
    async def list_api_tokens(
        self,
        request: admin_pb2.ListApiTokensRequest,
        ctx: RequestContext,
    ) -> admin_pb2.ListApiTokensResponse:
        del request
        await require_admin(ctx)
        tokens = await asyncio.to_thread(git_token_service.list)
        return admin_pb2.ListApiTokensResponse(tokens=[_api_token_to_pb(t) for t in tokens])

    @override
    async def delete_api_token(
        self,
        request: admin_pb2.DeleteApiTokenRequest,
        ctx: RequestContext,
    ) -> common_pb2.Empty:
        await require_admin(ctx)
        try:
            await asyncio.to_thread(git_token_service.delete, request.name)
        except SshVaultError as exc:
            raise ConnectError(Code.NOT_FOUND, str(exc)) from exc
        return common_pb2.Empty()
