# retopo architecture

Pure Rust workspace (`rust/`) plus a GPL Blender extension (`blender/retopo/`) and Python benchmark/regression suites (`bench/`). The original C++ tree and upstream Qt desktop shell were removed after the 1:1 port was verified; Blender serves as the GUI.

## Components

| Component | Path | Target / Version | License |
|---|---|---|---|
| Engine crates | `rust/core/`, `rust/solvers/` | `retopo_core`, `retopo_solvers` | MIT |
| CLI binary | `rust/cli/` | `retopo` (`0.3.0`) | MIT |
| Blender extension | `blender/retopo/` | *RetopoForge* (`0.3.0`, Blender 4.2+) | GPL-3.0-or-later |
| Benchmark harness | `bench/` | Static, deformation, artist, and creature suites | MIT |

### Dependency and license boundaries

- **`retopo_solvers`** (`rust/solvers/src/`): `constrained` and `mixed_integer` least-squares solvers backed solely by `faer` (`0.24`).
- **`retopo_core`** (`rust/core/src/`): depends only on `retopo_solvers` and `faer` (`0.24`). Pre-decimation (`decimator`) and all mesh/GLB/OBJ math live in pure Rust; no C++, `build.rs`, or vendored `thirdparty/` code remains.
- **`retopo` CLI** (`rust/cli/src/`): depends only on `retopo_core` and the Rust standard library (`args`, `constraint_files`, `error`, `format`, `main`, `mesh_io`, `progress`, `report`, `run`).
- **Blender extension** (`blender/retopo/__init__.py`): imports only `bpy` / `mathutils` and the Python standard library. It drives the `retopo` CLI as an external subprocess over temporary OBJ files, preserving a strict GPL/MIT process boundary.
- **Verification & benchmarks** (`rust/cli/tests/cli_contract.rs`, `bench/*.py`): treat the built `retopo` binary as a black box (arguments + mesh files in, exit code + output meshes + report parsed).

Data flow:

```text
Blender mesh --wm.obj_export--> in_N.obj --[retopo CLI]--> out_N.obj --wm.obj_import--> Blender mesh
bench/models/*.obj --[retopo CLI]--> results JSON --check--> baseline verdict
```

## Engine pipeline (`retopo_core`)

`auto_remesher` (`rust/core/src/auto_remesher/`) orchestrates each remesh run across modular phases (`normalize`, `decimate`, `island`, `resample`, `coverage`, `pipeline`, `atlas`, `progress`):

1. **Condition & split:** normalize sub-unit input scale by an exact power of two (`normalize`), weld non-indexed triangle soup on load (`mesh_io` / `obj_reader` / `glb`), resolve symmetry (`symmetry`), size voxels, split into connected islands (`mesh_separator`), and run native pre-decimation (`decimator`) when an island exceeds 8x the target triangle count.
2. **Isotropic working mesh:** resample each island in parallel (`isotropic_remesher`, `iso_remesh_kernel`) to a curvature-adaptive working mesh.
3. **Parameterize:** build the cross field (`frame_field`, steered by `guides` and hard `features` with optional `symmetry`), simplify singularities (`singularity_simplifier`), evaluate local density (`density`, plus sibling-limb crease sizing from `skeleton`), insert automatic dipole pairs across sharp density steps when enabled (`quad_parameterizer`), and solve the seamless quad cover via `retopo_solvers::{constrained, mixed_integer}`.
4. **Extract, verify & merge:** trace and clean up the quad mesh (`quad_extractor/*`), pair and re-cut residual odd faces into quads (`quad_fill`), check working- and input-side surface coverage plus extremity loss (`coverage`) with deterministic jittered retries and finer solo island re-runs, merge islands, optionally snap symmetric vertex positions, and optionally pack per-island UVs into a normalized atlas (`atlas`).

Shared utilities (`vector2`, `vector3`, `double_utils`, `position_key`, `surface_mesh`, `progress`, `par`) keep every stage deterministic run-to-run on the same platform.

## Blender extension (`blender/retopo/`)

`blender/retopo/__init__.py` (extension v0.3.0, Blender 4.2+) exposes the *RetopoForge* sidebar panel (`VIEW_3D` → `UI` → `RetopoForge`) and talks to the CLI via per-run temporary directories (`tempfile.mkdtemp(prefix="retopo_")`) cleaned up on exit:

1. **Identity-transform export:** each selected mesh object is temporarily placed under the identity transform (so local == world), exported via `wm.obj_export` with optional modifier evaluation (*Apply Modifiers*), and its world matrix is always restored in a `finally` path.
2. **Constraint & option plumbing:** maps panel settings (`--target-quads`, `--model-type`, `--sharp-edge`, `--smooth-normal`, `--edge-scaling`, `--adaptivity`, `--anisotropy`, `--uvs on`, `--symmetry`) and exports per-target constraint files when enabled:
   - *Flow Guides* (`--guides`) from Edit Mode edge selections,
   - *Sharp Features* (`--features`) from *Mark Sharp* edges,
   - *Density Mask* (`--density`) from a selected vertex group,
   - *Skeleton Density* (`--skeleton`) from the deforming armature.
3. **Subprocess execution:** `retopo.remesh` offers a synchronous `execute()` path (used headless and by tests) and an interactive `invoke()`/`modal()` path that polls a log file rather than pipes (preventing pipe-buffer deadlocks on verbose progress output) and supports `ESC` cancellation.
4. **Import, LODs & Bake Assist:**
   - Remesh results replace the active mesh in a single undo step (or spawn `<object>_retopo` when *Keep Original* is set), and per-object parameters are stored in `scene.retopo_recall` for automatic recall when switching active objects.
   - `retopo.generate_lods` runs `--lods` once per selected object and imports `<object>_lod<N>` siblings.
   - *Bake Assist* (`retopo.bake_textures`, `retopo.remesh_and_bake`, `retopo.project_uvs`, `retopo.transfer_colors`) handles Smart UV or Unwrap+Pack UV generation, Cycles CPU high-to-low PBR baking (diffuse, normal, roughness, metallic, AO, emission), nearest-point UV projection, and vertex-color transfer.

Binary discovery (`find_retopo_binary`) checks the add-on's *Retopo CLI* preference path first, then `PATH`, then `rust/target/{release,debug}/retopo` relative to a repository checkout.

## Benchmark harness (`bench/`)

- **Regression & deformation gates:** `bench/run.py` runs the CLI over standard test models (`bench/fetch_models.sh`), validates outputs, checks counts against `bench/baseline.json` (macOS) or `bench/baseline-linux.json` (Linux), and runs `bench/deform.py` against `bench/deform_baseline.json`.
- **Distribution & baseline comparisons:** `bench/noise.py` and `bench/score.py` measure multi-seed spreads; `bench/compare.py`, `bench/matched.py`, `bench/quadwild.py`, and `bench/blender_voxel.py` compare against external/free baselines; `bench/profile.py` records phase timings and peak RSS.
- **Artist & creature suites:** `bench/artist/` evaluates edge-ring recovery and surface fidelity on CC0 base meshes and props; `bench/creature/` (`bench/creature/README.md`) evaluates posed joint deformation, silhouette RMSE, open-edge health, and blind game-camera judging (`gamebench.py`, `budget_finder.py`, `hole_repair.py`).
