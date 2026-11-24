# Direction: multi-game asset creation and engine roadmap

## Current direction (2026-11-23): unparked for general multi-game asset creation

As of **2026-11-23**, `retopo` is **unparked** and actively developed as a standalone, open-source quad remesher for general multi-game 3D asset creation:

- **Primary sweet spot today:** clean sculpts, stylized meshes, reusable animation base meshes (authored with `--guides`, `--features`, `--symmetry`, `--density`, and cleaned up in Blender), and automated multi-LOD export (`--lods`, `--uvs`).
- **Systematic engineering focus:** fixing the core parameterization, extraction, projection, and runtime failures documented in Rounds 1–6 and [archive/review-2026-11-13.md](archive/review-2026-11-13.md), hardening the CLI and Blender extension, and building the clean-room patch-layout backend (`--backend patch`) for guaranteed all-quad layouts on hard geometry.

The creature and prop benchmarks in `bench/creature/` and `bench/artist/` remain the shared objective testbed so engine changes are proved against real deformation, surface-fidelity, and visual gates rather than static quad counts alone.

---

## Recorded benchmark baseline (Rounds 1–6, `retopo-2`, `retopo-7`, `retopo-4`)

### Static props vs generated creatures (Round 1 & Round 6, 2026-11-13 to 2026-11-16)

- **Clean static props already built at budget:** do not remesh them at their own triangle count. In Round 1 ([log.md](log.md)), remeshing 10 clean props at their own budget caused `0.31–11.6%` p95 surface drift and collapsed thin features before the Round 5 `--no-finer-rerun` / finer-rerun work.
- **Noisy/spiky generated creatures (`retopo` vs Blender `decimate`, Round 6, 2026-11-16):**
  Across five generated creatures (furred biped, rooted quadruped, horned biped, spined quadruped, boss biped) cut to `10,000` triangles and rigged with identical transferred weights (`bench/creature/`):
  - `decimate` won **5/5** on bend tears (`0.00–10.8%` vs `0.26–10.9%`), **5/5** on 0–100 bend score (`63.7–93.9` vs `44.6–79.1`), **5/5** on surface p95 (`0.10–0.19%` vs `1.38–2.56%`, **11.59x** median ratio against a `<= 1.10x` gate), and **5/5** in blind game-camera judging (`seed 20261115`, `cant_tell` control and `1,500`-tri limb-swapped planted case both valid).
  - `retopo` left open holes on 4/5 creatures (`497` and `687` open edges on creatures 1 and 5; `32` and `11` on creatures 2 and 3), melted horn crowns, claws, and dorsal spikes, split connected meshes into dropped islands, and ran **1,221–2,143 s** on creature 4 when `island 0` exhausted coverage retries (`< 1 s` for `decimate`).
  - (Round 1's note that Blender's QuadriFlow refused 3 of 5 creatures was withdrawn in Round 2 once meshes were scaled to 100 units across.)

### Frozen held-out creature set & triangle budget finder (`retopo-2` & `retopo-7`, 2026-11-20)

- **`retopo-2`:** froze 3 non-overlapping held-out creatures (`creature_1` quadruped 83.6k dense, `creature_2` horned/spined 94.4k dense, `creature_3` furred 96.4k dense) via SHA-256 manifest in `bench/creature/heldout.json` (enforced by `bench/creature/gamebench.py`).
- **`retopo-7`:** `bench/creature/budget_finder.py` calibrated budgets (`2k–18k`) against the rigged dense source using `retopo-3`'s 8-frame posed silhouette and shading check (`posed_image_check.py`), cutting `creature_1` cleanly to **15,000 tris (-16.7%)** within decimate's noise ceiling while holding `creature_2` and `creature_3` at **17,999 tris (0.0%)** (`cant_tell` 3/3 in blind judging; control and planted valid).

### Raw `decimate` vs hole-repaired baselines (`retopo-4` & `clean-copy`, 2026-11-21)

- On the benchmark, raw `bench/creature/cut.py decimate` runs Blender's bare `DECIMATE` modifier directly on `dense.obj` without hole repair so topology cuts are isolated cleanly. That left `2`/`22`/`29` holes (`9`/`116`/`110` open edges) on the 3 held-out creatures during the valid `span/700` `clean-copy` comparison (`0a15ddc3`, 2026-11-21), where `clean-copy` closed all holes (`0` vs `0` dev; `0` vs `2/22/29` held-out) and won held-out bend (`2/3`), while losing dev bend (`1/4`) and surface p95 on `8/8` (`1.2428x` dev, `1.4684x` held-out; `controls.valid=true`, blind PASS).
- In `retopo-4`, `cut.py decimate-repaired` (`--repair` via `bench/creature/hole_repair.py`) added a 4-stage `untangle -> mend_holes -> cap_rims -> close_rims_in_obj` pipeline around `DECIMATE` with `--holes` perimeter guarding and `--report` metrics for broken or unclosed sculpt inputs.

