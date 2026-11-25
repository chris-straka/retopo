#!/usr/bin/env bash
# Gate for retopo-v21: the v2/v2.1 engine under the pins put back on
# 2026-11-25 (48279b98, 35d7061d, e517ce3f). See goal.md beside it.
#
# Checks, each printed PASS/FAIL; ends with GATE PASS or GATE FAIL:
#  - runs on macOS arm64 (the goldens, oracle fixtures and bench/baseline.json
#    are pinned there; Linux differs on float bits alone, AGENTS.md);
#  - scope: only rust/core/src (not auto_remesher/tests.rs), rust/cli/src,
#    README.md, PLAN.md and docs/ changed since the gate's commit; no test
#    file, fixture, golden, bench file, baseline, gate or Cargo file touched;
#  - no unit test in rust/*/src removed and no #[ignore] added;
#  - v2/v2.1 engine code kept: its functions, the retry setters and
#    set_v2_engine exist, and rust/core/tests/v2_opt_in.rs (v2.1's own pins,
#    run with set_v2_engine(true)) passes;
#  - cargo fmt, clippy -D warnings;
#  - the five public bench models fetched and byte-identical to the pinned
#    sha256 (a missing model would turn the beast test into a silent SKIP);
#  - cargo test --release over the whole workspace, no fail-fast: every
#    restored pin, oracle replay and CLI golden;
#  - bench/run.py --check bench/baseline.json (counts and deformation).
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
export LC_ALL=C
SLUG=retopo-v21
FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
show() { sed 's/^/      /' | head -n "${1:-30}"; }
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
# A private build folder: fleet shares a warm one between jobs of a repo, and
# on 2026-11-25 two concurrent jobs with different sources ran each other's
# code (bev6yhc8, bzw4wmyw). Cold, but every run tests its own tree.
unset CARGO_BUILD_BUILD_DIR
export CARGO_TARGET_DIR="$T/target"

BASE=$(git log -1 --format=%H -- "gates/$SLUG")
if [ -z "$BASE" ]; then fail "cannot find the commit that added gates/$SLUG"; BASE=HEAD; fi
echo "base $BASE, head $(git rev-parse HEAD)"

echo "== platform"
if [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ]; then
  pass "macOS arm64"
else
  fail "needs macOS arm64 (got $(uname -s) $(uname -m)); the pins are arm64's"
fi

echo "== scope"
changed=$( (git diff --name-only "$BASE"; git ls-files --others --exclude-standard) | sort -u)
outside=$(echo "$changed" | grep -v -E '^$|^rust/core/src/|^rust/cli/src/|^README\.md$|^PLAN\.md$|^docs/' ;
          echo "$changed" | grep -E '^rust/core/src/auto_remesher/tests\.rs$|Cargo\.(toml|lock)$')
if [ -z "$outside" ]; then
  pass "only rust/core/src, rust/cli/src, README.md, PLAN.md, docs/ changed"
else
  fail "files outside the goal's folders changed (tests, fixtures, bench, gates and Cargo files are fixed):"
  echo "$outside" | show
fi

echo "== tests kept"
count_tests() { git grep -h -E '#\[(tokio::)?test\]' "$1" -- rust/core/src rust/cli/src 2>/dev/null | wc -l | tr -d ' '; }
nb=$(count_tests "$BASE")
nh=$(grep -r -h -E '#\[(tokio::)?test\]' rust/core/src rust/cli/src | wc -l | tr -d ' ')
if [ "$nh" -ge "$nb" ]; then pass "unit tests in rust/*/src: $nh (base $nb)"; else fail "unit tests in rust/*/src dropped: $nh, base $nb"; fi
ib=$(git grep -h -E '#\[ignore' "$BASE" -- rust 2>/dev/null | wc -l | tr -d ' ')
ih=$(grep -r -h -E '#\[ignore' rust --include='*.rs' --exclude-dir=target | wc -l | tr -d ' ')
if [ "$ih" -le "$ib" ]; then pass "no #[ignore] added ($ih, base $ib)"; else fail "#[ignore] added: $ih, base $ib"; fi

