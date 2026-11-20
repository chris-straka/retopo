#!/usr/bin/env bash
# Portable measure-only creature bend + surface + blind game-camera benchmark.
# Required environment variables:
#   GAMEBENCH_WORK  prepared creature-bench workspace (contains subjects.tsv)
#   GAMEBENCH_OUT   output directory for results, verdict.json, report.md, blind/
# Optional environment variables (auto-discovered from PATH / sibling builds if unset):
#   BLENDER         Blender executable
#   WEIGHTS         meshweights `weights` CLI (or WEIGHTS_NEW)
#   RETOPO          retopo CLI (built from ./rust when --tools includes retopo)
#   GAMEBENCH_TOOLS challenger,baseline pair (default: retopo,decimate)
#   GAMEBENCH_SEED  blind shuffle seed (default: 20261115)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

WORK="${GAMEBENCH_WORK:-}"
OUT="${GAMEBENCH_OUT:-}"
if [ -z "$WORK" ] || [ ! -d "$WORK" ]; then
  echo "gamebench.sh: set GAMEBENCH_WORK to an existing creature-bench workspace" >&2
  exit 2
fi
if [ -z "$OUT" ]; then
  echo "gamebench.sh: set GAMEBENCH_OUT to an output directory" >&2
  exit 2
fi

if [ -z "${BLENDER:-}" ]; then
  if [ -x "/Applications/Blender.app/Contents/MacOS/Blender" ]; then
    BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
  elif command -v blender >/dev/null 2>&1; then
    BLENDER="$(command -v blender)"
  fi
fi
export BLENDER="${BLENDER:-}"
if [ -z "$BLENDER" ] || [ ! -x "$BLENDER" ]; then
  echo "gamebench.sh: missing BLENDER executable (set BLENDER or put blender on PATH)" >&2
  exit 2
fi

if [ -z "${WEIGHTS:-}" ] && [ -n "${WEIGHTS_NEW:-}" ]; then
  WEIGHTS="$WEIGHTS_NEW"
fi
if [ -z "${WEIGHTS:-}" ]; then
  for cand in \
    "$REPO_ROOT/../meshweights/target/release/weights" \
    "$REPO_ROOT/../meshweights/bin/weights" \
    "$(command -v weights 2>/dev/null || true)"; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then
      WEIGHTS="$cand"
      break
    fi
  done
fi
export WEIGHTS="${WEIGHTS:-}"
if [ -z "$WEIGHTS" ] || [ ! -x "$WEIGHTS" ]; then
  echo "gamebench.sh: missing meshweights CLI (set WEIGHTS or build ../meshweights)" >&2
  exit 2
fi

TOOLS="${GAMEBENCH_TOOLS:-retopo,decimate}"
for ((i = 1; i <= $#; i++)); do
  arg="${!i}"
  if [ "$arg" = "--tools" ] && [ $((i + 1)) -le $# ]; then
    next=$((i + 1))
    TOOLS="${!next}"
  elif [[ "$arg" == --tools=* ]]; then
    TOOLS="${arg#--tools=}"
  fi
done

export PATH="$HOME/.cargo/bin:$PATH"
if [[ ",$TOOLS," == *",retopo,"* ]]; then
  if [ -z "${RETOPO:-}" ] || [ ! -x "${RETOPO:-}" ]; then
    (cd rust && cargo build --locked --release -p retopo)
    TARGET_DIR="${CARGO_TARGET_DIR:-$PWD/rust/target}"
    export RETOPO="$TARGET_DIR/release/retopo"
  fi
  if [ ! -x "$RETOPO" ]; then
    echo "gamebench.sh: missing RETOPO executable at '$RETOPO'" >&2
    exit 2
  fi
fi

echo "commit $(git log --oneline -1 | cut -c1-60) tools=$TOOLS"
mkdir -p "$OUT"

export PYTHONUNBUFFERED=1
extra_tools=()
has_tools_arg=0
for arg in "$@"; do
  if [ "$arg" = "--tools" ] || [[ "$arg" == --tools=* ]]; then
    has_tools_arg=1
  fi
done
if [ "$has_tools_arg" -eq 0 ]; then
  extra_tools=(--tools "$TOOLS")
fi

if command -v stdbuf >/dev/null 2>&1; then
  python3 bench/creature/gamebench.py "$WORK" --out "$OUT" "${extra_tools[@]}" "$@" 2>&1 | stdbuf -oL tee "$OUT/run.log"
else
  python3 bench/creature/gamebench.py "$WORK" --out "$OUT" "${extra_tools[@]}" "$@" 2>&1 | tee "$OUT/run.log"
fi
python3 bench/creature/blind.py build "$OUT" --seed "${GAMEBENCH_SEED:-20261115}"

if [ -n "${FLEET_ARTIFACTS:-}" ]; then
  mkdir -p "$FLEET_ARTIFACTS"
  cp "$OUT/report.md" "$OUT/verdict.json" "$OUT/run.log" "$OUT/blind.key.json" "$FLEET_ARTIFACTS/"
  [ -f "$OUT/summary_sheet.png" ] && cp "$OUT/summary_sheet.png" "$FLEET_ARTIFACTS/"
  cp "$OUT/blind/manifest.json" "$FLEET_ARTIFACTS/blind_manifest.json"
  for p in "$OUT"/blind/pair_*.png; do
    [ -e "$p" ] && cp "$p" "$FLEET_ARTIFACTS/"
  done
fi
