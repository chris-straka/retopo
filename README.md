# retopo

`retopo` (Blender UI: *RetopoForge*) is a fork of
[AutoRemesher](https://github.com/huxingyi/autoremesher) (`https://github.com/huxingyi/autoremesher`,
MIT, by Jeremy HU) restructured around a **headless engine**: a pure Rust
core library (`retopo_core` + `retopo_solvers`), the `retopo` CLI, a
Blender extension, and a benchmark/regression harness. There is no desktop
app — Blender is the UI. The engine was ported **1:1 from the original
C++**, proven by differential oracles, and the C++ tree was then removed
(see [docs/rust-switch-verdict.md](docs/rust-switch-verdict.md); git
history keeps it). Upstream is kept as the `upstream` git remote as a
read-only reference; this fork has structurally diverged, so upstream
engine fixes are ported by hand when relevant, never git-merged.

## Status & Purpose (`retopo` vs `bodywrap` vs `decimate`)

**Status: parked (owner decision, 2026-11-18).** `retopo` remains parked for
automated monster work and is not a general hero blocker. No automated game
route calls `retopo` today; monster assets currently go through
`motion/tools/tripo_rig.py` + `motion/tools/tripo_to_glb.py`, which runs a
4-stage hole-repair + decimate pipeline (`untangle` / `mend_holes` /
`cap_rims` / `close_rims_in_file` around Blender's `DECIMATE`) and passes
the closed mesh to `skintokens`. For broken/Tripo-style inputs inside this
repo, `bench/creature/cut.py` (`decimate-repaired` / `--repair`) now bridges
that same 4-stage pipeline via `bench/creature/hole_repair.py`.

How the three mesh tools split the work ([docs/direction.md](docs/direction.md)):

| Tool | Owns | Why |
|---|---|---|
| **`bodywrap`** (`wrapforge`, `~/Games/_blender/bodywrap`) | Humanoids (including humanoid monsters: goblins, orcs, skeletons), creature families with an existing base, and piece styles reused 3+ times (rule of three). | Wraps a clean, already-rigged base with proper face/hand loops and weights onto the generated shape. |
| **`retopo`** (this repo; **parked for automated creatures**) | **One-off monsters and bosses** (6 arms, spiders, tentacles, slimes), **one-off attached pieces** (gear, armor, capes, hair, belts), and **building new base meshes** by hand (guides, symmetry, density, cleaned in Blender) for `bodywrap` to reuse. | Handles topology that fits **no** base body. Never used for HLH props (kit props are built at budget; generated props go through `meshkit`'s `clean_copy.py`). |
| **Blender `decimate`** (`motion/tools/tripo_to_glb.py`) | Current production monster cutter while `retopo` is parked. | Runs in `< 1 s` with `0.10–0.19%` surface p95, and `tripo_to_glb.py` closes open rims around the cut. |

## Why Exoside Quad Remesher handles monsters well (and what gap exists)

Exoside Quad Remesher (built on ZRemesher / Decimation Master technology)
handles complex monsters and creased sculpts cleanly out of the box
primarily because of its **whole edge-loop guide system** and local sizing
controls (see [docs/exoside-gap.md](docs/exoside-gap.md)):

- **Auto hard-edge & crease guide loops:** Exoside automatically detects
  hard edges by angle (~30°+) and converts normals splitting/creasing,
  smoothing groups, material boundaries, and polygon groups into real quad
  edge loops that hold silhouette and fold cleanly. In `retopo`:
  - `--sharp-edge` inspects dihedral angles on the coarse working mesh
    (a thin limb 4 edges around turns into an over-constrained cage;
    [docs/review-2026-11-13.md](docs/review-2026-11-13.md)),
  - `--guides` only steers the cross field's tangent without pinning quad
    edges onto joint loops, and `--features` aligns (`AlignU/AlignV`)
    without automatic angle-detected guide loops.
- **Symmetry:** Exoside mirrors layout cleanly across a plane. `retopo`'s
  `--symmetry auto|x|y|z` mirrors the cross field and snaps vertex
  positions (without mirroring the integer UV layout; unbounded nearest
  matching can collapse differing left/right roundings).
- **Density painting:** Exoside's vertex-color density painting (0.25x–4x)
  controls local quad density strongly, and its adaptive quad count toggle
  lets curves buy extra quads without starving flat areas. `retopo` has
  `--density` (plus `--skeleton` and automatic `--dipoles`), but per-face
  scale enters only the least-squares cover — not the cross field that
  places singularities — so strong localized asks saturate (~2.3x max;
  ~1.2x realized on heads; [docs/log.md](docs/log.md) Round 5).
- **Keep-Materials & attribute survival:** Exoside preserves material
  slots across the remesh. `retopo`'s Blender round-trip exports bare temp
  OBJs and drops UVs, vertex colors, and material slots (compensated
  afterward via *Bake Assist*: High-to-Low PBR bake, `Project HIGH UVs`,
  `Transfer HIGH Colors`, or CLI `--uvs on`).
- **Where `retopo` leads:** free, MIT core (`retopo_core`,
  `retopo_solvers`, CLI) + GPL-3.0-or-later Blender add-on, headless/CI-safe
  batch and `--lods` workflows, and zero per-seat activation or EULA bans on
  ML/competing-product work.

## Measured current state

- **Round 6 (`docs/log.md`, 2026-11-16 — 5 dev creatures at 10,000 tris vs Blender `decimate`):**
  - `decimate` won **5/5** on bend tears (`0.00–10.8%` vs `0.26–10.9%`),
    **5/5** on 0–100 bend score (`63.7–93.9` vs `44.6–79.1`), and **5/5**
    with the blind game-camera judge (`seed 20261115`, `cant_tell` control
    and planted bad case both valid).
  - Median surface p95 ratio (`retopo / decimate`) was **11.59x** worse
    (`1.38–2.56%` vs `0.10–0.19%`, gate `<= 1.10x`).
  - Mesh faults: **497** and **687** open edges (gaping back holes) on
    creatures 1 and 5 (`32` and `11` on creatures 2 and 3), melted horn
    crowns, claws, and dorsal spikes, dissolved fingers, and split/dropped
    islands.
  - Runtime: creature 4 took **1,221 s** (5k quads) to **2,143 s** (6.1k
    quads) when `island 0` failed coverage across all 4 attempts and
    `--exact-count` re-ran them, vs `< 1 s` for `decimate`.
  - Static Mac regression gate (`bench/run.py --check bench/baseline.json`
    + `deform_baseline.json`) remained green across all 12 cases.
- **`retopo-2` & `retopo-7` (`docs/log.md`, 2026-11-20 — frozen 3 held-out creatures + budget finder):**
  - Froze 3 non-overlapping backlog creatures (quadruped 83.6k,
    horned/spined 94.4k, furred 96.4k) via SHA-256 manifest in
    `bench/creature/heldout.json` (enforced by `bench/creature/gamebench.py`).
  - `bench/creature/budget_finder.py` (`retopo-7`, using `retopo-3`'s
    8-frame posed game-camera silhouette/shading check) cut `creature_1`
    (quadruped) cleanly to **15,000 tris (-16.7%)** within decimate's noise
    floor while holding `creature_2` (horned/spined) and `creature_3`
    (furred) at **17,999 tris (0.0%)**; independent blind judging passed
    (`cant_tell` 3/3, control & planted valid).
- **`meshkit-1` valid `span/700` runs (`0a15ddc3`, 2026-11-21 — `clean-copy` vs `decimate` at 10,000 tris):**
  - **Dev 5 (`kevwqp8w`, `controls.valid=true`):** `clean-copy` closed holes
    `0` vs `0`, won bend `1/4` (`decimate` 4), lost surface closeness `0/5`
    (`median_surface_ratio = 1.2428x` vs `<= 1.10x` gate); blind PASS
    (`clean-copy` 1, `decimate` 2, `cant_tell` 2; `creature_1` left-forearm
    shading artifact).
  - **Held-out 3 (`k34exqgb`, `controls.valid=true`):** `clean-copy` sealed
    all holes (**`0` vs `2/22/29` holes / `9/116/110` open edges** left by
    raw `cut.py decimate`), won bend **`2/3`**, lost surface closeness
    `0/3` (`median_surface_ratio = 1.4684x`); blind PASS (`clean-copy` 1,
    `decimate` 0, `cant_tell` 2).
  - Note: raw `bench/creature/cut.py decimate` runs bare `DECIMATE` directly
    on `dense.obj` without hole repair so the bench isolates cut topology;
    `cut.py decimate-repaired` (or `--repair` with `--holes SHARE` and
    `--report R.json`) bridges `motion/tools/tripo_to_glb.py`'s 4-stage
    `untangle`/`mend_holes`/`cap_rims`/`close_rims_in_obj` pipeline around
    `DECIMATE` via `bench/creature/hole_repair.py`.

## Unpark roadmap (ready for Opus 5.5 review)

Re-entering `retopo` on the creature route is gated by `~/Games/RETO.md`
Gates 1–8 (`bench/creature/gamebench.py` on both the 5 dev and 3 held-out
creatures: beat decimate on bend tears on most creatures, median surface
p95 `<= 1.10x`, zero open edges on closed inputs, `< 5 min` runtime, and
blind game-camera pass). Two tracks are ready for Opus 5.5's review:

1. **6 AutoRemesher engine faults (`~/Games/RETO.md`, in order) + hard-edge guides ([docs/exoside-gap.md](docs/exoside-gap.md)):**
   1. Stray vertices thrown off the body (spikes).
   2. Open holes on closed inputs (497 / 687 open edges on creatures 1 & 5).
   3. Connected parts split into islands and dropped.
   4. Horns, spines, and claws melted into blobs (one-shot MILS rounding,
      coarse working-mesh projection, extremity starvation).
   5. Missing joint loops (pinned integer edge rings instead of field
      steering alone) + automatic hard-edge/crease guide loops, layout
      symmetry, and cross-field-coupled density painting.
   6. Runtime (cut 20–35 min worst-case retries down under 5 min on a fleet
      machine).
2. **Low-cost bend test (`retopo-4`, `~/Games/_tools/docs/plans/retopo.md`):**
   - One stop-ruled experiment (`cut.py decimate-weighted`) using Blender
     `DECIMATE`'s `vertex_group` biased toward joint rings and thin
     horns/claws/spines (and away from unseen faces) to test whether
     joint-weighted decimation beats plain decimate on bend tears before
     committing to the full quad-remesher overhaul.

## Install (Homebrew)

```bash
brew tap chris-straka/retopo https://github.com/chris-straka/retopo
brew install chris-straka/retopo/retopo
```

This taps the repo itself (the formula lives in `Formula/retopo.rb`) and
builds the `retopo` CLI from source (rust is pulled in automatically),
linking it onto your PATH as `retopo`. `brew update` keeps the tap
current; `brew upgrade retopo` rebuilds on updates.

## Build from source (cargo)

```bash
brew install rust
cargo build --locked --release -p retopo
```

Run it from `rust/` (the cargo workspace). This builds the `retopo` CLI
(`rust/target/release/retopo`); pure Rust, no C++ (the vendored
meshoptimizer was replaced by the native `retopo_core::decimator`). Compiles
on macOS and Linux; the bitwise oracle and golden tests are pinned on macOS
arm64 (see [AGENTS.md](AGENTS.md)).

## CLI usage

```bash
./rust/target/release/retopo --help
./rust/target/release/retopo --input bench/models/armadillo.obj \
    --output /tmp/armadillo-remeshed.obj --report /tmp/armadillo-report.txt \
    --target-quads 5000
```

Flags: `--input`/`-i` and `--output`/`-o` (required), `--report`,
`--target-quads` (default 50000), `--edge-scaling` (1.0–4.0),
`--working-refinement` (1.0–4.0, default 2.0: the working mesh is twice
as fine as the output, so small features survive; 1.0 is the old engine),
`--keep-odd-faces` (by default the triangles and pentagons left by
extraction are paired and re-cut into quads), `--no-exact-count` (by
default a run that misses `--target-quads` by more than 8% is repeated
once, aimed at the miss), `--no-finer-rerun` (by default a separate
part that no coverage retry covered, such as a rib or a key's bow that
collapsed to a box, is remeshed again on its own 3x, then 6x, denser,
and an output that stops short of an end of the input by half a quad
retries),
`--sharp-edge` (30–180°), `--smooth-normal` (0–180°),
`--adaptivity`/`--anisotropy` (0–1), `--model-type organic|hardsurface`,
`--symmetry off|auto|x|y|z` (default `off`), `--guides <file>`,
`--density <file>`, `--features <file>`, `--skeleton <file>`,
`--dipoles off|auto` (default `auto`), `--dipole-every <count>` (expert
stride override, default `0` = auto), `--dipole-ratio <value>` (expert
step-sharpness override, default `0` = auto `1.5`), `--uvs [on|off]`
(default `off`, bare `--uvs` means on),
`--lods <q0,q1,...>`,
`--quiet`/`-q`, `--verbose`, `--help`/`-h` (`--help --all` for expert
flag detail), `--version`/`-v`.
Non-indexed triangle soup is
welded on load; `--quiet` silences progress output (warnings, errors, and
the report still print), `--verbose` adds the phase-timing table and
engine diagnostics on stderr (mutually exclusive with `--quiet`).
Progress, warnings, errors, and the closing `done:` line go to stderr;
stdout carries only results (the report block, `FILE`/`LOD` rung lines).
`--symmetry auto` detects the dominant mirror
plane (x/y/z pin it) and falls back to unconstrained output when the
input scores below threshold. `--guides` takes a polyline file (one
`x y z` point per line, blank lines separate polylines, `#` comments)
and bends quad edge flow along the curves; single-file and `--lods`
runs only. `--density` takes a mask file (one multiplier per input
vertex, `1.0` = unchanged, clamped to 0.25–4.0) for local detail
control; strong localized refinement saturates (~2.3x realized for
4x asks), mild masks realize nearly fully; single-file and `--lods`
runs only. `--features` takes a polyline file in the same format as
`--guides` and marks crisp hard-surface edges (wins ties over guides);
single-file and `--lods` runs only. `--skeleton` takes the rig (one
`hx hy hz tx ty tz parent bend_degrees` bone per line, parent = earlier
line index or -1) and sizes the edges at creases between sibling limbs
(see Skeleton density); it multiplies with `--density`; single-file and
`--lods` runs only. `--dipoles auto` (default) inserts dipole rings along
sharp `--density` steps; `--dipole-every` and `--dipole-ratio` override
its stride and sharpness threshold. `--uvs on` emits remeshed UVs from
the internal parameterization (`vt` + `v/vt` corners for OBJ, `TEXCOORD_0`
for GLB), normalized 0..1 per island. `--input`/`--output` accept `.glb`
as well as `.obj` (positions + faces; batch dirs and `--lods` chains keep
each file's extension). The input model comes from
`bench/fetch_models.sh` (see Benchmarks).

Multi-output: `--lods 10000,5000,2000` emits a full LOD chain in one run
(`<stem>_lod0.obj`, `<stem>_lod1.obj`, ... next to `--output`, overriding
`--target-quads`); pointing `--input` at a directory remeshes every
`.obj` and `.glb` in it (non-recursive) with `--output` as the
directory:

```bash
./rust/target/release/retopo --input bench/models/armadillo.obj --output /tmp/hero.obj --lods 10000,5000,2000
./rust/target/release/retopo --input assets/ --output assets-retopo/
```

Exit codes: 0 success, 2 usage error (bad flags, missing options,
unreadable inputs — nothing runs), 1 runtime failure (an input failed
to load or remesh). Inputs are validated before any work starts, so a
bad flag or missing file fails fast without touching outputs.
Out-of-range numerics clamp to their documented range with a
`retopo: warning:` rather than failing.

## Tests

```bash
cd rust && cargo fmt --all --check && cargo test --locked --release
python3 bench/creature/test_gamebench.py
```

Module goldens replay committed C++ reference dumps
(`tests/fixtures/*_diff.txt`, recorded before the C++ tree was removed)
against every engine component. The CLI contract test
(`rust/cli/tests/cli_contract.rs`) drives the built binary over 52
arg-matrix + 20 IO-failure + 37 remesh cases against goldens in
`tests/fixtures/cli_golden/`: exact output for parser and IO paths;
run-to-run determinism plus counts within 0.5% + 8 for remeshes.
Creature bench unit tests and hole-repair checks live in
`bench/creature/test_*.py` (`test_gamebench.py`, `test_budget_finder.py`,
`test_clean_copy_cut.py`, `test_hole_repair_cut.py`,
`test_posed_image_check.py`, `test_harness_fixes.py`), alongside
`bench/creature/retopo4_bench.py`. After
an intended CLI or engine change, regenerate and review the diff:

```bash
UPDATE_GOLDENS=1 cargo test --release -p retopo --test cli_contract
```

## Benchmarks

```bash
bench/fetch_models.sh          # one-time download of test models (gitignored)
bench/run.py                   # run suite, validate meshes, save results JSON
bench/run.py --check bench/baseline.json   # fail on regression vs baseline
bench/profile.py               # profile one production-size mesh (docs/perf.md)
bench/deform.py --check bench/deform_baseline.json  # deformation gate alone
bench/creature/gamebench.sh    # creature bend + surface + blind game-camera bench vs decimate (bench/creature/README.md)
bench/creature/gamebench.py    # neutral A vs B creature bench (--tools A,B, --heldout bench/creature/heldout.json)
bench/creature/budget_finder.py  # 8-frame posed game-camera triangle budget finder (retopo-7)
bench/rings.py --mesh subject.obj --landmarks lm.json \
  --joints elbow,knee --output guides.txt  # joint rings -> --guides file
```

The suite runs `rust/target/release/retopo` over five models × two presets
(override with `bench/run.py --binary`)
(`--target-quads` 1000/5000), validates every output mesh, and records
timings plus quad counts. A run regresses when it exits non-zero, its
mesh fails validation, its quad count drops >5% below baseline, its
non-quad share rises >2pp, or its deformation scores (joint p95
stretch, volume loss, flips vs `bench/deform_baseline.json`) regress;
wall time is recorded but never gates.
`bench/profile.py` profiles a single mesh instead: wall time, peak RSS,
and the engine's per-phase breakdown — see
[docs/perf.md](docs/perf.md). The shipped LOD rung strategy is
[docs/lod-strategy.md](docs/lod-strategy.md).

## Blender extension

Quad remeshing inside Blender 4.2+, driven by the `retopo` CLI: select
mesh objects, open the *RetopoForge* tab in the 3D Viewport sidebar
(N-panel), tune the parameters, hit **Remesh Selected**. Each object is
exported to a temp OBJ under the identity transform, the CLI remeshes
it, and the result lands back on the original object in a single undo
step; temp files are removed afterwards. New topology cannot carry UVs
or vertex colors — the panel says so. The panel also offers **Generate
LODs** (one `--lods` chain per selected object from the comma-separated
rung field, each rung imported as a `<object>_lod<N>` sibling), per-object
settings recall (each remesh saves its parameters; the next run on the
same object restores them), **Flow Guides**, **Sharp Features**,
**Density Mask** / **Skeleton Density**, and a **Bake Assist** box
(high-to-low PBR bake, `Remesh + Bake All`, `Project HIGH UVs`, and
`Transfer HIGH Colors`).

Install:

1. Build the CLI (see Build above).
2. Zip the `blender/retopo/` directory (the folder containing
   `blender_manifest.toml`):

   ```bash
   (cd blender && zip -r /tmp/retopo-addon.zip retopo -x '*/__pycache__/*')
   ```

3. In Blender: *Edit → Preferences → Extensions → Install from Disk*,
   pick the zip, enable *RetopoForge* (extension v0.3.0).
4. If the `retopo` binary is not on `PATH`, set its location in the
   add-on preferences (the *Retopo CLI* path — the panel header shows
   whether the binary was found). The panel's **Reload Scripts** button
   picks up extension updates without restarting Blender.

Headless test (uses the Blender app binary directly — the
`~/.local/bin/blender` shim has a broken Python environment):

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background \
    --factory-startup --python blender/tests/test_headless.py
```

The test registers the extension, remeshes a transformed subdivided
cube, and asserts the mesh was replaced, is mostly quads, keeps the
object transform bit-exact, records a report, and leaves no temp
objects. It skips (exit 0) when no `retopo` binary is available.

### assetgen adapter (repair-topology)

assetgen's `gen character` chain rebuilds a character that fails its
budget check (P_VERTS, P_TRIS, P_TEX_SIZE, P_TEX_NORMAL) with

```bash
tools/genforge_adapter.sh repair-topology IN.glb OUT.glb RESULT.json \
    [--class humanoid|quadruped|custom] [--guides FILE] [--features FILE]
```

`--guides` and `--features` are passed straight to `retopo` (same polyline
format as above). assetgen sends the guide file an owner attached with
`gen character guides <asset> FILE`, and the adapter logs the exact `retopo`
command it ran. When drawing guides in Blender, import the GLB with
"Guess Original Bind Pose" off, or the mesh shifts and the guides miss.

Each skinned mesh over budget (or without a normal map) is remeshed by
the installed `retopo` CLI to a target from the class budget (rfcheck's
hero 10k verts / 15k tris / 1024 px; monster 12k / 20k / 1024),
smart-UV'd, baked (color + normal from the original, Cycles CPU), and
reskinned from the original (nearest-face weight transfer, 4
influences, normalized); the remesh gets the input's armature as
`--skeleton` (see Skeleton density), and the adapter runs this
checkout's `rust/target/release/retopo` (or `$RETOPO_BIN`) before the
one on PATH, since an older installed CLI lacks the flag; the armature, bone names and clips are kept
(glTF import without bind-pose guessing, so a translated root node
round-trips exactly). Textures over budget on untouched meshes are
scaled down. `RESULT.json` follows assetgen's adapter schema
(`{"ok", "outputs", "tool": "retopo", ...}`; exit 0 within budget,
1 still over or a step failed, 2 error with no result). Test:

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background \
    --factory-startup --python blender/tests/test_genforge_adapter.py
```

Measured 2026-10-05 on a real 22-bone, 9-clip textured character from
the owner's AI corpus: 10,395 -> 6,657 glTF verts (12,616 -> 11,074
tris; 6,632 on an earlier run: the tiling varies run to run), 2048 px
albedo -> 1024 px color + normal, rfcheck hero-class
clean, 6 s on an M4; turntable matches the original's colors.

The extension is GPL-3.0-or-later, as Blender requires; the Rust engine
stays MIT — the extension talks to it only as a subprocess over OBJ
files. See [docs/architecture.md](docs/architecture.md) and
[blender/README.md](blender/README.md).

## Skeleton density (`--skeleton`)

Deformation-aware density for rigged characters. The skin weights a
rigger or weight fixer produces blend across a joint over a roughly
fixed number of edge rings (SkinTokens' transition and meshweights'
repair bands are both about four rings), so the blend's width in space
is that ring count times the local edge length. Where a limb's crease
runs deep, the blend is then too narrow for the sweep and the crease
edges tear. Measured on a 2 m humanoid from the owner's AI corpus
(2026-10-05; SkinTokens rig, motionforge standardize, `weights fix`,
then the meshweights gate, three remesh targets each):

| groin topology | thigh stretch fails | score after fix |
|---|---|---|
| default | 3 of 3 runs (5-11 verts) | 72.6-73.2 |
| 1.6x more quads everywhere* | yes (27 verts) | 67.5 |
| denser groin and armpits (x4 zones)* | yes, fix refused | 41.6 |
| `--skeleton` (groin edges 1.2-1.3x longer) | 1 of 3 (3 verts) | 75.7-78.0 |

(*one run each, on the earlier Python SkinTokens runtime, whose
default-topology runs scored the same 69-73.)

More rings at the groin made it worse; wider, even rings fixed it.
Coarsening every limb root (armpits too) moved the failure into
collapsed arm volume and cost a test mannequin 6 points, so the rule
only sizes the crease between **sibling limbs** (two legs under one
pelvis, four legs under one body), where one limb swings against a
still neighbour:

1. For each bone whose parent has another child, slice the mesh
   perpendicular to the bone, stepping from its pivot toward its tail,
   until the loop around the bone holds no surface nearer to a sibling
   limb than to this one. That distance is the crease depth (none when
   the limb separates within its own radius of the pivot, or never).
2. The crease asks for edges no shorter than
   `bend * depth / ((2.5 - 1) * 4)` (radians; 2.5 = the gate's per-edge
   stretch, 4 = blend rings). Where the nominal quad edge
   (`sqrt(area / target)`) is shorter, a sphere around the pivot
   (radius 2x the depth, full strength over the inner half) gets
   density `(nominal / wanted)^2`, floored at 0.25. The density
   pipeline keeps the total quad budget.

Everything comes from bone lengths, the hierarchy, bend ranges and the
mesh; nothing is in units or tuned per character. On the meshweights
mannequin (legs leave the body at the hip) no crease qualifies and the
output is byte-identical; on a rigged quadruped the hind legs ask for
x0.77 and its same-rig scores stay level (31.4-33.8 off, 31.8-34.7 on).
`--verbose` lists each crease and its ask. The Blender extension writes
the file from the object's armature (Density panel, *Skeleton Density*;
bend ranges from bone-name roles, 45 degrees otherwise) and assetgen's
repair-topology adapter turns it on. Limits: limb-to-trunk creases
(armpits) are left alone, and the humanoid still fails one small arm
finding in each run (arm_forward volume or arm_up stretch, 4-10 verts);
that is a weights or corrective-shape job, not topology.

## Layout

- `rust/` — the product: `retopo_core` + `retopo_solvers` libraries and
  the `retopo` CLI binary (`rust/cli`), ported from the original C++ by
  differential oracles (see [docs/rust-switch-verdict.md](docs/rust-switch-verdict.md)).
- `Formula/` — Homebrew formula (`retopo.rb`) for the CLI.
- `blender/` — Blender extension (`blender/retopo/`) driving the
  CLI over a temp-OBJ round-trip, with headless tests in
  `blender/tests/`.
- `tools/` — `assetgen` adapter (`genforge_adapter.sh`, `genforge_adapter.py`).
- `bench/` — static regression harness (`bench/run.py`, models/results
  gitignored, `bench/baseline.json` committed), `bench/profile.py`
  profiler, `bench/score.py` scorecard, `bench/noise.py` noise-floor
  harness, and `bench/creature/` (the posed game-camera creature bench,
  frozen held-out manifest `heldout.json`, `budget_finder.py`, and
  `test_*.py` unit tests).
- `gates/` — gated verification specs and scripts (`retopo-1`, `retopo-3`,
  `retopo-4`, `retopo-8`, `meshkit-1`).
- `docs/` — [direction.md](docs/direction.md), [log.md](docs/log.md),
  [review-2026-11-13.md](docs/review-2026-11-13.md),
  [exoside-gap.md](docs/exoside-gap.md), [architecture.md](docs/architecture.md),
  [perf.md](docs/perf.md), and [lod-strategy.md](docs/lod-strategy.md).
- `tests/fixtures/` — golden data for the Rust tests (reference dumps,
  procedural OBJ/GLB fixtures, CLI contract goldens).

See [docs/architecture.md](docs/architecture.md) for the module graph
and the engine/CLI/addon split.

## Direction & History

1. Headless engine + CLI + benchmarks (done).
2. Blender addon driving the CLI (done, extension v0.3.0).
3. Rust rewrite of the engine + CLI, proven by differential oracles
   (done — shipped in 0.3.0, C++ tree removed; see
   [docs/rust-switch-verdict.md](docs/rust-switch-verdict.md)).
4. Post-switch work landed on `main`: native Rust decimator, residual
   route cleanup, scale normalization, single-island parallelism,
   `--working-refinement 2`, `--uvs`, `--guides`/`--features`/`--density`/
   `--dipoles`/`--skeleton`, CLI UX redesign, and the portable posed
   creature bench (`bench/creature/`).
5. **Parked 2026-11-18** after Round 6 (`docs/log.md`) lost 5/5 to
   Blender `decimate`. Unpark plan for Opus 5.5 review: see **Unpark
   roadmap** above, [docs/direction.md](docs/direction.md),
   `~/Games/RETO.md`, and `~/Games/_tools/docs/plans/retopo.md`.

## Attribution

`retopo` (Blender UI: *RetopoForge*) is a fork of
[AutoRemesher](https://github.com/huxingyi/autoremesher) by Jeremy HU
(Dust3D Project) and contributors, used under the MIT license — see
[LICENSE](LICENSE). The core remeshing engine is principally Jeremy's
work; this fork restructures it around a headless library and adds the
`retopo` CLI, the Blender extension, and the benchmark harness. (The
upstream Qt desktop shell was removed; Blender is the UI.)

- Upstream repository: <https://github.com/huxingyi/autoremesher> (tracked as
  the `upstream` git remote)
- Support Jeremy's work:
  [donate via PayPal](https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=GHALWLWXYGCU6&item_name=Support+me+coding+in+my+spare+time&currency_code=AUD&source=url)
- Upstream authors and contributors are credited in the upstream
  repository; third-party licenses:
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