echo "== v2/v2.1 code kept"
missing=""
for f in project_mesh_onto_surface project_point_normal_guarded anti_flip_move_ok \
         compute_polygon_geometry compute_triangle_normals vec3_is_finite \
         set_retry_budget set_max_coverage_retries set_v2_engine; do
  grep -r -q -E "fn $f[(<]" rust/core/src || missing="$missing $f"
done
if [ -z "$missing" ]; then pass "v2/v2.1 functions and set_v2_engine are in rust/core/src"; else fail "missing from rust/core/src:$missing"; fi
if ! command -v cargo >/dev/null 2>&1; then
  fail "no cargo on this machine"
  echo "GATE FAIL ($FAILS)"; exit 1
fi

echo "== format and lint"
if (cd rust && cargo fmt --all --check) >"$T/fmt" 2>&1; then pass "cargo fmt --all --check"; else fail "cargo fmt --all --check"; show 20 <"$T/fmt"; fi
if (cd rust && cargo clippy --locked --workspace --all-targets -- -D warnings) >"$T/clippy" 2>&1; then
  pass "cargo clippy --workspace --all-targets -D warnings"
else
  fail "cargo clippy -D warnings"
  grep -E '^(warning|error)' -A 6 "$T/clippy" | show 30
fi

echo "== bench models"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/retopo-bench-models"
mkdir -p "$CACHE" bench/models
models_ok=1
while read -r sum name; do
  [ -n "$name" ] || continue
  if [ ! -f "bench/models/$name" ] && [ -f "$CACHE/$name" ]; then cp "$CACHE/$name" "bench/models/$name"; fi
  if [ ! -f "bench/models/$name" ]; then
    curl -sSL --max-time 300 -o "bench/models/$name" \
      "https://raw.githubusercontent.com/alecjacobson/common-3d-test-models/master/data/$name" 2>"$T/curl" || true
  fi
  got=$(shasum -a 256 "bench/models/$name" 2>/dev/null | awk '{print $1}')
  if [ "$got" = "$sum" ]; then
    cp -n "bench/models/$name" "$CACHE/$name" 2>/dev/null || true
  else
    models_ok=0
    echo "      $name: sha256 ${got:-missing}, want $sum"
  fi
done <<'EOF'
d2fd040334c12b16804fbcb9962391232cc24708244617f931baaa871210563e armadillo.obj
74c5fecd883b761bc1b2bb6c82fe94d3b3b450297ab561e83c0e63c7ed46abe8 beast.obj
19e39748b145092878cc4faafd050505eb5991c5d3e497be2b4575673fa87968 nefertiti.obj
ea5bab2fbf545b1915f0d9faf6cc61ff8c18e0d8174ad61f8e35de15d8f6e3f8 fandisk.obj
5d7106b58f2ac0d017758480fe607c5b6bf7d6201d930d2dd1ba7022c4d281c0 xyzrgb_dragon.obj
EOF
if [ "$models_ok" -eq 1 ]; then pass "five bench models present, sha256 as pinned"; else fail "bench models missing or changed (see above)"; fi

