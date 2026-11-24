# retopo

`retopo` (Blender UI: *RetopoForge*) is a pure Rust headless quad-remeshing
engine and CLI paired with a GPL-3.0-or-later Blender 4.2+ extension. It is a
fork of [AutoRemesher](https://github.com/huxingyi/autoremesher) (MIT, by
Jeremy HU) restructured around a standalone library (`retopo_core` +
`retopo_solvers`), the `retopo` CLI, a Blender extension, and automated
quality/deformation benchmarks. There is no standalone desktop app — Blender is
the GUI, and the original C++ engine and Qt shell have been removed (see
[docs/archive/rust-switch-verdict.md](docs/archive/rust-switch-verdict.md)).

## Quickstart: Install & Build

### Homebrew (macOS)

```bash
brew tap chris-straka/retopo https://github.com/chris-straka/retopo
brew install chris-straka/retopo/retopo
```

The tap builds `retopo` from `Formula/retopo.rb` (pulling in Rust
automatically) and links it onto your `PATH`. Keep it updated with
`brew update && brew upgrade retopo`.

### Build from source (Cargo)

```bash
cd rust && cargo build --locked --release -p retopo
```

This builds `rust/target/release/retopo` in pure Rust (only external dependency:
`faer` for sparse/dense linear algebra; zero C++ or vendored `thirdparty/`
code). Compiles on macOS and Linux; bitwise module and CLI contract goldens
are pinned on macOS arm64 (see [AGENTS.md](AGENTS.md)).

## CLI Usage & Options

```bash
./rust/target/release/retopo --help
./rust/target/release/retopo --input bench/models/armadillo.obj \
    --output /tmp/armadillo-remeshed.obj --report /tmp/armadillo-report.txt \
    --target-quads 5000
```

