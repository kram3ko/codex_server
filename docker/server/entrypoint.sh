#!/usr/bin/env sh
set -eu

# PYTHON_GIL=1: asyncpg's C-extension hangs at connection-pool teardown under
# free-threaded Python 3.14. Force GIL on for migrations only — uvicorn
# inherits PYTHON_GIL=0 from the image ENV.
timeout 30 env PYTHON_GIL=1 alembic upgrade head
exec "$@"