### Artist base-mesh & prop benchmark (Rounds 4–5, 2026-11-13 to 2026-11-14)

- Across 20 CC0 hand-made meshes (`bench/artist/`, 3 noise seeds), default `retopo` (`--working-refinement 2`, odd-face pairing, exact-count retry, finer island re-runs) recovers **34.7% mean artist edge rings** (`12.0°` mean flow error, `1.02%` median / `1.44%` mean p95 shape error) vs QuadriFlow's **3.5%** rings and QuadWild+Bi-MDF's **32.2%** rings (`13.1°` flow, `0.72–0.82%` p95 shape).
- Where `retopo` still trails on artist/character work: curved non-manifold loops (e.g. key bow side loops), uniform vs artist-allocated facial density, and worst-case silhouette deviation on noisy meshes.

---

## Engine improvement tracks

### 1. Core solver, extractor, and projection fixes

Target the structural defects identified in Round 6 and [archive/review-2026-11-13.md](archive/review-2026-11-13.md), in priority order:

1. **Eliminate stray spike vertices and normal-flip projections:** project extracted vertices onto the dense original input rather than only the coarse working mesh, with normal-orientation guards matching the isotropic stage.
2. **Eliminate open holes on closed inputs:** resolve extraction-side hole reopening (`497` and `687` open edges on creatures 1 and 5 in Round 6).
3. **Stop splitting and dropping connected parts:** prevent single-ring non-manifold cleanup or winding discontinuities from detaching limbs/horns and discarding them via `remove_isolated_faces`.
4. **Preserve thin features (horns, claws, spines, fingers):** reduce one-shot MILS rounding collapse and extremity starvation at modest budgets.
5. **Realize joint loops, hard-edge guide loops, true symmetry, and local density:**
   - Pin joint rings with a single shared integer isoline per loop (with `--guides` / `--skeleton` integration) rather than tangent-only cross-field steering ([archive/research-2026-11-13.md](archive/research-2026-11-13.md)).
   - Protect marked feature edges during isotropic flip/relaxation passes and add angle-based automatic hard-edge guide loops ([archive/exoside-gap.md](archive/exoside-gap.md)).
   - Mirror the integer layout (half-mesh solve + mirror + weld) instead of position-only snapping under `--symmetry`.
   - Couple local density masks into cross-field singularity placement and scaling rather than only the final least-squares cover.
6. **Bound worst-case runtime:** prevent coverage retry loops + `--exact-count` cascades from spinning 20–35 minutes on failing islands (target `< 5 min` even on worst-case meshes).

Low-cost bend baseline (`retopo-4`): benchmark joint-weighted `cut.py decimate-weighted` (vertex-group weighting around joint rings and pointed extremities) alongside plain `decimate` and `retopo` to isolate topology vs joint-density effects.

### 2. Patch-layout back end (`--backend patch`)

On a real 150k-tri character (`~13.5k` quads, 2026-10-01), comparing `retopo` against QuadWild + Bi-MDF showed complementary strengths: `retopo` won irregular vertex share (`6.3%` vs `7.8%`) and corner angle error (`10°` vs `18°`), while QuadWild won worst-case surface error (`2.0%` vs `3.2%`) and produced `0` non-quads (`186` for `retopo`). Across 10 count-matched cases (`bench/matched.py` vs `bench/quadwild.py`), `retopo` won irregular vertices `8/10` and corner angles `8/10`, while QuadWild won worst-case fidelity and guaranteed all-quad output.

Evidence in [archive/igm-validity-spike.md](archive/igm-validity-spike.md) explains why: least-squares seamless covers combined with one-shot rounding cannot guarantee an inversion-free integer-grid map, and heuristic extraction compensates downstream.

**Plan:** build a clean-room Rust patch-layout back end (QGP / QuadWild / Bi-MDF flow quantization family) as an **opt-in `--backend patch`** mode while keeping the existing front end (weld, isotropic remesh, cross field, singularity simplification, density, symmetry, guides) and default extractor intact. Patch boundaries will accept:

1. **Automatic** curves traced from cross-field singularities,
2. **Joint loops** cut across skeleton joints (using `bench/rings.py` / `--skeleton` landmarks), and
3. **User guide strokes** drawn in Blender or supplied via `--guides` / `--features`.

Every candidate change gates on multi-seed `bench/noise.py` + `bench/score.py` distributions, `bench/deform.py` joint distortion checks, and `bench/creature/gamebench.py` posed/blind evaluations rather than single-run static counts.
