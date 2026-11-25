# retopo-v21: the v2/v2.1 engine under the restored pins

Repo: this one (`retopo`), in the worktree you were given. One goal, one
conversation.

## Where things stand

On 2026-11-23/24 the v2/v2.1 engine rounds (`c2911f57`, `ea6d034a`,
`292ed6f1`, `3b13a8bc`, `d3b1bce0`, `66382e68`, `fa90d436`; the code is
`git diff d1aed13b HEAD -- rust/core/src rust/cli/src`) regenerated the
bench baselines, CLI goldens and test pins to accept their own output,
regressions included. On 2026-11-25 the honest ones came back (`48279b98`,
`35d7061d`, `e517ce3f`; `docs/log.md`, "2026-11-25: the gates put back").
They pin the pre-v2 engine's output (`d1aed13b`): exact quad counts, oracle
replays, coverage reports, the timing checksum, the `--help` text
(`--working-refinement` default 2.0). The v2.1 code fails them: a collapsed
ring with the finer rerun off is no longer reported, beast@1000's and the
thin claw's coverage retries no longer fire, the timing case makes 623 quads
instead of 694, case 132 fires a retry, the oracle replays and about 30 CLI
goldens differ, and `bench/run.py --check bench/baseline.json` flags beast
and dragon (small: -5.1% and -7.0% quads) and deformation. Two v2.1 tests
that pinned v2.1's own output on the default path (the thin claw at wr=2.0,
and its box and claw across refinements) moved to
`rust/core/tests/v2_opt_in.rs`, where they run with the v2 engine on.

## What done means

`bash gates/retopo-v21/gate.sh` prints `GATE PASS` on a macOS arm64 build
machine (run it with `check`). That means, with nothing else changed:

1. **The default path is the pre-v2 path.** The CLI's defaults and
   `AutoRemesher::new`'s defaults give the pre-v2 output: every restored pin,
   oracle replay and CLI golden passes, and the bench shows no regression
   against `bench/baseline.json`. The CLI's `--working-refinement` default is
   2.0 again (the engine's own default stays 1.0, as before v2), and
   `--help` and README say so. No wall-clock limit acts on the default path:
   the engine's retry budget defaults to off (`None`), as before v2, and the
   CLI's 180 s exact-count budget leaves the default CLI path.
2. **v2/v2.1 is kept, off by default, behind `AutoRemesher::set_v2_engine(bool)`.**
   With it on, the engine does exactly what v2.1 (`5e15a666`) did: the
   normal-guarded input projection (`project_mesh_onto_surface`), the
   iso-kernel feature-edge and decimator-snap changes, the quad-extractor
   pinch / detached-part / hole repairs, soft auto-sharp (its 1,000-tri
   threshold stays as v2.1 had it, inside the option only), the finer-rerun coverage records, and the 75 s retry budget.
   Per-behaviour setters are welcome. The working refinement stays whatever
   the caller sets (v2.1's adaptive 1.75 -> 1.5 rule was a CLI default; keep
   it as a public library function with its unit tests, unused by the CLI).
   A v2/v2.1 change may stay on by default only if every pin and the bench
   still pass with it on.
3. **The opt-in keeps v2.1 intact.** `rust/core/tests/v2_opt_in.rs` (written
   with the gate; don't edit it) holds v2.1's own pins run with
   `set_v2_engine(true)`: beast@1000 covers its appendage on the first
   attempt and embeds on the input mesh, the thin claw at wr=2.0 fires a
   working-side retry that recovers on retry 2 from 47 stranded vertices,
   the claw and box survive at wr 1.0 / 1.75 / 2.0, and the timing grid makes
   623 quads / 668 verts with v2.1's checksum. All 4 must pass.
4. **Docs.** README's `--working-refinement` line matches `--help`; README
   says how to turn the v2 engine on from the library; a short entry at the
   top of `docs/log.md`'s dated sections says what moved behind the option.
   Fix any line in `PLAN.md` or `docs/` that now states a wrong default.

## Folders you may change

`rust/core/src/` (except `rust/core/src/auto_remesher/tests.rs`),
`rust/cli/src/`, `README.md`, `PLAN.md`, `docs/`. Nothing else.

## Never

- Never edit the gate (`gates/`), any test file (`rust/core/tests/*`,
  `rust/cli/tests/*`, `rust/core/src/auto_remesher/tests.rs`), the fixtures and goldens
  (`tests/`), the bench and its baselines (`bench/`), or any `Cargo.toml` /
  `Cargo.lock`. Don't regenerate a golden or a fixture (`UPDATE_GOLDENS`,
  `UPDATE_ISOREMESH` and the like).
- Never remove a unit test inside `rust/*/src`, add `#[ignore]`, or weaken an
  assertion there. A v2-era unit test there may change only to switch on the
  option it tests (or move with the code it tests); its assertions stay.
- Never choose between pre-v2 and v2 behaviour by input size, triangle or
  vertex count, target count, file name, or anything else that tells the test
  meshes apart: v2 behaviour comes only from the explicit options.
- No new crates, no new CLI flags (the `--help` goldens are fixed), no
  changes to the Blender add-on or the bench harness.

## Out of scope

Judging v2.1 on held-out or game meshes, the Linux baselines, the Blender
add-on, the bench scripts.
