# retopo — agent & contributor notes

One rules file only: this `AGENTS.md` is it — never create a `CLAUDE.md` in
this folder (some coding agents ignore it or warn while both exist).

`retopo` is a fork of [AutoRemesher](https://github.com/huxingyi/autoremesher)
(MIT): a pure Rust engine (`retopo_core` + `retopo_solvers`), the `retopo` CLI,
a GPL-3.0-or-later Blender extension driving the CLI over a subprocess boundary,
and benchmark/quality harnesses (`bench/`). The original C++ tree and Qt shell
were removed after 1:1 differential-oracle verification (see
[docs/archive/rust-switch-verdict.md](docs/archive/rust-switch-verdict.md)).

- **Upstream reference:** <https://github.com/huxingyi/autoremesher> (MIT). Do
  not assume an `upstream` git remote is configured, and **never** `git merge`
  upstream history into this structurally diverged fork — inspect upstream
  changes and port relevant engine fixes by hand, verified against tests and
  benchmarks.
- **Scope & direction:** read [docs/direction.md](docs/direction.md),
  [PLAN.md](PLAN.md), and [docs/README.md](docs/README.md) before proposing
  engine or CLI work.

## Standing rules

- Commit small, logically scoped changes with clear commit messages; never
  force-push, rebase, amend published commits, or otherwise rewrite published
  history unless explicitly asked.
- This is a **public** repository: never commit private game assets, private
  filesystem paths, internal project/character names, or copyrighted test
  meshes. `bench/models/` and `bench/results/` are gitignored.
- Never launch GUI binaries without explicit approval. Headless Blender
  (`--background --factory-startup`) is fine.
- Keep `retopo_core`, `retopo_solvers`, and `retopo` CLI MIT-licensed with zero
  GPL dependencies; the Blender extension (`blender/retopo/`) stays
  GPL-3.0-or-later and communicates with the CLI only as a subprocess.
- New CLI flags or behavior changes must update `--help` (`rust/cli/src/args.rs`)
  and [README.md](README.md).

## Build (pure Rust)

Run from `rust/` (or pass `--manifest-path rust/Cargo.toml`):

```bash
cd rust && cargo build --locked --release -p retopo
```

This produces `rust/target/release/retopo` with zero C++ or `thirdparty/` code
(`faer` is the only external crate dependency).

## Checks & macOS vs Linux test behavior

- **Format & lint:**
  ```bash
  cd rust && cargo fmt --all --check
  cd rust && cargo clippy --locked --workspace --all-targets
  ```
- **Rust test suite (`cargo test --locked --release`):**
  - Bitwise module oracles (`tests/fixtures/*_diff.txt`) and CLI contract
    goldens (`tests/fixtures/cli_golden/`) are pinned on **macOS arm64**, which
    runs the full `cargo test` gate in CI (`.github/workflows/development.yml`
    and `.github/workflows/release.yml`).
  - On **Linux x86_64**, ~11 test binaries can fail on platform floating-point
    bits alone (last-ulp `libm` differences in `acos`/`atan2`, arch-specific
    `faer` SIMD summation order, and x86 negative default NaN payloads). Linux
    CI validates release compilation and `bench/run.py --check bench/baseline-linux.json`;
    when running `cargo test` on Linux, compare against an unchanged baseline
    checkout rather than expecting zero platform-bit diffs.
- **Regenerating CLI goldens** (after an intentional CLI or engine output
  change; review diffs carefully):
  ```bash
  cd rust && UPDATE_GOLDENS=1 cargo test --release -p retopo --test cli_contract
  ```
- **Benchmarks & regression gates:**
  ```bash
  ./bench/fetch_models.sh                               # one-time model download
  ./bench/run.py --check bench/baseline.json            # macOS gate (+ deform_baseline.json)
  ./bench/run.py --check bench/baseline-linux.json      # Linux gate
  ./bench/deform.py --check bench/deform_baseline.json  # deformation gate alone
  ```
- **Quality claims require distributions, not single runs:** tiling is
  sensitive to sub-visible input noise (see
  [docs/archive/igm-validity-spike.md](docs/archive/igm-validity-spike.md)).
  Use `bench/noise.py` + `bench/score.py` for multi-seed spreads.
- **Python & Blender tests:**
  ```bash
  python3 bench/creature/test_gamebench.py
  /Applications/Blender.app/Contents/MacOS/Blender --background \
      --factory-startup --python blender/tests/test_headless.py
  ```

## Layout

- `rust/core/` — `retopo_core` engine library (`auto_remesher/`, `isotropic_remesher`, `parameterizer`, `quad_parameterizer`, `quad_extractor/`, `quad_fill`, `decimator`, `guides`, `density`, `skeleton`, `symmetry`, `glb`, `obj_reader`).
- `rust/solvers/` — `retopo_solvers` least-squares solvers (`constrained`, `mixed_integer` over `faer`).
- `rust/cli/` — `retopo` binary and CLI contract/determinism tests (`tests/`).
- `blender/` — *RetopoForge* Blender 4.2+ extension (`blender/retopo/`, GPL-3.0-or-later) + headless tests (`blender/tests/`).
- `bench/` — regression gate (`run.py`), deformation check (`deform.py`), distribution & scorecard (`noise.py`, `score.py`), baseline comparisons (`compare.py`, `matched.py`, `quadwild.py`, `blender_voxel.py`), profiler (`profile.py`), artist suite (`bench/artist/`), and creature posed/blind benchmark (`bench/creature/`).
- `docs/` — [docs/README.md](docs/README.md) index, active docs (`architecture.md`, `direction.md`, `log.md`, `bench/`), and archived R&D logs (`docs/archive/`).
- `tests/fixtures/` — golden dumps, procedural OBJ/GLB test meshes, and `cli_golden/` outputs.
- `Formula/` — Homebrew formula (`Formula/retopo.rb`).
- `tools/` — headless Blender glTF repair-topology adapter (`genforge_adapter.sh`, `genforge_adapter.py`).
- `gates/` — gated verification specifications and scripts.
