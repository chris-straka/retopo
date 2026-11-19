#!/usr/bin/env bash
# Gate for retopo-8 (game-tools docs/plans/retopo.md): parked-repo hygiene.
#  - scope: only TODO.md, docs/direction.md and opencode.json changed by the worker;
#  - opencode.json is gone;
#  - `grep -nE "Godot|Twilight|clean copy" TODO.md docs/direction.md` prints nothing;
#  - TODO.md no longer opens with the owner-context header, and says near its top that it
#    is history;
#  - direction.md's creature paragraph says what the code does: the route decimates the raw
#    mesh in motion's tools/tripo_to_glb.py, not meshkit's clean copy;
#  - `cd rust && cargo fmt --all --check` passes (no code changed).
# Builds nothing, starts nothing. Prints PASS/FAIL per check, ends with GATE PASS or GATE FAIL.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
SLUG=retopo-8
FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
show() { sed 's/^/      /' | head -n 30; }
BASE=$(git log -1 --format=%H -- gates/$SLUG)
[ -n "$BASE" ] || { fail "cannot find the commit that added the gate"; BASE=HEAD; }

echo "== scope"
# The worker's files: its own commits ("carbon $SLUG: round N") and anything uncommitted;
# other people's commits on main after the gate are not its.
mine=$(for c in $(git log --format=%H --grep "^carbon $SLUG: " "$BASE..HEAD"); do git diff-tree --no-commit-id --name-only -r "$c"; done)
other=$( (echo "$mine"; git status --porcelain | cut -c4-) | grep -v -E '^(TODO\.md|docs/direction\.md|opencode\.json)?$' | sort -u)
if [ -z "$other" ]; then pass "only TODO.md, docs/direction.md and opencode.json changed"; else fail "other files changed:"; echo "$other" | show; fi

echo "== opencode.json"
if [ ! -e opencode.json ] && [ -z "$(git ls-files opencode.json)" ]; then pass "opencode.json is gone"; else fail "opencode.json is still there"; fi

echo "== stale words"
hits=$(grep -nE "Godot|Twilight|clean copy" TODO.md docs/direction.md)
if [ -z "$hits" ]; then pass "no Godot, Twilight or 'clean copy' in TODO.md or docs/direction.md"; else fail "stale words left:"; echo "$hits" | show; fi

echo "== TODO.md header"
if grep -q "Owner context" TODO.md; then fail "TODO.md still has the owner-context header"; else pass "the owner-context header is gone"; fi
if head -n 6 TODO.md | grep -qi "histor"; then pass "TODO.md says near its top that it is history"; else fail "TODO.md's first 6 lines don't say the file is history"; head -n 6 TODO.md | show; fi
n=$(wc -l < TODO.md | tr -d ' ')
nb=$(git show "$BASE:TODO.md" | wc -l | tr -d ' ')
if [ "$n" -ge $((nb - 40)) ]; then pass "TODO.md keeps its history ($n lines, base $nb)"; else fail "TODO.md lost more than the header: $n lines, base $nb"; fi

echo "== the creature line matches the code"
para=$(awk '/^\*\*Creatures/{p=1} p&&/^$/{exit} p' docs/direction.md)
if [ -z "$para" ]; then fail "docs/direction.md has no **Creatures paragraph"
else
  if echo "$para" | grep -q "tripo_to_glb.py"; then pass "the creature paragraph names tools/tripo_to_glb.py, where the route decimates"; else fail "the creature paragraph doesn't name tripo_to_glb.py"; fi
  if echo "$para" | grep -qi "meshkit"; then fail "the creature paragraph still sends creatures through meshkit"; echo "$para" | show; else pass "the creature paragraph no longer names meshkit as the route"; fi
fi

echo "== rust formatting"
if command -v cargo >/dev/null 2>&1; then
  if (cd rust && cargo fmt --all --check) >/dev/null 2>&1; then pass "cargo fmt --all --check"; else fail "cargo fmt --all --check"; fi
else fail "no cargo on this machine"; fi

if [ "$FAILS" -eq 0 ]; then echo "GATE PASS"; exit 0; else echo "GATE FAIL ($FAILS)"; exit 1; fi
