# Documentation Index

Documentation for `retopo` (pure Rust quad-remeshing core and CLI + the *RetopoForge* Blender extension).

## Active Documentation

- [Project Overview & CLI Quickstart](../README.md) — installation, CLI flags and modes, Blender extension overview, measured strengths/limits, and test/benchmark commands.
- [Architecture](architecture.md) — workspace crates (`retopo_core`, `retopo_solvers`, `retopo` CLI), the Blender extension subprocess boundary, and the benchmark harness.
- [Engineering Roadmap (PLAN)](../PLAN.md) — current priorities across CLI/Blender hardening, CI reliability, core solver/extractor fixes, and the `--backend patch` track.
- [Direction](direction.md) — multi-game asset creation direction (2026-11-23), measured Round 1–6 and held-out benchmark summary, and engine improvement roadmap.
- [Benchmark Log](log.md) — chronological benchmark rounds, experiment verdicts, and keep/drop measurements.
- [Creature Benchmark Log](bench/log.md) & [Bench Gates](bench/gates.json) — creature benchmark gate status and void-run record.
- [Creature Benchmark Harness README](../bench/creature/README.md) — posed deformation, silhouette, blind game-camera judging, and hole-repair decimation harness (`bench/creature/`).
- [Blender Extension README](../blender/README.md) — RetopoForge Blender 4.2+ panel reference, Flow Guides, Sharp Features, Density Masks, LODs, and PBR Bake Assist.
- [Agent & Contributor Notes](../AGENTS.md) — build/test commands, macOS vs Linux verification behavior, repository layout, and upstream reference policy.

## Archived R&D & Spike Logs (`docs/archive/`)

Historical roadmaps, C++-to-Rust port records, numerical spikes, and early pipeline notes kept for reference:

### Roadmap & Port History
- [todo-history.md](archive/todo-history.md) — historical task log from initial C++ cleanup through the Rust switch and post-switch feature waves.
- [rust-switch-verdict.md](archive/rust-switch-verdict.md) — C++-to-Rust switch verdict, differential oracle summary, and timing notes.
- [rust-port-conventions.md](archive/rust-port-conventions.md) — module-by-module port contract, FMA/libm audits, and oracle generation rules.
- [rust-solvers-experiment.md](archive/rust-solvers-experiment.md) — `retopo_solvers` calibration (`faer` vs Eigen CLS/MILS).

### Parameterization, Rounding & Coverage
- [igm-validity-spike.md](archive/igm-validity-spike.md) — integer-grid-map inversion census and sub-visible input noise floor analysis.
- [mils-rounding-scope.md](archive/mils-rounding-scope.md) — why progressive/sizing-aware MILS rounding was scoped out.
- [untangle-verdict.md](archive/untangle-verdict.md) — post-rounding Garanzha barrier untangling evaluation (rejected).
- [tetra-collapse.md](archive/tetra-collapse.md) — diagnosis of non-monotonic empty outputs on tiny tetrahedral inputs.
- [beast-knife-edge-bisection.md](archive/beast-knife-edge-bisection.md) — stage-by-stage bisection of appendage UV folding at low quad budgets.
- [coverage-retry.md](archive/coverage-retry.md) — resolution-relative coverage verification, jittered retries, finer re-runs, and extremity checks.
- [corner-marks.md](archive/corner-marks.md) — alignment-only explicit sharp features (`AlignU`/`AlignV`) fix for over-constrained cages.

### Density & Dipole Insertion
- [density-poles-spike.md](archive/density-poles-spike.md) — why localized `--density` masks saturate without new singularities.
- [dipole-mechanism-spike.md](archive/dipole-mechanism-spike.md) — `+1/-1` dipole-pair insertion prototype across density boundaries.
- [dipole-fixtures-baseline.md](archive/dipole-fixtures-baseline.md) — procedural finger fixtures and baseline saturation curves.
- [dipole-production.md](archive/dipole-production.md) — production automatic dipole placement (`--dipoles auto|off`), offset rings, and sliver guards.

### Reviews, Literature & Baselines
- [review-2026-11-13.md](archive/review-2026-11-13.md) — code audit of engine joint-loop, rounding, symmetry, extraction, and projection limitations.
- [research-2026-11-13.md](archive/research-2026-11-13.md) — literature and open-source survey on joint loops, pole placement, rounding, and symmetric layouts.
- [compare-baselines.md](archive/compare-baselines.md) — `bench/compare.py` comparison harness against Blender Voxel and standalone QuadriFlow.
- [deformation-test.md](archive/deformation-test.md) — `bench/deform.py` posed joint stretch, volume-loss, and flip-count regression gate.
- [perf.md](archive/perf.md) — `bench/profile.py` phase breakdown, threading notes, and Rust parallelism speedup.
- [exoside-gap.md](archive/exoside-gap.md) — 2026-09-29 feature and UX comparison against Exoside Quad Remesher.

### Workflows & Historical Pipeline Notes
- [face-flow-iterate.md](archive/face-flow-iterate.md) — iterative Blender workflow for character heads using guides, masks, and symmetry.
- [lod-strategy.md](archive/lod-strategy.md) — `--lods` rung budgeting and interaction with game-engine import-time auto-LOD.
- [plan-guide-drawing.md](archive/plan-guide-drawing.md) — archived design note for freehand surface guide strokes in the Blender add-on.
- [rigging-strategy.md](archive/rigging-strategy.md) — archived notes on external rigging tools and weight transfer trade-offs.
