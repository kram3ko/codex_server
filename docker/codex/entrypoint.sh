#!/bin/sh
# Codex CLI app-server: всі native tools увімкнено, workspace = /home/codex/workspace.
# Без MCP, без авторизації на WS (порт 4500 у docker-network, наружу не публікується).

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
EOF

exec codex app-server --listen ws://0.0.0.0:4500
