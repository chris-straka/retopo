# PLAN: Active Engineering Roadmap

Active roadmap for `retopo` as a standalone, open-source multi-game quad remesher. See [docs/direction.md](docs/direction.md) for measured context and [docs/README.md](docs/README.md) for the full documentation map (historical build notes live in [docs/archive/todo-history.md](docs/archive/todo-history.md); Round 7 engine fixes and Blender v2 hardening are logged in [docs/log.md](docs/log.md)).

## 1. Blender Extension & Pipeline Polish

- **Asset-pipeline guide helpers (G2/G3, optional):** one-click character GLB open/send integration, joint-suggested starting guide strokes from `--skeleton`, per-mesh guide keying, and `--density` brush adapter pass-through ([docs/archive/plan-guide-drawing.md](docs/archive/plan-guide-drawing.md); Object-Mode G1 surface stroke drawing, mirroring, preview, and `--guides` merging are complete).

## 2. CI & Cross-Platform Reliability

- **Keep macOS and Linux CI green:** keep `.github/workflows/development.yml` (`cargo fmt`, `cargo clippy`, `cargo test --locked --release`, Python unit tests, `bench/run.py --check bench/baseline.json`) and `.github/workflows/release.yml` (Linux compile + `bench/baseline-linux.json`, macOS tests + smoke + extension zip) reliable without silent skips; consider adding `blender/tests/test_unit.py` to CI.
- **Linux x86_64 test parity (optional):** address the ~11 Linux x86_64 test binaries that diverge on last-ulp `libm` / default NaN bits via ULP-tolerant oracles or Linux golden tiers ([AGENTS.md](AGENTS.md)).
- **Regression coverage:** expand `bench/creature/test_*.py`, `blender/tests/`, and Rust `w2_hardening` / `w4_determinism` / `cli_contract` checks whenever flags or edge-case guards change.

## 3. Core Solver, Projection & Constraint Realization

Remaining open defects after Round 7 watertight projection, hole closure, detached-part retention, and `WR=1` defaults ([docs/log.md](docs/log.md), [docs/archive/review-2026-11-13.md](docs/archive/review-2026-11-13.md)), ordered by impact:

1. **Preserve thin features (horns, claws, spines, extremities):** reduce one-shot MILS rounding collapse and extremity starvation at modest budgets.
2. **Realize joint loops, hard-edge guide loops, layout symmetry, and local density:**
   - Pin joint rings via single shared integer isolines (`--guides` / `--skeleton`) rather than tangent-only cross-field steering ([docs/archive/research-2026-11-13.md](docs/archive/research-2026-11-13.md)).
   - Add angle-based automatic hard-edge guide loops alongside the completed `iso_remesh_kernel` feature-edge preservation ([docs/archive/exoside-gap.md](docs/archive/exoside-gap.md)).
   - Mirror the integer layout (half-mesh solve + mirror + weld) under `--symmetry` instead of position-only snapping.
   - Couple `--density` into cross-field singularity placement and scaling so localized asks realize cleanly without budget collapse.
3. **Bound worst-case runtime:** add explicit caps on pathological retry cascades (`coverage` retries + `--exact-count`) well under 5 minutes on heavy multi-attempt meshes.

## 4. Opt-In Patch-Layout Back End (`--backend patch`)

- Design and validate a clean-room Rust patch-layout back end (QGP / QuadWild / Bi-MDF flow quantization family) selectable via `--backend patch`, leaving the existing front end and default extractor intact ([docs/direction.md](docs/direction.md), [docs/archive/igm-validity-spike.md](docs/archive/igm-validity-spike.md)). The Round 7 `origin/agent/patch-backend` prototype overshot counts by 10.6x–21.6x and left open boundaries, so it stays off `main`.
- Support patch boundaries traced from singularities, skeleton joint loops, and user guide/feature strokes for guaranteed all-quad layouts, non-collapsing thin limbs, and tight worst-case surface fidelity.
- Gate every engine change on multi-seed `bench/noise.py` + `bench/score.py` spreads, `bench/deform.py`, and `bench/creature/gamebench.py` posed/blind checks.
