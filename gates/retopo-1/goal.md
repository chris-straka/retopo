# retopo-1: Make the creature bench neutral, portable, and a gate

Remove all hardcoded host/machine paths (`~/rfwork`, `weightforge`, `/home/f`) from
`bench/creature/`, make `gamebench.py` and `blind.py` tool-neutral (`--tools A,B`,
supporting any cut from `cut.py` plus `retopo` and self-comparison `decimate,decimate`),
add a real self-control check, write standalone `verdict.json` with proper exit codes,
and add frozen held-out sha256 manifest loading/verification (`--heldout` / `--require-heldout`).

Read `bench/creature/gamebench.py`, `bench/creature/gamebench.sh`, `bench/creature/blind.py`,
`bench/creature/test_gamebench.py`, and `docs/plans/retopo.md` first.

## Requirements

1. **Tool-neutral `gamebench.py` (`--tools A,B`):**
   - `--tools` defaults to `"retopo,decimate"` and accepts any `CHALLENGER,BASELINE` pair
     where each side is `"retopo"` or any `cut.py` mode (`decimate`, `quadriflow`, `artist`,
     `decimate-weighted`, etc.), including self-comparison (`decimate,decimate`).
   - Only require `--retopo` / `$RETOPO` when `"retopo"` is in `--tools`.
   - `verdict(rows, tools=("retopo", "decimate"))`:
     - Works cleanly for arbitrary `(tool_a, tool_b)` and self-comparison `(t, t)` (where
       every creature is a tie, `winner` is `None`, `challenger_beats_baseline` is `False`,
       and `median_surface_ratio` is `1.0`).
     - Checks real self-control when `row["cuts"].get("control")` is present (any delta
       against baseline sets `controls["cant_tell_zero_delta"] = False` and `controls["valid"] = False`).
     - An invalid run (`controls["valid"] == False`) always sets `winner = None` and
       `challenger_beats_baseline = False`.
     - Includes `tools: {"challenger": tool_a, "baseline": tool_b}`, `winner`,
       `challenger_beats_baseline`, `median_surface_ratio`, and legacy `beats_decimate` /
       `median_surface_ratio_retopo_over_decimate` keys for backward compatibility.
   - Writes standalone `verdict.json` beside `results.json` and `report.md`.
   - Supports `--gate` (exit 0 only when controls valid AND `challenger_beats_baseline`;
     without `--gate`, exit 0 when controls valid, 1 when invalid, 2 on CLI/env/manifest errors).
   - `report(rows, v, a, tools=...)` formats the active challenger/baseline names cleanly and
     never leaks subject/corpus filenames.

2. **Held-out manifest contract (enabling `retopo-2`):**
   - `load_heldout_manifest(path)` loads either a JSON manifest (`{"sha256": [...]}` or
     `{"creatures": [{"sha256": ...}]}`) or line-based hex sha256 file (ignoring `#` comments),
     validating at least 3 unique 64-hex sha256 entries (zero private names required).
   - `verify_heldout_workspace(work, manifest_path)` verifies every `dense.obj` in `work`
     against the frozen hashes and raises `ValueError` if the manifest is missing/invalid or
     any expected hash is missing/mismatched.
   - CLI flags `--heldout PATH` and `--require-heldout`: if `--require-heldout` is set and
     `--heldout` is omitted or missing/invalid, exit 2 with a clear stderr message.

3. **Portable env-driven `gamebench.sh` & zero stale paths:**
   - Remove `~/rfwork/env.sh`, `~/rfwork/bin/weights-new`, `~/rfwork/set`,
     `~/rfwork/gamebench_clean`, and stale `weightforge` references in `bench/creature/`.
   - Require `GAMEBENCH_WORK`, `GAMEBENCH_OUT`, `BLENDER`, and `WEIGHTS` (or `WEIGHTS_NEW`)
     via environment or standard PATH/sibling discovery with clear stderr errors + exit 2.
   - Only build/require `retopo` when `--tools` includes `retopo`.

4. **Tool-neutral `blind.py`:**
   - Read `tools` from `results.json` (defaulting to `("retopo", "decimate")`), supporting
     arbitrary `(tool_a, tool_b)` and self-comparison `(t, t)` without leaking tool names
     inside `blind/manifest.json`.

## Evidence

`bash gates/retopo-1/gate.sh` prints `GATE PASS`:
- Protected paths (`gates/`, `bench/creature/test_gamebench.py`, `rust/`) unchanged.
- `grep -rnE "rfwork|weightforge|/home/f" bench/creature/` prints nothing.
- `python3 bench/creature/test_gamebench.py` passes all tests (>= 13 tests).
- `bash -n bench/creature/gamebench.sh` passes.
- `cd rust && cargo fmt --all --check` passes.
