# Plan: freehand flow guides in the Blender add-on

Status: planned 2026-10-06, not started.

## Why

Flow guides decide where the remesh puts its edge loops (shoulder rings,
groin, eye and mouth loops). Guides already work end to end: the add-on's
Flow Guides panel turns an Edit Mode edge selection into a `--guides` file,
an external character pipeline can attach it to a character asset, and the
repair-topology adapter (`tools/genforge_adapter.sh`) passes it to `retopo`.

The gap is drawing them. External character pipelines remesh from dense
generated models, often 1M+ triangles (a generated hero mesh is ~1.5M).
Picking edges on a mesh that dense is slow, and its edges rarely run where
the flow should go. Getting a character GLB into Blender also needs an import
with "Guess Original Bind Pose" off, or the mesh shifts about 1 m and the
guides miss.

Goal: open a character GLB, draw guides freehand on the surface, and send
them back, in a few minutes, on any mesh size.

## G1: freehand strokes

- **Draw:** a "Draw Guides" button in the Flow Guides panel switches to
  Blender's Annotate tool with placement set to Surface, on a dedicated
  annotation layer ("RetopoForge Guides", distinct colour). It works in
  Object Mode, so a 1.5M-triangle mesh never enters Edit Mode. The native
  annotation eraser and Ctrl+Z work as usual.
- **Convert:** the exporter reads that layer's strokes and, per stroke:
  1. resamples at a spacing tied to the target edge length (from the
     target quad count and the surface area, not fixed units);
  2. projects each point onto the target mesh (BVH nearest point in the
     object's local space; points farther than a few target edge lengths
     are dropped);
  3. splits the stroke where points were dropped, and drops pieces that
     end up too short;
  4. simplifies the result, keeping deviation under a fraction of an edge
     length.
- **Mirror:** an option, on by default for humanoid and quadruped classes,
  that mirrors strokes across the object's symmetry plane. The plane comes
  from `retopo --symmetry auto` detection, falling back to X. You draw one
  side.
- **Merge:** freehand strokes and an edge selection may both be present;
  the exported file contains both.
- **Preview:** a toggle draws the exact polylines that will be exported
  (GPU overlay), so what you see is what gets sent.
- **Output:** the same `--guides` format as today (local coordinates, one
  `x y z` per line, blank line between polylines), so `retopo`, external
  pipelines, and the adapter are unchanged.
- Check the annotation API on both Blender 4.2 LTS (the manifest minimum)
  and 5.2 before building on it (annotations moved around during the Grease
  Pencil v3 change). If Surface placement is unreliable, fall back to a
  small modal draw operator that raycasts the mouse onto the mesh.

## G2: one click in, one click out (asset pipeline integration)

- **Open:** an "Open Character" operator takes a GLB from an asset's
  hand-edit folder, imports with "Guess Original Bind Pose" off, hides the
  armature, selects the mesh to remesh, frames it, starts the guides layer,
  and remembers the asset name.
- **Send:** a "Send Guides" operator exports the guides next to the GLB and
  hands them back to the configured character CLI to resume the pipeline run,
  showing the command's result in the panel.
- **Launch from CLI:** an optional `--open` flag on the external guide
  command starts Blender with the add-on and opens the character directly.
- **Coordinate check:** the adapter imports the same GLB the same way, so
  local coordinates line up. A test asserts the round trip (draw, export,
  adapter import, `retopo` input) stays within 1 mm.

## G3: helpers (after G1-G2 prove out)

- **Suggest Guides:** generate starting rings from the rig's joints
  (`retopo`'s existing joints-to-guides helper) as editable strokes on the
  guides layer. You fix them up instead of starting from nothing.
- **Several meshes:** one guides file per mesh. Today a character with
  several meshes sends the same file to every mesh. Needs the adapter and
  caller to key guides by mesh name.
- **Density brush:** pass the existing density vertex group through
  the adapter (`--density`), so you can paint "more detail here" (face,
  hands) the same way.

## Verification

- **Headless tests:** scripted strokes on fixtures (a cylinder, a torso,
  a hero mesh). Every exported point lies on the surface (under 1 mm), mirrored
  strokes match, gaps split, the guides layer survives save and reload.
  Covers 4.2 and 5.2.
- **Real run:** scripted shoulder rings and groin lines on a dense 1.5M
  mesh, then pipeline runs with and without them. Compare deformation weights
  scores and before/after edge-flow renders.
- **UI:** screenshots of the panel and of strokes on the mesh.

## Effort and where it runs

- Scope: G1 freehand stroke layer + projection, G2 open/send round-trip, G3
  joint suggestions + per-mesh guides + density brush pass-through.
- Requires Blender (runs on macOS or headless Linux hosts with Blender
  installed).
- Scope boundary: retopo add-on & adapter (`blender/retopo/`, `tools/`), plus
  caller-side `--open` / per-mesh hook if integrated into an external pipeline.
