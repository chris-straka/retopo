#!/usr/bin/env bash
# Gate for retopo-3: 8-frame posed HLH game-camera silhouette + shading check.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
SLUG=retopo-3
FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
show() { sed 's/^/      /' | head -n 40; }
BASE=$(git log -1 --format=%H -- gates/$SLUG)
[ -n "$BASE" ] || { fail "cannot find the commit that added the gate"; BASE=HEAD; }

echo "== scope"
for p in gates bench/creature/test_gamebench.py bench/creature/test_posed_image_check.py rust; do
  if git diff --quiet "$BASE" -- "$p" && [ -z "$(git status --porcelain -- "$p")" ]; then
    pass "$p unchanged"
  else
    fail "$p changed since the gate was written"
  fi
done

other=$( (git diff --name-only "$BASE"; git status --porcelain | cut -c4-) | grep -v -E '^(bench/creature/(posed_image_check\.py|posed_image_metrics\.py|gamebench\.py|README\.md)|docs/.*)?$' | sort -u)
if [ -z "$other" ]; then pass "only posed_image_check/metrics, gamebench.py, and docs changed"; else fail "other files changed:"; echo "$other" | show; fi

echo "== stale host/folder paths"
hits=$(grep -rnE "rfwork|weightforge|/home/f" bench/creature/ --exclude='*.pyc' --exclude='test_*.py' --exclude-dir='__pycache__' || true)
if [ -z "$hits" ]; then pass "no stale paths in bench/creature/"; else fail "stale paths left:"; echo "$hits" | show; fi

if [ -z "${BLENDER:-}" ]; then
  if [ -x "/Applications/Blender.app/Contents/MacOS/Blender" ]; then
    export BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
  elif command -v blender >/dev/null 2>&1; then
    export BLENDER="$(command -v blender)"
  fi
fi

echo "== Python tests (retopo-1 + retopo-3)"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

python3 bench/creature/test_gamebench.py >"$T/gb.out" 2>&1
rc1=$?
n1=$(grep -E '^Ran [0-9]+ test' "$T/gb.out" | awk '{print $2}')
if [ "$rc1" -eq 0 ] && [ "${n1:-0}" -ge 13 ]; then
  pass "test_gamebench.py: $n1 tests OK"
else
  fail "test_gamebench.py failed (exit $rc1):"
  tail -30 "$T/gb.out" | show
fi

if [ -z "${BLENDER:-}" ] || [ ! -x "${BLENDER:-}" ]; then
  fail "BLENDER not found (run with --needs=blender)"
else
  REQUIRE_BLENDER=1 python3 bench/creature/test_posed_image_check.py >"$T/img.out" 2>&1
  rc2=$?
  n2=$(grep -E '^Ran [0-9]+ test' "$T/img.out" | awk '{print $2}')
  if [ "$rc2" -eq 0 ] && [ "${n2:-0}" -ge 4 ]; then
    pass "test_posed_image_check.py: $n2 tests OK (incl. 5-creature 8-frame Blender check)"
  else
    fail "test_posed_image_check.py failed (exit $rc2, ran ${n2:-0}, need >= 4):"
    tail -40 "$T/img.out" | show
  fi
fi

echo "== rust formatting"
if command -v cargo >/dev/null 2>&1; then
  if (cd rust && cargo fmt --all --check) >/dev/null 2>&1; then
    pass "cargo fmt --all --check"
  else
    fail "cargo fmt --all --check"
  fi
else
  fail "no cargo on this machine"
fi

if [ "$FAILS" -eq 0 ]; then echo "GATE PASS"; exit 0; else echo "GATE FAIL ($FAILS)"; exit 1; fi
