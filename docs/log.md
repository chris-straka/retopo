# retopo log: what was tried, kept and dropped

Newest round first. Scores come from [../bench/creature/README.md](../bench/creature/README.md):
generated creatures and static props, plus hand-made rigged
animals as the topology reference. Subjects are named by kind only.

## The keep rule (owner, 2026-11-13, replaces the first one)

"Some tools are better for different characters", "we might just be
doing the benchmarks wrong", "we should be improving and not trying and
dropping":

1. A method that wins on some models stays. The tool learns to choose per
   model by measurable reasons, and the log says where each method wins.
2. A promising method that loses gets worked on: find why, fix it, run it
   again. Parking one needs the reason and what it would take.
3. When the score and the pictures disagree, the benchmark is fixed first.

The comparison itself (`bench/creature/table.py --compare A B`) still
reports, per score, on how many models B is better and worse; that now
feeds rule 1 (where B wins) instead of a single keep/drop verdict.

The main score comes from professional ground truth where there is one:
hand-made game creatures and characters (CC0/CC-BY, benchmark use only),
turned into what a generator gives (dense, triangulated, parts fused),
remeshed, and compared with the pro mesh and bent with the pro's rig.

## Round 7 (2026-11-23): Watertight projection, hole closure, and fast WR=1 default

Deep-dive engine pass (against `main` WR=2, `main` WR=1, and `origin/agent/patch-backend` `--backend patch`) measured at `5,000` target quads across 6 public bench meshes (`armadillo`, `beast`, `nefertiti`, `fandisk`, `xyzrgb_dragon`, stylized character dense cut) plus 2 dense generated creatures (`creature_spined` quadruped, `creature_horned` horned/furred):

- **Watertight & manifold everywhere (`0` open edges, `0` non-manifold edges on all 8 models):**
  - Replaced vertex deletion on pinch vertices in `quad_extractor/holes.rs` with per-edge removal + polygon half-edge fan walks, added out-of-plane distance checks in `test_point_in_triangle`, retained non-trivial detached components in `remove_isolated_faces`, pruned duplicate-edge quads, and added deterministic quad/triangle-ear `close_remaining_hole` fallback on closed-input tears (closing `686 -> 0` open edges on `creature_horned`, `17 -> 0` on `beast`, and `6 -> 0` on `xyzrgb_dragon`).
  - Guarded `smooth_and_project` with non-backfacing normal checks + `3x` local edge cap and added normal-aligned `project_mesh_onto_surface` onto `island.input_vertices`/`input_triangles`, cutting candidate-to-input `p95` surface error to `0.000%–0.019%` on 7/8 models (`0.072%` on `creature_horned` at WR=1).
  - Protected `feature_state == 1` edges in `iso_remesh_kernel.rs` (`flip_edge` / `break_edge`; keeping `relax_vertex` unlocked after ablation showed vertex-locking regressed speed and coverage), anchored `snap_decimator_input` to bbox minimum in `f64`, and softened organic coarse auto-sharp integer locks to `AlignU`/`AlignV`.
- **CLI default `--working-refinement 1.0`:** Switching CLI default from `2.0` back to `1.0` combined with the extractor/projection fixes delivers **3.0x–13.4x speedups** while closing holes cleanly (`xyzrgb_dragon`: `83.2s -> 16.7s`, `open 6 -> 0`, `surf_p95 0.849% -> 0.439%`, `irr 9.80% -> 6.75%`; `creature_spined`: `212.5s -> 15.9s`, `irr 24.66% -> 15.41%`, `degen 27 -> 1`, `surf_p95 1.498% -> 1.178%`; `creature_horned`: `135.7s -> 45.0s`, `open 686 -> 0`, `irr 30.92% -> 23.50%`, `min_ang 5.43° -> 10.74°`; `armadillo`: `13.1s -> 1.59s`; `fandisk`: `6.04s -> 2.12s`, `twist/norm_flip 0/0`). Smooth busts (`nefertiti`, `beast`) still benefit from explicit `--working-refinement 2.0` when tighter surface p95 is desired.
- **`--backend patch` (`origin/agent/patch-backend`) verdict:** Overshoots `5,000` quad targets by `10.6x–21.6x` (`52.9k–108.1k` quads), leaves `13,979–51,264` open edges across `94–844` islands, `44.3%–45.2%` irregular verts, and `1,313–13,484` normal flips (`514.7s` on `creature_spined` with `6/7` islands failed); left off `main`.

