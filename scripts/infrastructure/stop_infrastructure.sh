#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PID_DIR="$ROOT_DIR/scripts/infrastructure/pids"

stop_service() {
    local name=$1
    local pid_file="$PID_DIR/${name}.pid"
    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
        fi
        rm -f "$pid_file"
    fi
}

stop_service "xmtp"
stop_service "ipfs"
stop_service "anvil"

echo "基础设施已停止"
