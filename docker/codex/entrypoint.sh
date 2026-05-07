#!/bin/sh
# Codex CLI app-server: всі native tools увімкнено, workspace = /home/codex/workspace.
# MCP server `codex_app` — наш FastAPI на codex-server:8000/mcp/streamable з
# `show_image` тулою. Bearer береться з env MCP_CALLBACK_TOKEN, той самий що
# валідується settings.MCP_CALLBACK_TOKEN на стороні FastAPI.

set -eu

export RUST_LOG="${RUST_LOG:-warn,codex_app_server=info}"
export NO_COLOR="${NO_COLOR:-1}"

cat > "$HOME/.codex/config.toml" <<'EOF'
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