## 2026-11-20: Frozen held-out creature set (`retopo-2`) and triangle budget finder (`retopo-7`)

- **`retopo-2` (held-out freeze):** Froze 3
  held-out backlog creatures (zero overlap with the 5 dev creatures above:
  `creature_1` quadruped 83.6k dense, `creature_2` horned/spined 94.4k dense,
  `creature_3` furred 96.4k dense) prepared through `clean,prep,ref`. Their
  `dense.obj` SHA-256 hashes are committed in `bench/creature/heldout.json`
  (zero creature names), and `gamebench.py` now refuses to run (exit 2) if
  the frozen held-out manifest is missing or invalid.
- **`retopo-7` (budget finder on the 3 frozen held-out creatures):**
  `bench/creature/budget_finder.py` measures each candidate budget
  (`2k..18k`) against the rigged dense source using `retopo-3`'s 8-frame
  posed game-camera silhouette and shading check (`posed_image_check.py`),
  accepting the smallest triangle count within decimate's own noise at `18k`
  (`ref_vs_dense + max(ref_vs_95pct_neighbor, 0.10% sil / 0.25 RMSE)`), then
  verifies the found cut blind against `18k` at the shared follow camera.

| creature | kind | ref 18k sil / shade | noise ceiling sil / shade | probe 15k sil / shade | found budget | reduction | blind vs 18k |
|---|---|---:|---:|---:|---:|---:|---|
| `creature_1` | quadruped | `0.461%` / `9.282` | `<= 0.561%` / `<= 11.651` | **`0.537%` / `10.371`** | **15,000** | **-16.7%** | `cant_tell` |
| `creature_2` | horned/spined | `0.474%` / `11.771` | `<= 0.574%` / `<= 15.666` | `0.612%` / `13.503` | **17,999** | `0.0%` | `cant_tell` |
| `creature_3` | furred | `0.569%` / `12.609` | `<= 0.668%` / `<= 16.686` | `0.698%` / `14.561` | **17,999** | `0.0%` | `cant_tell` |

- **Blind verification (`budget_finder.py score`, seed `20261120`, independent blind judge before opening `blind.key.json`):**
  **PASS** (`control_ok = true` at `RMSE 0.0`, `planted_ok = true` — judge
  spotted the faceted 1,500-tri + limb-swap bad case cleanly — and
  `claims_cant_tell = 3/3`).
- **Takeaway:** Under the strict 8-frame posed silhouette noise floor, the
  smoother quadruped cuts cleanly to `15,000` tris (`-16.7%`) while fine
  spines (`creature_2`) and fine fur (`creature_3`) exceed the `0.10%`
  silhouette noise floor by `0.03–0.04%` at `15k` and stay at `18k`.

## Round 6 (2026-11-16): measure-only creature bend + blind game-camera check vs decimate

`bench/creature/gamebench.py`, `game_frame.py`, `blind.py`, `gamebench.sh`
(engine untouched): current `retopo` (including Round 4-5 defaults:
`--working-refinement 2`, `--exact-count`, `finer_rerun`, extremity check)
measured head-to-head against Blender `decimate` at 10,000 triangles across
the five generated creatures from the owner's AI corpus, each carrying the
same reference rig (`transfer.py`) and scored on its own range-of-motion
poses (`weights check --no-clips --poses <rom>`). Same-frame smooth-shaded renders at the shared follow camera
(`dense.obj` framing, no wireframes or labels) packed into `OUT/blind/`
with external key `OUT/blind.key.json`.

