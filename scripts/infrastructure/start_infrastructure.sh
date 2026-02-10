#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PID_DIR="$ROOT_DIR/scripts/infrastructure/pids"
ENV_FILE="$ROOT_DIR/scripts/infrastructure/env.json"

mkdir -p "$PID_DIR"

ANVIL_CMD=${ANVIL_CMD:-"anvil --host 127.0.0.1 --port 8545 --block-time 1"}
IPFS_CMD=${IPFS_CMD:-"ipfs daemon"}
XMTP_CMD=${XMTP_CMD:-"xmtp-node"}
SKIP_XMTP=${SKIP_XMTP:-""}

start_service() {
    local name=$1
    local cmd=$2
    local pid_file="$PID_DIR/${name}.pid"

    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        return
    fi

    nohup bash -c "$cmd" >"$PID_DIR/${name}.log" 2>&1 &
    echo $! >"$pid_file"
}

if ! command -v anvil >/dev/null 2>&1; then
    echo "未找到 anvil，请先安装 Foundry" >&2
    exit 1
fi

if ! command -v ipfs >/dev/null 2>&1; then
    echo "未找到 ipfs，请先安装 IPFS Kubo" >&2
    exit 1
fi

if ! command -v ${XMTP_CMD%% *} >/dev/null 2>&1; then
    if [[ -z "$SKIP_XMTP" ]]; then
        echo "未找到 XMTP 节点命令 (${XMTP_CMD%% *})，将跳过 XMTP。可设置 SKIP_XMTP= 或 REQUIRE_XMTP=1 控制行为。" >&2
        SKIP_XMTP="1"
    fi
fi

start_service "anvil" "$ANVIL_CMD"
start_service "ipfs" "$IPFS_CMD"
if [[ -z "$SKIP_XMTP" ]]; then
    start_service "xmtp" "$XMTP_CMD"
else
    echo "跳过 XMTP 节点启动"
fi

echo "基础设施已启动。配置文件: $ENV_FILE"
