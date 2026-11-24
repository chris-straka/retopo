# Rigging strategy (historical character pipeline notes)

Updated 2026-10-01 (archived historical note; rigging lives outside this repo).

## Order in the pipeline

Topology first, rig second:

- Humanoids: rig a reusable **base** mesh once with a control rigger; every
  character wrapped onto it via a base-mesh wrapper inherits the rig and
  weights, and bones follow the wrap.
- One-off creatures: remesh with `retopo` -> rig with an external rigger ->
  weight binding / repair.
- Pieces (capes, hair, armor, clothes): weights copied from the body via
  Data Transfer.

## Evaluated tool classes

- **Control-rig generator (Rigify-based, GPL-2.0-or-later):** game presets
  (biped and quadruped), deform-bones-only glTF export for game engines,
  and deterministic landmark detection + metarig fitting.
- **ML auto-rigger (Token-based / VAST-AI, MIT code and weights):** runs
  skeleton + weight prediction on GPU in ~35–55 s per character and can
  supply joint hints or baseline weights for repair.
- **Legacy ML rigger (UniRig):** retired in early evaluations in favor of
  newer ML auto-riggers.

## Bake-off notes (2026-09-30)

Template-fitted control rigs won hero and creature quality: ~160 deform
bones with face, finger, and twist bones vs ~28 body-only joints on a
hero and ~12 weak joints on a creature from raw ML output. Raw ML won only
zero-touch speed (~7 min vs ~30 min scripted) and could be seed-dependent,
so deterministic landmark fitting + full finger/face rigs stayed preferred
for hero characters.

## Open points

- **Mobile bone budget:** ~160 deform bones is heavy for mobile skinning;
  export profiles that drop or merge face/twist bones for mobile LODs help
  keep runtime skinning cost bounded.
- **Weights are the real time sink:** humanoids inherit weights from a
  wrapped base mesh; for one-off creatures where heat weighting fails on
  messy topology, geodesic voxel binding (Dionne & de Lasa 2013) and
  robust weight transfer with inpainting (Abdrashitov et al. 2023) are the
  main candidates.
- **License check:** external ML riggers using a shape encoder derived from
  Michelangelo inherit GPL-3.0 constraints if distributed; running them
  locally and shipping only exported rigs keeps distribution isolated,
  whereas redistributing the tools requires GPL compliance.