| creature | tears % retopo / decimate / planted | bend 0-100 retopo / decimate | surface p95 % retopo / decimate / planted | open edges retopo / decimate | blind judge |
|---|---|---|---|---|---|
| 1 (furred biped) | 10.9 / **10.8** / 44.1 | 44.6 / **63.7** | 1.52 / **0.19** / 0.87 | 497 / **0** | decimate |
| 2 (rooted quadruped) | 3.45 / **0.30** / 59.1 | 66.3 / **72.6** | 1.81 / **0.16** / 0.92 | 32 / **0** | decimate |
| 3 (horned biped) | 0.26 / **0.00** / 64.5 | 79.1 / **93.9** | 1.72 / **0.10** / 0.57 | 11 / **0** | decimate |
| 4 (spined quadruped) | 2.43 / **1.36** / 1.57 | 78.1 / **86.6** | 1.38 / **0.17** / 0.72 | 0 / 0 | decimate |
| 5 (mountain boss, biped) | 2.33 / **1.76** / 59.0 | 71.2 / **79.3** | 2.56 / **0.13** / 1.14 | 687 / **0** | decimate |

- **Numeric verdict ([direction.md](direction.md) rule):** `decimate` wins bend tears
  5/5 and 0-100 bend score 5/5; median surface p95 ratio `retopo / decimate`
  is **11.59x** (gate <= 1.10x). Controls hold: can't-tell delta 0, planted
  (1,500-tri cut with swapped L/R limb weights) worse than `decimate` on
  5/5 surface and 5/5 bend.
- **Genuine blind game-camera run (seed `20261115`, 7 unmarked pairs):**
  can't-tell control (`pair_03`, RMSE below header `0.0`) answered
  `cant_tell`; planted bad case (`pair_07`, 64.5% tears) caught; on the
  five creature claim pairs the blind judge picked `decimate` **5/5**
  (100% agreement with both bend tears and surface p95), citing gaping back
  holes (`497` and `687` open edges on creatures 1 and 5), lost horn crowns
  and dorsal spikes, and fused/dissolved fingers and claws on `retopo`.
- **Runtime finding on creature 4:** `island 0` fails coverage across all 4
  attempts while `--exact-count` re-runs the 4 attempts (`1,221 s` at 5k
  quads; `2,143 s` at 6.1k quads with non-monotone count jump `4,067 ->
  7,255` quads), vs `< 1 s` for `decimate`.
- **Mac regression gate (macOS arm64):** `bench/run.py --check bench/baseline.json`
  and `deform_baseline.json` confirmed green across all 12 cases.

## Round 1 (2026-11-13): review, bench, baseline

See [archive/review-2026-11-13.md](archive/review-2026-11-13.md) for the review.

### Baseline: retopo against plain decimate on the owner's creatures

10,000-triangle budget, each creature rigged once by the creature route,
the same weights carried onto every cut. First four creatures (the fifth
and the hand-made animals were still scoring):

| creature | bend retopo / decimate | worst distance % | open edges | normal dev. deg |
|---|---|---|---|---|
| 1 (furred biped) | 49.7 / 68.1 | 5.33 / 1.17 | 14 / 0 | 32.7 / 21.2 |
| 2 (rooted quadruped) | 64.8 / 75.1 | 9.51 / 0.42 | 78 / 0 | 39.4 / 20.3 |
| 3 (horned biped) | 83.0 / 93.3 | 4.44 / 0.38 | 0 / 0 | 25.3 / 12.8 |
| 4 (spined quadruped) | 68.5 / 85.0 | 4.26 / 0.48 | 0 / 0 | 33.1 / 18.8 |

