#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
OUT="$ROOT/app/grpc_generated"

mkdir -p "$OUT"

python -m grpc_tools.protoc \
  -I "$ROOT/protos" \
  --python_out="$OUT" \
  --pyi_out="$OUT" \
  --connectrpc_out="$OUT" \
  "$ROOT"/protos/codex/v1/*.proto
