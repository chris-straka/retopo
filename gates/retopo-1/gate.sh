#!/usr/bin/env bash
# Gate for retopo-1: neutral, portable creature bench with verdict JSON,
# self-comparison control, held-out sha256 manifest refusal, and zero stale paths.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
SLUG=retopo-1
FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
show() { sed 's/^/      /' | head -n 30; }
BASE=$(git log -1 --format=%H -- gates/$SLUG)
[ -n "$BASE" ] || { fail "cannot find the commit that added the gate"; BASE=HEAD; }

echo "== scope"
for p in gates bench/creature/test_gamebench.py rust; do
  if git diff --quiet "$BASE" -- "$p" && [ -z "$(git status --porcelain -- "$p")" ]; then
    pass "$p unchanged"
  else
    fail "$p changed since the gate was written"
  fi
done

other=$( (git diff --name-only "$BASE"; git status --porcelain | cut -c4-) | grep -v -E '^(bench/creature/(gamebench\.py|gamebench\.sh|blind\.py|run\.py|README\.md)|docs/.*)?$' | sort -u)
if [ -z "$other" ]; then pass "only bench/creature scripts and docs changed"; else fail "other files changed:"; echo "$other" | show; fi

echo "== stale host/folder paths"
hits=$(grep -rnE "rfwork|weightforge|/home/f" bench/creature/ --exclude='*.pyc' --exclude-dir='__pycache__' || true)
if [ -z "$hits" ]; then
  pass "no rfwork, weightforge, or /home/f in bench/creature/"
else
  fail "stale paths left in bench/creature/:"
  echo "$hits" | show
fi

echo "== shell syntax"
if bash -n bench/creature/gamebench.sh; then
  pass "bash -n bench/creature/gamebench.sh"
else
  fail "bash -n bench/creature/gamebench.sh failed"
fi

echo "== Python tests"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
python3 bench/creature/test_gamebench.py >"$T/py.out" 2>&1
rc=$?
n=$(grep -E '^Ran [0-9]+ test' "$T/py.out" | awk '{print $2}')
if [ "$rc" -eq 0 ] && [ "${n:-0}" -ge 13 ]; then
  pass "bench/creature/test_gamebench.py: $n tests OK"
else
  fail "bench/creature/test_gamebench.py failed (exit $rc, ran ${n:-0}, need >= 13):"
  tail -40 "$T/py.out" | show
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
