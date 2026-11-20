# retopo-3: Posed game-camera image check

Add an 8-frame posed HLH game-camera image check (`bench/creature/posed_image_check.py` +
pure-Python scorer `bench/creature/posed_image_metrics.py`) comparing each cut against the
dense source (both posed with the same weights across 8 clip frames at the follow camera),
measuring both silhouette difference and shading difference, and hook optional `image_check`
control evaluation into `bench/creature/gamebench.py`.

Read `bench/creature/game_frame.py`, `bench/creature/silhouette.py`, `bench/creature/gamebench.py`,
`bench/creature/test_posed_image_check.py`, and `docs/plans/retopo.md` first.

## Requirements

1. **`bench/creature/posed_image_metrics.py`** (stdlib only, no Blender dependency):
   - `DEFAULT_FRAME_COUNT = 8`
   - `sample_frame_indices(start, end, count=8)` returns `count` evenly-spaced integer frame indices.
   - `read_png_rgba(path)` reads an 8-bit non-interlaced RGBA (or RGB) PNG into `(w, h, bytearray_rgba)`.
   - `compare_frame_pair(ref_png, cand_png)` computes:
     - `sil_diff_pct`: `100.0 * XOR(mask_ref, mask_cand) / max(1, area(mask_ref))` (foreground mask = `alpha >= 128`).
     - `shade_rmse`: RGB RMSE (0..255) over the foreground union (`mask_ref | mask_cand`).
     - `shade_l1_pct`: mean absolute RGB error (% of 255) over the foreground union.
     - Identical images return exact `0.0` for all three metrics.
   - `aggregate_frames(per_frame)` returns `{"frames", "sil_diff_mean_pct", "sil_diff_max_pct", "shade_rmse_mean", "shade_rmse_max", "shade_l1_mean_pct", "per_frame"}`.

2. **`bench/creature/posed_image_check.py`** (Blender script):
   - CLI: `blender -b --factory-startup -P bench/creature/posed_image_check.py -- REF_RIGGED.glb CAND_RIGGED.glb FRAME_MESH.obj OUT.json [--frames 8] [--width 320] [--height 360] [--cache DIR]`
   - Frames the HLH follow camera strictly from `FRAME_MESH.obj` (matching `game_frame.py`).
   - Poses both `REF_RIGGED.glb` and `CAND_RIGGED.glb` across 8 frames (embedded animation action if present with >= 2 frames, otherwise `game_frame.apply_stride_pose` across the 36-frame stride cycle), smooth-shaded, `film_transparent = True`, Workbench Studio lighting.
   - Caches reference frames under `--cache` when provided.
   - Writes aggregated JSON to `OUT.json`.

3. **`bench/creature/gamebench.py` integration:**
   - Optional `--image-check` flag invoking `posed_image_check.py` per cut when a rigged reference exists.
   - In `verdict()`, when `image_check` entries are present on all valid rows, include
     `planted_image_worse_count` and `planted_image_worse_on_all` in `controls` and require
     `planted_image_worse_on_all` for `controls["valid"]`.

## Evidence

`bash gates/retopo-3/gate.sh` (through fleet with `--needs=blender`) prints `GATE PASS`:
- Protected paths (`gates/`, `bench/creature/test_*.py`, `rust/`) unchanged.
- `bench/creature/test_posed_image_check.py` passes (unit tests + real 5-creature 8-frame
  Blender test verifying planted > decimate on 5/5 and decimate-vs-decimate == 0.0 on 5/5).
- `bench/creature/test_gamebench.py` still passes all 13 tests.
- Zero stale paths (`rfwork|weightforge|/home/f`) in `bench/creature/`.
- `cd rust && cargo fmt --all --check` passes.
