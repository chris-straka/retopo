# Goal: meshkit-1 (portable bench wiring) — `cut.py clean-copy` & hole tracking

Wire `meshkit`'s `clean_copy.py` into `retopo`'s neutral creature benchmark so
`gamebench.py --tools clean-copy,decimate` (and `gamebench.sh` with
`GAMEBENCH_TOOLS=clean-copy,decimate`) can benchmark `clean-copy` against plain
`decimate` on any dev or held-out creature workspace without hardcoded paths.

## Requirements

1. **`bench/creature/cut.py` supports `clean-copy`:**
   - Resolves `meshkit` from `MESHKIT_DIR` (if set, must contain
     `tools/clean_copy.py`, failing loudly naming `MESHKIT_DIR` otherwise) or
     sibling `../meshkit` next to the `retopo` repo root.
   - Runs `tools/clean_copy.py --steps shell,cut --tris TRIS` in a temp folder
     (sizing `--voxel` from the mesh bounds so small and large creatures carve
     reliably), imports the resulting closed cut mesh (`cut.ply`), applies
     transforms, and exports `OUT.obj`.
   - Zero hardcoded hostnames, usernames, or private paths.

2. **`bench/creature/gamebench.py` tracks open-boundary holes per cut:**
   - Adds `obj_holes(path)` returning `(holes, open_edges)` for any `.obj` cut
     (0 holes / 0 open edges on a watertight mesh).
   - Records `"holes"` and `"open_edges"` on each measured cut in `_measure_cut`.
   - Adds `holes_no_worse_on_all`, `challenger_holes_max`, and
     `baseline_holes_max` to `verdict()` (staying backward compatible when older
     rows omit `"holes"`).

3. **`bench/creature/gamebench.sh` discovers `MESHKIT_DIR`:**
   - Auto-discovers sibling `../meshkit` when `MESHKIT_DIR` is unset and exports
     `MESHKIT_DIR` when `GAMEBENCH_TOOLS` includes `clean-copy`.

4. **All existing tests (`test_gamebench.py`, `test_posed_image_check.py`) and
   new `test_clean_copy_cut.py` pass in headless Blender.**
