# retopo-8: parked-repo hygiene

Repo: retopo (`~/Games/_blender/retopo`). One goal, this repo only. Docs only: no code.

## Why

retopo is parked (the owner, 2026-11-18). The repo is public, and three of its files still
say things that are no longer true: an OpenCode config for a tool nobody runs, a TODO whose
opening describes an old engine and the owner's game, and a direction doc that says creatures
go through meshkit's clean copy when the route actually decimates the raw mesh.

## What to do

1. Delete `opencode.json` (OpenCode and Muse jobs are off; nothing reads it).
2. `TODO.md`: replace the owner-context header (lines 3-16 today, from "Owner context:" down
   to the paragraph about the owner's corpus) with one line saying the file is history: the
   roadmap as it stood while retopo was being built, kept as a record; the current state is
   in `docs/direction.md`. Keep the rest of the file. Line 273 names the old engine
   ("Godot import auto-LOD interaction"): say "the game engine's own LODs" instead.
3. `docs/direction.md`:
   - The **Creatures** paragraph (lines 17-27): say what the code does. The creature route
     cuts the raw generated mesh with Blender's decimate in motion's `tools/tripo_to_glb.py`
     (called by `tools/tripo_rig.py`), then hands it to skintokens' creature route for the
     rig. meshkit's clean copy is not on the creature route. Keep the paragraph's numbers.
   - Line 15 (props) says a generated prop "goes through meshkit's clean copy": reword it
     without the words "clean copy" (for example "meshkit's `clean_copy.py`, run by hand").
   - Lines 114 and 129 name the old engine: say "the game engine" (the games run on Bevy).
4. Nothing else. Don't touch `rust/`, `bench/`, `tests/`, `tools/`, `blender/`, `README.md`,
   `AGENTS.md` or `PLAN.md`.

## Evidence

`bash gates/retopo-8/gate.sh` prints `GATE PASS`. It checks the scope, that `opencode.json`
is gone, that `grep -nE "Godot|Twilight|clean copy" TODO.md docs/direction.md` prints
nothing, TODO.md's header, the creature paragraph (names `tripo_to_glb.py`, no meshkit), and
`cd rust && cargo fmt --all --check`.

## Out of scope; don't

- Don't edit `gates/` (checked byte for byte).
- Don't rewrite the history in TODO.md beyond the header and line 273; don't reformat it.
- Don't name any other game, company or franchise anywhere.
- Don't start Blender or build anything on this Mac.
