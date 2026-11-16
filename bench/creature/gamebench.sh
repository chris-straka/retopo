#!/usr/bin/env bash
# Measure-only creature bend + surface + blind game-camera benchmark on f.
# Builds the current tree's `retopo` binary, evaluates `retopo` vs Blender
# `decimate` on the prepared creature workspace (`$GAMEBENCH_WORK`, default
# `~/rfwork/set`), builds the unmarked blind A/B sheets (`OUT/blind/`) with
# external answer key (`OUT/blind.key.json`), and stages top-level artifacts
# into `$FLEET_ARTIFACTS` when running under `fleet run`.
set -euo pipefail

if [ -f "$HOME/rfwork/env.sh" ]; then
  # shellcheck disable=SC1091
  source "$HOME/rfwork/env.sh"
fi

export WEIGHTS_NEW="${WEIGHTS_NEW:-$HOME/rfwork/bin/weights-new}"
for req in "$WEIGHTS_NEW" "${BLENDER:-}"; do
  if [ -z "$req" ] || [ ! -e "$req" ]; then
    echo "gamebench.sh: missing required tool '$req'" >&2
    exit 2
  fi
done

export PATH="$HOME/.cargo/bin:$PATH"
(cd rust && cargo build --locked --release -p retopo)
TARGET_DIR="${CARGO_TARGET_DIR:-$PWD/rust/target}"
export RETOPO="$TARGET_DIR/release/retopo"
echo "commit $(git log --oneline -1 | cut -c1-60) retopo=$RETOPO"

WORK="${GAMEBENCH_WORK:-$HOME/rfwork/set}"
OUT="${GAMEBENCH_OUT:-$HOME/rfwork/gamebench_clean}"
mkdir -p "$OUT"

export PYTHONUNBUFFERED=1
if command -v stdbuf >/dev/null 2>&1; then
  python3 bench/creature/gamebench.py "$WORK" --out "$OUT" "$@" 2>&1 | stdbuf -oL tee "$OUT/run.log"
else
  python3 bench/creature/gamebench.py "$WORK" --out "$OUT" "$@" 2>&1 | tee "$OUT/run.log"
fi
python3 bench/creature/blind.py build "$OUT" --seed "${GAMEBENCH_SEED:-20261115}"

if [ -n "${FLEET_ARTIFACTS:-}" ]; then
  mkdir -p "$FLEET_ARTIFACTS"
  cp "$OUT/report.md" "$OUT/run.log" "$OUT/blind.key.json" "$FLEET_ARTIFACTS/"
  [ -f "$OUT/summary_sheet.png" ] && cp "$OUT/summary_sheet.png" "$FLEET_ARTIFACTS/"
  cp "$OUT/blind/manifest.json" "$FLEET_ARTIFACTS/blind_manifest.json"
  for p in "$OUT"/blind/pair_*.png; do
    [ -e "$p" ] && cp "$p" "$FLEET_ARTIFACTS/"
  done
fi
