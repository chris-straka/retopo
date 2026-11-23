# retopo-4: 4-Stage Hole-Repair Bridge in `bench/creature/cut.py`

Bridge `motion/tools/tripo_to_glb.py`'s 4-stage hole-repair pipeline
(`untangle` -> `mend_holes` -> `cap_rims` -> `close_rims_in_file` / `.obj` rim
closure) cleanly into `retopo`'s `bench/creature/cut.py` (`decimate-repaired`
mode and `--repair` / `--holes` / `--report` flags) so broken Tripo-style
sculpts with non-manifold tangles, pinched figure-8 boundaries, unwelded cracks,
and open holes repair and decimate cleanly without eating thin valid geometry
or changing raw `cut.py decimate` behavior.

Keep `retopo` parked for automated monsters (not a general hero blocker), and
verify with `python3 ~/Games/hlh/tools/scan_terms.py` (0 hits).

## Requirements

1. **`bench/creature/hole_repair.py` + `bench/creature/cut.py`:**
   - Implement the 4-stage repair (`untangle`, `mend_holes`, `cap_rims` with
     simple-cycle decomposition for pinched rims, and `close_rims_in_obj` /
     `close_rims_in_file` at quantized `1e-5 * size` seam tolerance).
   - Add `decimate-repaired` mode to `cut.py` plus `--repair`, `--holes SHARE`,
     and `--report R.json` options.
   - Keep `cut.py decimate` (without `--repair`) raw so `test_clean_copy_cut.py`
     and existing benchmarks stay unchanged.
   - Respect `--holes SHARE` (do not eat large intended openings when small
     limits like `0.08`/`0.12` are passed; count-only when `0`), and never
     eat thin valid geometry (horns, spines, claws, tail tips).
2. **Gate-first RED -> GREEN tests (`bench/creature/test_hole_repair_cut.py`):**
   - Pure-Python unit tests for Stage-4 `.obj` repair (open rim, unwelded crack,
     sliver face, figure-8 pinch, perimeter limit).
   - Headless Blender tests comparing raw `decimate` vs `decimate-repaired` on
     broken Tripo-style sculpts with thin valid features.
3. **Docs & Hygiene:**
   - Update `README.md`, `docs/direction.md`, and `bench/creature/README.md` to
     document the `cut.py` 4-stage hole-repair bridge while keeping clear that
     `retopo` stays parked for monster work.
   - Pass `gates/retopo-4/gate.sh`, `cargo fmt --all --check`, existing Python
     bench tests, and `scan_terms.py` (0 hits).
