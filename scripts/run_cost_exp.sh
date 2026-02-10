#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON=${PYTHON:-"python3"}

RUN_ID=${RUN_ID:-"fig3a-$(date -u +%Y%m%d-%H%M%S)"}
OUT_DIR=${OUT_DIR:-"$ROOT_DIR/outputs/fig3a_runs/$RUN_ID"}
FIG3_CONFIG=${FIG3_CONFIG:-"$ROOT_DIR/configs/fig3.json"}

mkdir -p "$OUT_DIR/raw_data" "$OUT_DIR/plots"

$PYTHON "$ROOT_DIR/scripts/collect_costs.py" \
  --config "$FIG3_CONFIG" \
  --experiment-id "$RUN_ID" \
  --out "$OUT_DIR/raw_data/costs.csv"

$PYTHON "$ROOT_DIR/scripts/cost.py" \
  --input "$OUT_DIR/raw_data/costs.csv" \
  --summary "$OUT_DIR/raw_data/costs_summary.csv" \
  --output "$OUT_DIR/plots/fig3a.tex" \
  --png-output "$OUT_DIR/plots/fig3a.png"

echo "Done: $OUT_DIR"
