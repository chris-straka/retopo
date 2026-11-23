#!/usr/bin/env bash
# Gate for retopo-4: 4-stage hole-repair bridge into bench/creature/cut.py.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
show() { sed 's/^/      /' | head -n 40; }

echo "== stale host/folder paths"
hits=$(grep -rnE "rfwork|weightforge|/home/f" bench/creature/ --exclude='*.pyc' --exclude='test_*.py' --exclude-dir='__pycache__' || true)
if [ -z "$hits" ]; then
  pass "no stale paths in bench/creature/"
else
  fail "stale paths left in bench/creature/:"
  echo "$hits" | show
fi

if [ -z "${BLENDER:-}" ]; then
  if [ -x "/Applications/Blender.app/Contents/MacOS/Blender" ]; then
    export BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
  elif command -v blender >/dev/null 2>&1; then
    export BLENDER="$(command -v blender)"
  fi
fi

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

echo "== Python tests (existing creature bench + new hole repair)"
python3 bench/creature/test_gamebench.py >"$T/gb.out" 2>&1
rc1=$?
n1=$(grep -E '^Ran [0-9]+ test' "$T/gb.out" | awk '{print $2}')
if [ "$rc1" -eq 0 ] && [ "${n1:-0}" -ge 13 ]; then
  pass "test_gamebench.py: $n1 tests OK"
else
  fail "test_gamebench.py failed (exit $rc1):"
  tail -30 "$T/gb.out" | show
fi

python3 bench/creature/test_budget_finder.py >"$T/bf.out" 2>&1
rc_bf=$?
n_bf=$(grep -E '^Ran [0-9]+ test' "$T/bf.out" | awk '{print $2}')
if [ "$rc_bf" -eq 0 ] && [ "${n_bf:-0}" -ge 2 ]; then
  pass "test_budget_finder.py: $n_bf tests OK"
else
  fail "test_budget_finder.py failed (exit $rc_bf):"
  tail -30 "$T/bf.out" | show
fi

if [ -z "${BLENDER:-}" ] || [ ! -x "${BLENDER:-}" ]; then
  fail "BLENDER not found (run with --needs=blender)"
else
  REQUIRE_BLENDER=1 python3 bench/creature/test_hole_repair_cut.py >"$T/hr.out" 2>&1
  rc_hr=$?
  n_hr=$(grep -E '^Ran [0-9]+ test' "$T/hr.out" | awk '{print $2}')
  if [ "$rc_hr" -eq 0 ] && [ "${n_hr:-0}" -ge 4 ]; then
    pass "test_hole_repair_cut.py: $n_hr tests OK"
  else
    fail "test_hole_repair_cut.py failed (exit $rc_hr, ran ${n_hr:-0}):"
    tail -50 "$T/hr.out" | show
  fi

  REQUIRE_BLENDER=1 python3 bench/creature/test_posed_image_check.py >"$T/img.out" 2>&1
  rc_img=$?
  n_img=$(grep -E '^Ran [0-9]+ test' "$T/img.out" | awk '{print $2}')
  if [ "$rc_img" -eq 0 ] && [ "${n_img:-0}" -ge 4 ]; then
    pass "test_posed_image_check.py: $n_img tests OK"
  else
    fail "test_posed_image_check.py failed (exit $rc_img):"
    tail -30 "$T/img.out" | show
  fi

  python3 bench/creature/retopo4_bench.py --out "$T/retopo4_bench" --gate >"$T/bench.out" 2>&1
  rc_bench=$?
  if [ "$rc_bench" -eq 0 ]; then
    pass "retopo4_bench.py: 3/3 broken Tripo + thin-geo creatures PASS"
    cat "$T/bench.out" | show
  else
    fail "retopo4_bench.py failed (exit $rc_bench):"
    tail -40 "$T/bench.out" | show
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
