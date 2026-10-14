#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${G2_LEROBOT_PYTHON:-$REPO_ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/convert_g2_record.py" \
  --src-path "${G2_RECORD_SRC_PATH:-/data/collection/agibot_g2/white_carton_to_left_tan_carton_20261009/raw_records}" \
  --output-path "${G2_LEROBOT_OUTPUT:-/data/collection/agibot_g2/white_carton_to_left_tan_carton_20261009/lerobot_eva}" \
  --task "${G2_LEROBOT_TASK:-Pick up the white carton and place it inside the large tan carton on the left.}"
