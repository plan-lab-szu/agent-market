#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON=${PYTHON:-"python3"}

RUN_ID=${RUN_ID:-"fig3c-$(date -u +%Y%m%d-%H%M%S)"}
OUT_DIR=${OUT_DIR:-"$ROOT_DIR/outputs/fig3c_runs/$RUN_ID"}
FIG3_CONFIG=${FIG3_CONFIG:-"$ROOT_DIR/configs/fig3.json"}

if [[ -z "${XMTP_PEER:-}" ]]; then
  echo "XMTP_PEER is required" >&2
  exit 1
fi

if [[ -z "${XMTP_PRIVATE_KEY:-}" ]]; then
  XMTP_PRIVATE_KEY=$($PYTHON - <<'PY'
import secrets
print("0x" + secrets.token_hex(32))
PY
)
fi
XMTP_DB_PATH=${XMTP_DB_PATH:-"null"}
XMTP_BRIDGE_CMD=${XMTP_BRIDGE_CMD:-"node scripts/xmtp_cli/xmtp_bridge.js"}
export XMTP_PEER
export XMTP_PRIVATE_KEY
export XMTP_DB_PATH
export XMTP_BRIDGE_CMD

mkdir -p "$OUT_DIR/raw_data" "$OUT_DIR/plots"

$PYTHON "$ROOT_DIR/scripts/collect_tps.py" \
  --config "$FIG3_CONFIG" \
  --experiment-id "$RUN_ID" \
  --out "$OUT_DIR/raw_data/tps.csv" \
  --summary "$OUT_DIR/raw_data/tps_summary.csv"

$PYTHON "$ROOT_DIR/scripts/throughput.py" \
  --input "$OUT_DIR/raw_data/tps.csv" \
  --summary "$OUT_DIR/raw_data/tps_summary.csv" \
  --png-output "$OUT_DIR/plots/fig3c.png" \
  --tikz-output "$OUT_DIR/plots/fig3c.tex"

echo "Done: $OUT_DIR"