echo "== cargo test --release (every target, no fail-fast)"
# v2_opt_in runs on its own: it calls set_v2_engine, so on a tree without it
# that one target doesn't build, and the restored pins must still report.
core_tests=$(for f in rust/core/tests/*.rs; do n=$(basename "$f" .rs); [ "$n" = v2_opt_in ] || printf -- '--test %s ' "$n"; done)
: >"$T/test"
# shellcheck disable=SC2086
(cd rust && cargo test --locked --release --no-fail-fast -p retopo_core --lib $core_tests) >>"$T/test" 2>&1; rc1=$?
(cd rust && cargo test --locked --release --no-fail-fast -p retopo --tests) >>"$T/test" 2>&1; rc2=$?
(cd rust && cargo test --locked --release --no-fail-fast -p retopo_solvers) >>"$T/test" 2>&1; rc3=$?
(cd rust && cargo test --locked --release -p retopo_core -p retopo_solvers --doc) >>"$T/test" 2>&1; rc4=$?
rc=$((rc1 + rc2 + rc3 + rc4))
# Name each failed test with its target: several targets share test names.
awk '/^ *Running /{bin=($2=="unittests")?$3:$2} /^test .* \.\.\. FAILED$/{sub(/^test /,""); sub(/ \.\.\. FAILED$/,""); print bin ": " $0}' "$T/test" | sort -u >"$T/failed"
if [ "$rc" -eq 0 ] && [ ! -s "$T/failed" ] && grep -q '^test result: ok' "$T/test"; then
  total=$(awk '/^test result: ok/{s+=$4} END{print s+0}' "$T/test")
  pass "cargo test: every target green ($total tests)"
elif [ -s "$T/failed" ]; then
  while read -r t; do
    fail "test $t"
    name=${t#*: }
    grep -F -A 12 -- "---- $name stdout ----" "$T/test" | grep -E 'panicked|left|right|mismatch|assert|unexpected|SKIP|:[0-9]+:[0-9]+' | show 8
  done <"$T/failed"
  grep -E 'golden mismatch' "$T/test" | sed -E 's#\(.*/tests/fixtures/#(tests/fixtures/#' | show 40
  if grep -q -E '^error(\[|:)' "$T/test"; then fail "cargo test: a target did not build"; grep -E '^error' -A 6 "$T/test" | show 20; fi
else
  fail "cargo test did not build or run (exit $rc)"
  grep -E '^error' -A 8 "$T/test" | show 30
  tail -n 15 "$T/test" | show 15
fi

echo "== v2 opt-in (v2.1's own pins with set_v2_engine(true))"
(cd rust && cargo test --locked --release --no-fail-fast -p retopo_core --test v2_opt_in) >"$T/v2" 2>&1
v2rc=$?
v2=$(grep -E '^test result:' "$T/v2" | tail -n 1)
case "$v2" in
  "test result: ok."*) n=$(echo "$v2" | awk '{print $4}')
    if [ "$v2rc" -eq 0 ] && [ "$n" -eq 4 ]; then pass "v2_opt_in: all 4 tests pass"; else fail "v2_opt_in: $n of 4 tests ran (exit $v2rc)"; fi ;;
  "test result: FAILED"*)
    grep -E '^test .* \.\.\. FAILED$' "$T/v2" | sed -E 's/^test (.*) \.\.\. FAILED$/\1/' >"$T/v2failed"
    while read -r t; do
      fail "v2_opt_in: $t"
      grep -F -A 12 -- "---- $t stdout ----" "$T/v2" | grep -E 'panicked|left|right|assert|SKIP' | show 6
    done <"$T/v2failed" ;;
  *) fail "v2_opt_in did not build (no set_v2_engine?)"
     grep -E '^error' -A 4 "$T/v2" | show 8 ;;
esac

echo "== bench --check bench/baseline.json"
TD=$CARGO_TARGET_DIR
if (cd rust && cargo build --locked --release -p retopo) >"$T/build" 2>&1 && [ -x "$TD/release/retopo" ]; then
  python3 bench/run.py --binary "$TD/release/retopo" --check bench/baseline.json >"$T/bench" 2>&1
  brc=$?
  if [ "$brc" -eq 0 ] && grep -q '^no regressions vs bench/baseline.json' "$T/bench"; then
    pass "bench: no regressions vs bench/baseline.json"
  elif grep -q '^REGRESSIONS vs' "$T/bench"; then
    awk '/^REGRESSIONS vs/{f=1; next} f&&/^  - /{sub(/^  - /,""); print}' "$T/bench" >"$T/regr"
    while read -r r; do fail "bench $r"; done <"$T/regr"
    sed -n '/^model  /,/^saved /p' "$T/bench" | show 20
  else
    fail "bench did not finish (exit $brc)"
    tail -n 25 "$T/bench" | show 25
  fi
else
  fail "cargo build --release -p retopo (no binary at $TD/release/retopo)"
  grep -E '^error' -A 8 "$T/build" | show 20
fi

if [ "$FAILS" -eq 0 ]; then echo "GATE PASS"; exit 0; else echo "GATE FAIL ($FAILS)"; exit 1; fi