Supported formats: `.obj` and `.glb` (non-indexed triangle soup is welded on
load; batch directories and `--lods` chains preserve each input's extension).

### Core & Sizing

- `-i, --input <file|dir>` (required): input `.obj`/`.glb` mesh or directory of
  meshes (non-recursive batch mode).
- `-o, --output <path>` (required): output mesh file, or directory for batch
  and `--lods` runs (created if missing).
- `--target-quads <count>`: target quad count (default `50000`).
- `--lods <q0,q1,...>`: emit a multi-rung LOD chain (`<stem>_lod0.<ext>`,
  `<stem>_lod1.<ext>`, ...) in one run (overrides `--target-quads`).

### Presets & Quality Controls

- `--model-type <organic|hardsurface>`: shape hint (default `organic`; also
  accepts `hard-surface`/`hard_surface`).
- `--edge-scaling <factor>`: average edge length multiplier vs target size
  (`1.0–4.0`, default `1.0`).
- `--working-refinement <factor>`: working-mesh edges per output edge
  (`1.0–4.0`, default `2.0`; `1.0` matches the legacy resolution).
- `--sharp-edge <degrees>`: dihedral crease threshold (`30–180`, default
  `90.0`).
- `--smooth-normal <degrees>`: smooth shading normals across edges below this
  angle (`0–180`, default `0.0` = off).
- `--adaptivity <value>`: `0` = uniform quads, `1` = follow curvature (`0–1`,
  default `1.0`).
- `--anisotropy <value>`: `0` = square quads, `1` = stretch along curvature
  (`0–1`, default `1.0`).
- `--symmetry <off|auto|x|y|z>`: mirror across a plane (`default: off`; `auto`
  detects the dominant mirror plane and falls back to unconstrained output
  below threshold).
- `--keep-odd-faces`: keep triangles/pentagons left by extraction (by default,
  nearby odd faces are paired and re-cut into quads).
- `--no-exact-count`: single pass even if the quad count misses `--target-quads`
  by more than 8% (default runs one aimed retry).
- `--no-finer-rerun`: disable solo 3x/6x finer island re-runs and extremity
  checks on missed thin/small parts.

### Constraints & UVs (single-file and `--lods` runs)

- `--guides <file>`: polyline file (`x y z` points per line, blank lines
  separate polylines, `#` comments) steering cross-field flow.
- `--features <file>`: polyline file in the same format marking crisp
  hard-surface edges (alignment constraints win ties over guides).
- `--density <file>`: per-vertex multipliers in OBJ `v`-line order (`0.25–4.0`,
  `1.0` = unchanged).
- `--skeleton <file>`: rig bone file (`hx hy hz tx ty tz parent bend_degrees`
  per line; `parent` = earlier line index or `-1`), widening edges across
  sibling-limb creases (`--verbose` prints per-crease asks; multiplies with
  `--density`).
- `--dipoles <off|auto>`: automatic `+1/-1` dipole insertion along sharp
  `--density` steps (default `auto`).
- `--dipole-every <count>`, `--dipole-ratio <value>`: expert overrides for
  dipole ring stride (`0` = auto) and step-sharpness threshold (`0` = auto
  `1.5`).
- `--uvs [on|off]`: emit remeshed UVs (`vt` in OBJ, `TEXCOORD_0` in GLB,
  normalized `0..1` per island and atlas-packed across multi-island outputs;
  default `off`, bare `--uvs` means `on`).

### Output, Diagnostics & Exit Codes

- `--report <report.txt>`: write summary statistics to a file.
- `-q, --quiet`: suppress progress output (warnings, errors, and the report
  still print).
- `--verbose`: print phase-timing breakdown and engine diagnostics to stderr
  (mutually exclusive with `--quiet`).
- `-h, --help` (`--help --all` includes expert flag details), `-v, --version`.

`stdout` receives only results (report block, `FILE`/`LOD` rung lines); progress,
warnings, errors, and the final `done:` line go to `stderr`. Inputs are
validated upfront; out-of-range numeric flags clamp with a `retopo: warning:`.
Exit codes: `0` success, `1` runtime failure, `2` usage/validation error.

```bash
./rust/target/release/retopo --input bench/models/armadillo.obj --output /tmp/hero.obj --lods 10000,5000,2000
./rust/target/release/retopo --input assets/ --output assets-retopo/
```

## Blender Extension (*RetopoForge*)

Quad remeshing inside **Blender 4.2+** driven by the `retopo` CLI over a clean
subprocess boundary (MIT engine, GPL-3.0-or-later add-on — see
[blender/README.md](blender/README.md) for the full panel guide):

- **Remesh Selected & LODs:** identity-transform temp-OBJ round-trip with
  single-step undo replacement or *Keep Original*, optional *Apply Modifiers*,
  `--symmetry`, `--uvs on`, `--lods` sibling generation, and persistent
  per-object settings recall.
- **Interactive Controls:** Edit-Mode *Flow Guides* (`--guides`), *Mark Sharp*
  features (`--features`), weight-painted *Density Masks* (`--density`), and
  armature *Skeleton Density* (`--skeleton`).
- **Bake Assist:** high-to-low Cycles CPU PBR baking (diffuse, normal,
  roughness, metallic, AO, emission), *Smart UV* or *Unwrap + Pack* with texel
  density control, *Remesh + Bake All*, *Project HIGH UVs*, and *Transfer HIGH
  Colors*.

Install in Blender:

1. Build the CLI (`cd rust && cargo build --locked --release -p retopo`).
2. Package the extension zip:
   ```bash
   (cd blender && zip -r /tmp/retopo-addon.zip retopo -x '*/__pycache__/*')
   ```
3. In Blender: *Edit → Preferences → Extensions → Install from Disk*, select the
   zip, and enable *RetopoForge* (v0.3.0). Point *Retopo CLI* in add-on
   preferences to `retopo` if it is not on `PATH`.

Headless verification (use the Blender app binary directly rather than a broken
shim):

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background \
    --factory-startup --python blender/tests/test_headless.py
```

## Measured Strengths & Current Limits

`retopo` is actively developed for multi-game asset creation ([docs/direction.md](docs/direction.md)).
Benchmarks distinguish clearly between where it works well today and where
solver/extractor improvements are needed ([docs/log.md](docs/log.md)):

### Where it works well

- **Clean sculpts, stylized meshes, and reusable base meshes:** across the
  20-mesh CC0 artist benchmark (`bench/artist/`, Rounds 4–5), default `retopo`
  recovers **34.7% mean artist edge rings** (`12.0°` mean edge flow error,
  `1.02%` median p95 shape error) vs QuadriFlow's **3.5%** rings and
  QuadWild+Bi-MDF's **32.2%** rings. On a whole-body humanoid base (`10.5k`
  quads), it reached **40% artist rings / 9.5° flow / 0.06% p95 shape error**.
- **Headless batch & LOD pipelines:** deterministic per-platform runs, clean
  CLI error reporting, `--lods` chains, `--uvs` atlas packing, and zero
  licensing/seat-activation limits.
- **Automatic dipole density relief on curved thin geometry:** `--dipoles auto`
  improves `4x` sharp density-mask refinement from `1.70x -> 2.04x` on single
  curved tubes and `1.33x -> 1.52x` on fused tubes
  ([docs/archive/dipole-production.md](docs/archive/dipole-production.md)).

### Honest current limits

- **Noisy high-frequency scans & uncleaned AI creature meshes:** in Round 6
  ([docs/log.md](docs/log.md), 5 generated creatures at `10,000` tris), bare
  `retopo` lost **5/5** to plain Blender `DECIMATE` on posed bend tears
  (`0.26–10.9%` vs `0.00–10.8%`), blind game-camera judging (`5/5`), and
  surface closeness (**11.59x** median p95 surface error ratio). It left open
  holes on 4/5 creatures (`11–687` open edges), melted fine horn crowns, claws,
  and dorsal spikes, split/dropped islands, and ran **1,221–2,143 s** on one
  creature whose island exhausted coverage retries. (For broken/unclosed meshes
  in benchmarks, `bench/creature/cut.py decimate-repaired` provides a 4-stage
  hole-repair + decimate baseline; see [bench/creature/README.md](bench/creature/README.md).)
- **No automatic hard-edge guide loops or layout-level symmetry yet:**
  `--guides` steers the cross-field tangent without pinning quad edges onto
  joint loops, `--symmetry` mirrors the cross field and snaps vertex positions
  rather than mirroring the integer layout, and strong localized `--density`
  asks still partially saturate because density enters only the least-squares
  cover rather than cross-field singularity placement
  ([docs/archive/exoside-gap.md](docs/archive/exoside-gap.md),
  [docs/archive/review-2026-11-13.md](docs/archive/review-2026-11-13.md)).
- **Clean static props built at budget:** remeshing an already-clean low-poly
  prop at its own triangle count adds surface error (`0.3–11.6%` p95 in
  Round 1) without benefit.

Active engineering priorities to close these gaps are tracked in [PLAN.md](PLAN.md).

## Tests & Benchmarks

```bash
cd rust && cargo fmt --all --check && cargo test --locked --release
python3 bench/creature/test_gamebench.py
```

- **Rust tests (`rust/`):** module-level differential/golden tests (`rust/core/tests/`,
  `rust/solvers/tests/`), determinism tests, and the CLI contract test
  (`rust/cli/tests/cli_contract.rs`) driving the built binary over 56 arg-matrix,
  25 IO-failure, and 42 remesh cases against `tests/fixtures/cli_golden/` (run-to-run
  determinism plus count tolerances). Regenerate goldens after an intentional
  change with:
  ```bash
  cd rust && UPDATE_GOLDENS=1 cargo test --release -p retopo --test cli_contract
  ```
  Note: macOS arm64 is the pinned oracle/golden platform; see [AGENTS.md](AGENTS.md)
  for Linux floating-point notes.
- **Regression & quality benchmarks (`bench/`):**
  ```bash
  ./bench/fetch_models.sh                             # one-time download of test models
  ./bench/run.py --check bench/baseline.json          # static + deform gate (macOS)
  ./bench/deform.py --check bench/deform_baseline.json
  ./bench/profile.py                                  # wall time, RSS, phase breakdown
  python3 bench/creature/gamebench.py --help          # posed creature & blind-judge suite
  ```

## Documentation & Layout

See **[docs/README.md](docs/README.md)** for the complete index separating active docs from archived R&D logs.

| Path | Purpose |
|---|---|
| `rust/core/`, `rust/solvers/`, `rust/cli/` | Pure Rust engine crates and `retopo` CLI binary |
| `blender/retopo/`, `blender/tests/` | *RetopoForge* Blender 4.2+ extension (GPL) and headless tests |
| `bench/` | Static/deform gates, artist benchmark (`bench/artist/`), creature bench (`bench/creature/`) |
| `docs/` | [Index](docs/README.md), [Architecture](docs/architecture.md), [Direction](docs/direction.md), [Benchmark Log](docs/log.md), and [Archive](docs/archive/todo-history.md) |
| `PLAN.md`, `AGENTS.md` | Active engineering roadmap and contributor/agent instructions |
| `Formula/` | Homebrew formula (`Formula/retopo.rb`) |
| `tools/`, `gates/`, `tests/fixtures/` | Headless glTF adapter scripts, verification gates, and test fixtures |

## Attribution & License

`retopo` is a fork of [AutoRemesher](https://github.com/huxingyi/autoremesher)
by Jeremy HU (Dust3D Project) and contributors, used under the MIT license
([LICENSE](LICENSE)). The core remeshing algorithm is principally Jeremy's work;
this repository provides the pure Rust engine port, headless CLI, Blender
extension, and benchmark suites.

- Upstream repository: <https://github.com/huxingyi/autoremesher>
- Support Jeremy's work: [donate via PayPal](https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=GHALWLWXYGCU6&item_name=Support+me+coding+in+my+spare+time&currency_code=AUD&source=url)
- Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
