"""Issue control commands to the host Docker daemon via /var/run/docker.sock.

The socket is bind-mounted from the host into this container; we talk to
the daemon's HTTP API directly with httpx + UDS transport (no docker CLI
binary needed inside the image).
"""

import httpx
import structlog

log = structlog.get_logger(__name__)

_DOCKER_SOCK = "/var/run/docker.sock"
_BASE_URL = "http://localhost"


class DockerControlService:
    async def restart_container(self, name: str, *, timeout_s: float = 30.0) -> bool:
        transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCK)
        try:
            async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
                resp = await client.post(
                    f"/containers/{name}/restart",
                    timeout=timeout_s,
                )
        except (httpx.HTTPError, OSError) as exc:
            log.error("docker_restart_failed", container=name, error=str(exc))
            return False
        if resp.status_code >= 400:
            log.error(
                "docker_restart_bad_status",
                container=name,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return False
        log.info("docker_restart_issued", container=name, status=resp.status_code)
        return True
