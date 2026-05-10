#!/bin/sh
# Codex CLI app-server: всі native tools увімкнено, workspace = /home/codex/workspace.
# MCP server `codex_app` — наш FastAPI на codex-server:8000/mcp/streamable з
# `show_image` тулою. Bearer береться з env MCP_CALLBACK_TOKEN, той самий що
# валідується settings.MCP_CALLBACK_TOKEN на стороні FastAPI.

set -eu

export RUST_LOG="${RUST_LOG:-warn,codex_app_server=info}"
export NO_COLOR="${NO_COLOR:-1}"

# Codex CLI's internal feedback SQLite blocks main thread на heavy turn'ах
# коли росте без bound'у. Symlink у tmpfs (size-capped) — sidecar's вбудована
# retention тримає її маленькою, INSERT'и instant.
mkdir -p /codex-feedback
rm -f "$HOME/.codex/logs_2.sqlite" "$HOME/.codex/logs_2.sqlite-wal" "$HOME/.codex/logs_2.sqlite-shm"
ln -sf /codex-feedback/logs_2.sqlite "$HOME/.codex/logs_2.sqlite"

cat > "$HOME/.codex/config.toml" <<'EOF'
# Container — наш sandbox boundary. Codex'у власний unshare-sandbox не
# потрібен (і блокувався б Docker seccomp/apparmor без зайвих cap'ів).
sandbox_mode = "danger-full-access"

# Усі native tools увімкнені — модель може вільно шукати в інтернеті,
# виконувати shell-команди в /home/codex/workspace, працювати з файлами.
[tools]
web_search = true
browser_use = true
computer_use = true
in_app_browser = true
image_generation = true
apps = true
shell_tool = true
unified_exec = true

# Workspace із trust_level=trusted — Codex не питає підтвердження
# для destructive команд у цій папці.
[projects."/home/codex/workspace"]
trust_level = "trusted"

# Streamable-HTTP MCP server у codex-server (FastAPI sub-app /mcp). Тули:
# show_image. Bearer auth — env_var вибирається Codex CLI runtime'ом, ми
# монтуємо його з .env через docker-compose.
[mcp_servers.codex_app]
url = "http://codex-server:8000/mcp/streamable"
bearer_token_env_var = "MCP_CALLBACK_TOKEN"
EOF

exec codex app-server --listen ws://0.0.0.0:4500
