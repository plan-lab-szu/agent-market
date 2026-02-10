#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON=${PYTHON:-"python3"}

RUN_ID=${RUN_ID:-"fig3b-$(date -u +%Y%m%d-%H%M%S)"}
OUT_DIR=${OUT_DIR:-"$ROOT_DIR/outputs/fig3b_runs/$RUN_ID"}
FIG3_CONFIG=${FIG3_CONFIG:-"$ROOT_DIR/configs/fig3.json"}
SAMPLES=${SAMPLES:-"100"}

if [[ -z "${XMTP_PEER:-}" ]]; then
  echo "XMTP_PEER is required" >&2
  exit 1
fi
if [[ -z "${COMFYUI_URL:-}" || -z "${COMFYUI_WORKFLOW:-}" ]]; then
  echo "COMFYUI_URL and COMFYUI_WORKFLOW are required" >&2
  exit 1
fi
if [[ -z "${IPFS_API:-}" ]]; then
  IPFS_API="http://127.0.0.1:5001"
fi

if [[ -z "${XMTP_PRIVATE_KEY:-}" ]]; then
  XMTP_PRIVATE_KEY=$($PYTHON - <<'PY'
import secrets
print("0x" + secrets.token_hex(32))
PY
)
fi

XMTP_DB_PATH=${XMTP_DB_PATH:-"$OUT_DIR/xmtp-bridge.db3"}
XMTP_BRIDGE_CMD=${XMTP_BRIDGE_CMD:-"node scripts/xmtp_cli/xmtp_bridge.js"}

export XMTP_PEER
export XMTP_PRIVATE_KEY
export XMTP_DB_PATH
export XMTP_BRIDGE_CMD

mkdir -p "$OUT_DIR/raw_data" "$OUT_DIR/plots"

$PYTHON "$ROOT_DIR/scripts/collect_latency.py" \
  --config "$FIG3_CONFIG" \
  --samples "$SAMPLES" \
  --workload all \
  --ipfs-mode real \
  --ipfs-api "$IPFS_API" \
  --comfyui-url "$COMFYUI_URL" \
  --comfyui-workflow "$COMFYUI_WORKFLOW" \
  --experiment-id "$RUN_ID" \
  --out "$OUT_DIR/raw_data/latency.csv" \
  --summary "$OUT_DIR/raw_data/latency_summary.csv"

$PYTHON "$ROOT_DIR/scripts/latency.py" \
  --input "$OUT_DIR/raw_data/latency.csv" \
  --summary "$OUT_DIR/raw_data/latency_summary.csv" \
  --png-output "$OUT_DIR/plots/fig3b.png" \
  --tikz-output "$OUT_DIR/plots/fig3b.tex"

echo "Done: $OUT_DIR"
