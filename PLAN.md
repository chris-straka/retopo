# PLAN: Active Engineering Roadmap

Active roadmap for `retopo` as a standalone, open-source multi-game quad remesher. See [docs/direction.md](docs/direction.md) for measured context and [docs/README.md](docs/README.md) for the full documentation map (historical build notes live in [docs/archive/todo-history.md](docs/archive/todo-history.md)).

## 1. CLI & Blender Extension Hardening

- **Multi-material LOW bake slots:** add bake map nodes to every material slot on a multi-material LOW target (currently only slot 0 receives them, which triggers Cycles "no active image" errors).
- **Color-layer reuse in `Transfer HIGH Colors`:** reuse an existing same-named face-corner color layer instead of generating `.001` suffixes on repeat transfers.
- **Evaluated-mesh UV projection:** update `Project HIGH UVs` to read HIGH's evaluated mesh (matching modifiers applied during export) rather than the base mesh.
- **Scale-aware bake ray extrusion:** scale default Cycles bake ray extrusion relative to model size so thin adjacent limbs (e.g. fingers near legs) do not capture rays from neighbours.
- **Freehand surface guide drawing:** add an Object-Mode surface stroke workflow in the Blender panel so artists can paint `--guides` on 1M+ triangle meshes without entering Edit Mode ([docs/archive/plan-guide-drawing.md](docs/archive/plan-guide-drawing.md)).
- **Origin-offset decimator snapping & relative OBJ indices:** snap `snap_decimator_input` relative to the mesh bounding-box minimum (avoiding f32 ULP loss far from the world origin, with baseline re-pinning via `bench/noise.py`), and support negative relative OBJ vertex indices in `bench/rings.py`.

## 2. CI & Cross-Platform Reliability

- **Keep macOS and Linux CI green:** keep `.github/workflows/development.yml` (`cargo fmt`, `cargo test --locked --release`, `bench/run.py --check bench/baseline.json`) and `.github/workflows/release.yml` (Linux compile + `bench/baseline-linux.json`, macOS tests + smoke + extension zip) reliable without silent skips.
- **Linux platform baselines & tests:** maintain `bench/baseline-linux.json` and `bench/deform_baseline-linux.json` alongside macOS baselines; optionally address the ~11 Linux x86_64 test binaries that diverge on last-ulp `libm` / default NaN bits via ULP-tolerant oracles or Linux golden tiers ([AGENTS.md](AGENTS.md)).
- **Hardening & regression coverage:** expand `bench/creature/test_*.py`, `blender/tests/test_headless.py`, and Rust `w2_hardening` / `w4_determinism` / `cli_contract` checks whenever flags or edge-case guards change.

## 3. Core Solver, Projection & Extractor Improvements

Measured Round 6 and audit defects ([docs/log.md](docs/log.md), [docs/archive/review-2026-11-13.md](docs/archive/review-2026-11-13.md)), ordered by impact:

1. **Dense-input projection & normal-flip guards:** project extracted vertices back onto the dense original input surface rather than only the coarse working mesh, with normal-angle checks matching the isotropic stage to stop stray spikes and folded thin geometry.
2. **Close extraction-stage holes on closed inputs:** eliminate open edges left on closed meshes during quad cleanup and non-manifold repair.
3. **Preserve connected parts:** prevent single-ring non-manifold removals or face-winding splits from detaching limbs/appendages and discarding them via `remove_isolated_faces`.
4. **Preserve thin features (horns, claws, spines, extremities):** reduce one-shot MILS rounding collapse and coarse working-mesh volume loss at modest budgets.
5. **Realize joint loops, hard-edge guide loops, layout symmetry, and local density:**
   - Pin joint rings via single shared integer isolines (`--guides` / `--skeleton`) rather than tangent-only cross-field steering ([docs/archive/research-2026-11-13.md](docs/archive/research-2026-11-13.md)).
   - Protect marked `--features` edges across isotropic flips/relaxation and add angle-based automatic hard-edge guide loops ([docs/archive/exoside-gap.md](docs/archive/exoside-gap.md)).
   - Mirror the integer layout (half-mesh solve + mirror + weld) under `--symmetry` instead of position-only snapping.
   - Couple `--density` into cross-field singularity placement and scaling so localized asks realize cleanly without budget collapse.
6. **Bound worst-case runtime:** cap pathological retry cascades (`coverage` retries + `--exact-count`) well under 5 minutes on heavy multi-attempt meshes.

## 4. Opt-In Patch-Layout Back End (`--backend patch`)

- Implement a clean-room Rust patch-layout back end (QGP / QuadWild / Bi-MDF flow quantization family) selectable via `--backend patch`, leaving the existing front end and default extractor intact ([docs/direction.md](docs/direction.md), [docs/archive/igm-validity-spike.md](docs/archive/igm-validity-spike.md)).
- Support patch boundaries traced from singularities, skeleton joint loops, and user guide/feature strokes for guaranteed all-quad layouts, non-collapsing thin limbs, and tight worst-case surface fidelity.
- Gate every engine change on multi-seed `bench/noise.py` + `bench/score.py` spreads, `bench/deform.py`, and `bench/creature/gamebench.py` posed/blind checks.
