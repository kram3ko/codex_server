"""UserService — Me / UpdateMe."""

from typing import override

from connectrpc.request import RequestContext

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import user_pb2
from app.grpc_generated.codex.v1.user_connect import UserService as UserProtocol
from app.rpc._auth import require_user
from app.rpc._mappers import user_to_pb


class UserRPC(UserProtocol):
    @override
    async def me(self, request: user_pb2.MeRequest, ctx: RequestContext) -> user_pb2.User:
        del request
        user = await require_user(ctx)
        return user_to_pb(user)

    @override
    async def update_me(
        self,
        request: user_pb2.UpdateMeRequest,
        ctx: RequestContext,
    ) -> user_pb2.User:
        user = await require_user(ctx)
        new_name: str | None = request.display_name.strip() or None
        async with SessionLocal() as db:
            db_user = await db.merge(user)
            db_user.display_name = new_name
            await db.commit()
            await db.refresh(db_user)
            return user_to_pb(db_user)