Retopo loses on every one. The pictures show why: single vertices thrown
far off the body (long spikes), small holes, and spines and fur melted
into blobs. Where the surface is plain (the rooted creature's hind leg),
retopo's rings bend cleaner than decimate's slivers, but the checker sees
the spikes and lost shape first. QuadriFlow (Blender) refused three of the
five creatures even on a closed voxel shell; on the one it cut, it scored
72.2 (between the two). Loops at joints are near zero for every tool
(retopo 0 to 0.4 rings per joint), so nothing on the route makes joint
loops today.

Also found: the horned creature is one connected piece, yet retopo split
it into three islands and dropped two (review finding 11, island split by
face winding); the spined quadruped's six "islands" were 3-vertex specks.

Fifth creature (mountain boss, biped): retopo 74.2, QuadriFlow 75.1,
decimate 79.9; surface p95 3.45 / 3.03 / 0.13%.

Hand-made animals (8, artist's rig and mesh as the reference, each tool
at the artist's own count, 576-4,004 triangles): the bend checker scores
the artists' own rigs 3.7-21.9, so on low-poly animals the bend score
says little (the bench-shelf session saw the same on pro characters).
What the lane does show: retopo collapses thin low-poly limbs at these
budgets (surface p95 1.3-17.5%; the fox came back at 180 of 576
triangles), while decimate stays within 0.04-0.23%.

Static props (10, each at its own count): decimate at the own count is the
prop itself (0 error); retopo moved the surface 0.31-11.6% (p95) and lost
parts (ribcage 1,370 of 4,160 triangles); QuadriFlow 0.14-4.6%. Clean props
made directly at target budget should not go through a remesher. Route
decision: [direction.md](direction.md).

### Outside rivals not run this round

Instant Meshes (BSD-3, official 2019 Linux build) and QuadWild+Bi-MDF
(GPL-3, official v0.0.2 Linux binaries) were downloaded locally on a Linux
test host (not committed), but running downloaded binaries was blocked by
permission checks and left for manual review. Tripo's own retopology needs
paid credits: not run.

### Tried: retopo's cut laid back onto the source (bench only, `retopo+lay`)

Each vertex moved to the nearest source point unless that turns a face
over. Bend on the five creatures: 49.7 -> 53.7, 64.8 -> 66.4, 83.0 ->
83.6, 68.5 -> 70.3, 74.2 -> 77.1 (better on all five); mean surface
distance 0.27-0.70% -> lower on all five; surface p95 and worst distance
almost unchanged (1.40 -> 1.37 on the first). So where retopo puts its
vertices costs a little; most of its loss is shape it never captured.
Kept as the reason to build the engine version (`exp/settle`: the same
projection onto the island's input, plus pulling stray vertices home).

### Found: the horns are lost after the working mesh

The mountain boss lost both horns (worst distance 15.5% of its size).
Its working mesh (`RETOPO_DUMP_STAGES`, the isotropic stage's output,
24k triangles) still has both horns and the spines, so they are lost in
rounding or extraction, not in decimation. Two suspects from the review:
one-shot rounding collapsing a thin part (finding 6) and extraction
keeping only the largest piece (finding 8). The second is cheap to test:
`RETOPO_KEEP_PIECES` on `exp/settle`.

Also learned: the long thin lines in the pictures are hair strands in
the Tripo source itself (decimate shows them too), not retopo outliers.

## Round 2 (2026-11-13, running)

New lanes (owner's request for quality benchmarks):

- **pro**: 14 professional rigged meshes (Quaternius UBC male and female,
  knight, eight Quaternius/Khronos animals: CC0; Cesium Man: CC-BY 4.0;
  Blender Studio's Phileas: CC-BY; the owner's MPFB base: CC0), benchmark
  use only. Each is smoothed one level and voxel-fused (0.3% of its size)
  into what a generator gives; every tool remeshes that at the pro's own
  triangle count, and the result is compared with the pro: rings where
  the pro rings (`pro-loops%`), edge flow against the pro's quads
  (`flow`, 0-45 degrees), poles, and bending on the pro's own rig.
- **character**: four generated humanoid characters, each cut by
  every tool, carrying the humanoid rig (fitted onto our cut), then
  given standard humanoid animation clips (`clips_humanoid.glb`)
  and checked on those clips.

Experiments in the queue: settle onto the input (`exp/settle`,
RETOPO_SETTLE), keep detached pieces (RETOPO_KEEP_PIECES), hard joint
rings (`exp/joint-rings`), symmetry snap fix (`exp/symmetry-snap`),
`--skeleton` density, soft ring guides, and the parked patch back end
(`agent/patch-backend`, `--backend patch`), which won worst-case distance
on 10-02 (2.27% against 5.21%+) and is measured here on creatures before
any work to land it.

Not possible this round: QuadWild with forced joint rings (the downloaded
QuadWild binaries may not be run without the owner's say).

### Settle onto the input (engine, `exp/settle`, RETOPO_SETTLE): working, partial

Four creatures so far: bend 49.7 -> 53.7, 64.8 -> 66.4, 83.0 -> 83.6,
68.5 -> 70.2; surface p95 down a little on each. Identical to the
bench-only `retopo+lay`, so the stray-vertex pass found nothing the
plain projection didn't fix on these; the gain is vertex placement.

### Symmetry (`--symmetry x`) and the snap fix (`exp/symmetry-snap`)

The engine accepted the mirror plane on one of five creatures (the horned
one); on the other four it judged them not symmetric enough and ran
plain (identical numbers). On the horned one: old snap bend 85.1, posed
flips 1,021, surface p95 2.06%; bounded mutual snap 84.2, 297, 1.74%;
no symmetry 83.0, 286, 1.72%. The old snap's damage (vertices dragged to
far partners) shows as the flips and the shape error; the fix removes it
and keeps most of symmetry's bend gain. One model only: kept on its
branch until more symmetric subjects (the pro lane's humans) are scored.

### Bench faults found and fixed this round (rule 3: fix the benchmark first)

- **QuadriFlow "refused" creatures and characters; the fault was ours.**
  Blender's QuadriFlow rejects any mesh with an edge shorter than 1e-4
  units, with the message "needs to be manifold". A dense creature one
  unit tall always has such edges. The bench now runs QuadriFlow at 100
  units across and scales back. Round 1's "QuadriFlow refused three of
  five" is withdrawn; re-run in progress.
- **The game's clips had not run on the characters.** The animation
  harness refused the standalone clip file (it holds only a skeleton). The
  clip file is on the same standard skeleton as the rigs, so the clips
  are now copied onto each candidate by bone name
  (`bench/creature/clips_onto.py`, 31 clips) and the weights checker samples
  them. The first character table (below) is on the standard pose set
  only.
- **The pro lane had thrown the pros' quads away.** A GLB stores
  triangles; the first try at pairing them back (Blender's Tris to Quads)
  left half the vertices irregular and found no loops on meshes that
  plainly have them. An exporter writes each quad as two consecutive
  triangles, so pairing neighbours in file order recovers the artist's
  quads exactly: 99-100% quads on the Quaternius humans and Cesium Man,
  98% on Phileas, 86% on the knight. The MPFB base turned out to be a
  decimated copy (half triangles): not a topology truth, removed.
- **The bend checker says little on pro meshes** (it scores the artists'
  own rigs 3-70, median 7). The pro lane's bend measure is now the truth
  itself: every joint is bent the same way on the candidate and on the
  pro mesh (same rig), and the distance between the two bent surfaces is
  the score (`bent-vs-pro%`).
- A mesh lost its parent's transform when unparented (prep and cuts);
  kept now.

### First character table (standard poses only, before the fixes above)

Four of the owner's characters at 14,000 triangles, the human route's
rig: retopo 42.0 / 68.4 / 63.7 / 55.5, decimate 53.6 / 69.4 / 62.1 /
70.2. Decimate ahead on three, retopo by 1.6 on one. Retopo's surface
error (p95) 0.37-1.47% against decimate's 0.04-0.06%. Re-running on the
game's clips with QuadriFlow included.

### Hard joint rings, first version (`exp/joint-rings`): no loops yet

Rings were cut into the working mesh and survive the isotropic stage
(694 ring vertices in, 694 out on a test animal; 14 of 14 rings pinned),
but only about 45% of ring edges got an alignment mark (the feature path
marks an edge only within 10 degrees of the cross field), so each ring is
in pieces and no loop closes (loops at joints 0 on all five creatures).
Bend rose on four of five creatures (median 68.5 -> 73.8) while the
joint picture got messier (more irregular vertices at the ring): score
and picture disagree, and a single run cannot tell a 5-point move from
noise. So: (1) the bend score's spread is being measured (three jittered
seeds of the baseline), (2) version 2 marks every ring edge by its ring
tag. Stays in work.

### `--skeleton` density and symmetry on creatures

`--skeleton` changed one creature of four so far (64.8 -> 66.6). Symmetry
was accepted on one of five. Neither moves the set; both stay available
(they are per-model tools by the owner's rule), nothing to tune yet.

### Instant Meshes (first two creatures)

Bend 26.3 and 44.6 against retopo's 49.7 and 64.8 and decimate's 68.1
and 75.1; surface p95 1.08 and 1.0%. Behind both so far.

## Round 3 (2026-11-13): what should retopo be?

Direction from the orchestrator: a game character or creature is judged
by how it bends at the joints, its silhouette and its triangle budget,
not by how square its faces are. Before more engine work, measure that
and decide the tool's shape.

**Scores** (per candidate, after rigging; `bench/creature/run.py --stages
... after`):

- `tears%`: skin-weights checker headline, the share of the surface inside
  failing findings while the body moves: on the humanoid animation clips for
  generated characters, and on the creature's own range of motion for creatures;
- `outline%`: silhouette error against the download, eight views
  (`silhouette.py`: area where the two outlines differ, as a share of
  the source's outline);
- triangles, held to the budget (10,000 creatures, 14,000 characters).

Quad quality (loops, poles, edge flow) stays in the tables as secondary.

**Candidates**: retopo as it is, Blender's QuadriFlow, Blender's
Decimate (today's route), and each with a joint-ring pass
(`ring_pass.py`): three planes cut across each limb joint (-0.5, 0, +0.5
limb radii), placed from the reference skeleton, trunk joints skipped;
the base cut is made smaller so the ringed mesh lands on the same budget.
On one creature the rings cost 9% of the triangles on retopo's quads and
22% on decimate's triangles before the base is re-cut.

Engine experiments (settle, hard rings v2, keep-pieces, patch back end)
and the wider rival runs were stopped to free the machine; their branches
and partial results stand as logged above.

## Round 5 (2026-11-14): thin parts, density, the floor

Floor (main at b033332a, artist fast set, 3 noise seeds, on Linux; seed 0
matches the Mac run exactly): rings 32.1 / 31.2 / 31.9%, flow 11.8-12.1,
shape p95 median 1.67 / 1.53 / 1.34%.

**Thin separate parts (to-do 1).** Found: the coverage check did fire on
the ribs and the key's pieces (worst gap 50-65% of the part), every
jittered retry failed too, and attempt 0 (a 6-10 quad box) was kept
silently. A single rib remeshed alone holds at about 3x its fair share
of quads and collapses at 1x: the quads are wider than the part.
Engine: `set_finer_rerun` (CLI default on, `--no-finer-rerun`): an
island no retry covered, or one that came out empty, is remeshed again
on its own from its input triangles at 3x, then 6x, its area share of
the target; the first covered run replaces it, else the candidate
nearest the input surface on average (the key's body covers at one
float rounding of its input and boxes at the next: the same part, cut
alone from a 7-digit OBJ, recovers). CLI: the count-matching second run
never wins on count alone when it drops a part the first kept (the
retargeted key run had lost its body entirely). Ribcage: 13 collapsed
ribs now tubes, 6.5 s. Bench (first version, 3x/6x only; 3 seeds vs
the floor): rings 33.0 / 33.0 / 34.2% vs 32.1 / 31.2 / 31.9; only three
cases change, all better: ribcage rings 19-30 -> 38-56%, shape p95 5-8
-> 2.1-3.5%; key shape 7.9-9.4 -> 2.0-3.0%; cauldron 3.1-3.7 -> 2.8-3.0%.
Counts: ribcage 0.56-0.69x -> 1.16-1.53x of the artist's (the floor was
short because the ribs were gone). The femur is a different fault: no island fails;
its ends come out 7% short (the ball and condyles rounded off at ~2.5
quads across).

**Density where artists put it (to-do 2).** New bench score `dens_err`
(score.py): each cut face's area against the artist's face under it,
log2, median taken off, area-weighted mean (artist 0; ours 0.9-1.5 on
heads and hand, QuadWild 0.57-1.44). On the realistic head our face
quads are ~5x too large relative to the artist's, ears, neck and the
back too dense: curvature adaptivity puts quads on the ears, the artist
on the face. Found: the engine barely realizes any density field: a 4x
`--density` mask on the face front moved its density ~1.2x, with
adaptivity off too, and forced dipoles (`--dipole-ratio 1.5
--dipole-every 1`) changed nothing. Why: per-face scale enters only the
least-squares cover; the cross field, which places the poles a size
change needs, never sees it, so the solve averages the size out.

**Adaptivity sweep** (curvature density, with the first re-run version;
3 seeds; rings mean / shape p95 median): 0 -> 23.5% / 2.8; 0.25 -> 29.2
/ 2.0; 0.5 -> 31.6 / 1.3; 0.75 -> 31.9 / 1.3; 1.0 (default) -> 33.4 /
1.5. Default kept: it wins the headline; lower values trade rings for
shape. Adaptivity 0 keeps the femur's ends but drops fingers.

**Femur's ends**: curvature adaptivity gives a ball quads sized to its
radius, so its cap is a couple of quads across and comes out 6% short
of the input. New extremity check (see [archive/coverage-retry.md](archive/coverage-retry.md)):
input vertices past the output by half a quad width along any of 13
directions fail the verdict. On the floor outputs every bad-shape case
receded 0.58-2.1 widths, every healthy organic case 0.02-0.25. First
look on six cases (shape p95, before -> after): anvil 7.1 -> 1.6, femur
9.0 -> 1.0, axe 3.0 -> 0.5 (but 2.1x the quads: its blade re-runs 3x
denser), scapula 2.4 -> 1.8, generic head 2.1 -> 1.5, sword 1.0 -> 1.1.
Re-runs now aim again when the engine's yield overshoots (the axe's
first try landed at 9x), and only parts under half the mesh re-run.

Gate floor (Mac, main): `cli_contract` was already red: the
`remesh-nasty-nonmanifold` golden went stale with f6444713 (coverage
retry now lands on retry 3 with 164 quads, golden says retry 1 / 157).

**Game-character matrix (to-do 3, re-run).** The round-4 run was void
because the Linux test checkout had fallen behind (stuck at 4435846b);
this one ran from a snapshot of this tree into a fresh Linux work copy,
the thin-parts engine before its last two fixes. Four generated characters
at 14k triangles; tears on standard humanoid clips (skin-weights headline,
% of surface; lower is better) / silhouette error %:

| character | ours | ours+rings | QuadWild | QuadriFlow | decimate |
|---|---|---|---|---|---|
| character 1 | 5.43 / 5.2 | **4.82** / 6.8 | no cut | 5.69 / 2.8 | 5.42 / 0.3 |
| character 2 | **0.81** / 3.3 | 1.05 / 3.3 | 1.48 / 3.2 (18k) | no cut | 1.25 / 0.4 |
| character 3 (coat off) | **1.60** / 3.2 | 1.77 / 3.1 | 1.67 / 3.3 (19k) | 1.84 / 2.9 | 1.97 / 0.4 |
| character 4 | **3.39** / 7.7 | 3.97 / 8.2 | 3.71 / 4.7 (36k) | 4.26 / 5.5 | 3.68 / 0.6 |

Ours tears least on all four (with rings on character 1); the joint-ring pass
helps only character 1. Ours loses on outline: 3-8% against decimate's
0.3-0.6% and QuadriFlow's 2.8-5.5% (character 4 worst). `table.py` crashed on a
score row without `cand` (QuadWild's failed cut); fix next round.

**Final engine, artist fast set** (3 seeds on Linux, vs the floor; QuadWild
for scale): rings 35.6 / 32.6 / 35.9%, mean **34.7** (floor 31.7,
QuadWild 32.2); flow 12.0 deg (floor 11.9, QuadWild 13.1); shape p95
mean **1.44%** (floor 2.79, QuadWild 0.82), median 1.02 / 1.00 / 0.90
(floor 1.67 / 1.53 / 1.34); count 1.16x the artist's (floor 0.97x,
QuadWild 1.19x). Shape fixes: anvil 10.6 -> 2.0, femur 8.8 -> 0.9,
ribcage 6.6 -> 2.0 (rings 25 -> 55%), axe 2.8 -> 0.6, cauldron 3.5 ->
0.8. Cost: quads over budget on the thin-part cases (axe 2.15x, key 1.7x,
cauldron 1.6x, ribcage 1.5x, femur 1.3x), part of their rings gain is
those quads; bench time ~65 s -> ~210 s. Still wrong: the key's curled
side loops (non-manifold junctions) vanish (shape p95 ~7.7).
Two fixes after the first bench: the fallback picks the re-run with the
lowest 95th-percentile gap (a lost region barely moves the mean), and
a re-run never returns fewer quads than the island had (the CLI golden's
two tetrahedra went 96 -> 35 without it); the extremity check needs 10
input verts past the output, since a sharp tip no quad reaches is one or
two verts and a lost end is a region.

**Gates (Mac).** cargo fmt and every cargo test green (goldens
regenerated: --help, nasty-floaters keeps both islands, a tetra LOD 50 ->
74 of 80; the single tetra at 50 keeps its 12-quad first run because the
37-quad count-matched run left a coverage shortfall; grid-density was
stale on main already). Deform baseline re-pinned (8 seeds): the
fold-through-bbox proxy moves beyond the seed spread on armadillo
(flips 91 -> 112 small, 244 -> 307 tiny), nefertiti (17 -> 28) and
fandisk (small shifts); the proxy is the old bbox one, and the tears
test on the game clips (the measure that matters) improved. run.py:
baseline.json re-pinned on macOS arm64: every count is the same or nearer its target (nefertiti tiny 1059 -> 997 and small 5322 -> 5219, dragon tiny 885 -> 948, armadillo small 5097 -> 5128 of 5000); `--check` passes against it.

## Round 4 (2026-11-13): the artist bench and the fast loop

Owner's goal restated: a general quad remesher better than the paid tool,
proven against the free rivals. Headline: **rings the artist has** (share
of a hand-made mesh's closed edge loops the remesh also has), given only
the shape. Fast set: 20 hand-made meshes (Blender's CC0 base meshes:
heads, hands, feet, eye, bones; Quaternius CC0 props), `bench/artist/`,
about a minute a run on this Mac. Noise floor from jittered seeds: rings
about +-3 points, shape p95 about +-0.4%.

| idea | rings % | flow deg | shape p95 % | worst % | odd/model | verdict |
|---|---|---|---|---|---|---|
| baseline (count-matched) | 22.5 | 13.6 | 1.97 | 4.96 | 3.6 | - |
| settle onto input | +2 | same | -0.08 | same | same | gain gone once the mesh below is finer; left on `exp/settle` |
| keep detached pieces | same | same | same | same | +1 | no effect; left on `exp/settle` |
| finer working mesh (2x) | 30.3 | 11.9 | 2.09 | 4.0 | 2.2 | **kept** (CLI default `--working-refinement 2`) |
| odd faces paired into quads | same | same | same | same | 5.3 -> 2.2 | **kept** (default; `--keep-odd-faces`) |
| second run when count misses 8% | fair counts | | | | | **kept** (default; `--no-exact-count`); without it the finer mesh overshot hard pieces 2.5x |
| coverage bar from expected quads | +1 on every seed | | -0.4 (seed 0) | 3.8 | | **kept** |

Lesson: an earlier "win" came from overshooting the quad count on hard
pieces; every comparison is now count-matched.

Rivals on the same 20 (now ours = last row): QuadWild rings 32.2%, flow
13.1, poles 0.65x the artist's, odd 0, shape p95 0.72%, count 1.19x;
QuadriFlow rings 3.5%, flow 15.4, 3 of 20 silently not remeshed. Ours:
rings 32.1%, flow 11.8 (best), poles 0.96x, odd 2.2, shape 1.67%.
Whole male body (10.5k quads): ours rings 40% / flow 9.5 / shape p95
0.06%; QuadWild 23% / 12.0 / 0.17%; QuadriFlow 0.3% / 14.1 / 0.36%.

Where we lose: thin separate parts (key bow, ribcage ribs, femur ball)
still collapse; QuadWild keeps them (shape 0.7 vs 1.7%). Artists put
far more quads in the face and fewer on limbs; every tool is uniform.
Next: a fallback for islands whose retries all fail; density where
curvature and features are; poles at QuadWild's level.

Game-character matrix (rings pass, tears on clips): its first run used a
stale checkout on the Linux test host and is void; that host went offline before the re-run.
