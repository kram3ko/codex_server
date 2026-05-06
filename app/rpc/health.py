"""Connect handler для `codex.v1.HealthService.Check` — gRPC-стиль health probe."""

from connectrpc.request import RequestContext

from app.grpc_generated.codex.v1 import common_pb2


class HealthRPC:
    """Implements connectrpc-generated `HealthService` Protocol."""

    async def check(
        self,
        _request: common_pb2.HealthCheckRequest,
        _ctx: RequestContext,
    ) -> common_pb2.HealthCheckResponse:
        return common_pb2.HealthCheckResponse(
            status=common_pb2.HealthCheckResponse.SERVING
        )
