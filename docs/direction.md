# Direction: what retopo is for, and what to build next

## Where retopo sits on the creature and prop route (parked 2026-11-18; refreshed 2026-11-22)

**Status: parked (owner decision, 2026-11-18).** No active game route
calls `retopo` today. Current creature benchmarks live in
`bench/creature/` (`docs/log.md`, Round 6 + `retopo-2`/`-7` + `meshkit-1`):
five dev creatures plus three frozen held-out backlog creatures
(`bench/creature/heldout.json`), each rigged once by `skintokens`'s
creature route with the same weights transferred onto every cut
(`10,000` triangles for dev/held-out comparisons; each HLH prop at its
own count).

**Props: never through retopo.** HLH's props are built at budget by
the props kit (`~/Games/_tools/blender/props/`), already clean. Remeshing
one at its own triangle count only damages it: surface error (p95) 0.3
to 11.6% of its size, and thin parts lost (the ribcage came back with
1,370 of 4,160 triangles). A generated prop goes through `meshkit`'s
`clean_copy.py`, run by hand (closed shell, decimate to budget, bake),
like any generated mesh.

**Creatures: not through retopo today.** The production creature route
runs `motion/tools/tripo_rig.py`, which calls `motion/tools/tripo_to_glb.py`
(with `--holes 1.0` for class `monster`), and then hands the closed GLB to
`skintokens`'s creature route (`creature_route.py`) for rigging. Inside
`tripo_to_glb.py`, monsters do not get bare simplification alone — they go
through a 4-stage hole-repair + decimate pipeline (`untangle` and
`mend_holes` before the cut, Blender's `DECIMATE` modifier to `--tris`, a
second post-cut `untangle` and `mend_holes`, `cap_rims` for tangled rims,
and post-export GLB `close_rims_in_file`). On Round 6 (`docs/log.md`,
2026-11-16, 5 dev creatures at 10k triangles), plain `decimate` beat
`retopo` **5/5** on bend tears (`0.00–10.8%` vs `0.26–10.9%`), **5/5** on
0–100 bend score (`63.7–93.9` vs `44.6–79.1`), **5/5** on surface p95
(`0.10–0.19%` vs `1.38–2.56%`, **11.59x** median ratio against the
`<= 1.10x` gate), and **5/5** with the blind game-camera judge. `retopo`
left gaping back holes (`497` and `687` open edges on creatures 1 and 5;
`32` and `11` on creatures 2 and 3), melted horn crowns, claws, and dorsal
spikes into blobs, split connected meshes into islands it then dropped,
and ran **1,221–2,143 s** on creature 4 (`< 1 s` for decimate). (Round 1's
note that Blender's QuadriFlow refused three of five creatures was
withdrawn in Round 2 once meshes were scaled to 100 units across.)

**Why `bench/creature/cut.py` runs raw `decimate` separately (and what `retopo-7` / `meshkit-1` measured):**
On the benchmark, `bench/creature/cut.py decimate` runs Blender's bare
`DECIMATE` modifier directly on the already-prepped `dense.obj` (from
`bench/creature/prep.py`), intentionally skipping `tripo_to_glb.py`'s
4-stage `untangle`/`mend_holes`/`cap_rims`/`close_rims_in_file` pipeline so
the bench isolates raw decimation topology on clean geometry. That is why
raw `cut.py decimate` reopened `2`/`22`/`29` holes (`9`/`116`/`110` open
edges) on the 3 frozen held-out creatures in the valid `span/700`
`meshkit-1` run (`0a15ddc3`, 2026-11-21), whereas `tripo_to_glb.py` closes
post-cut holes in production. In that same `meshkit-1` run (`clean-copy` vs
raw `decimate`), `meshkit`'s `clean_copy.py` closed all holes (`0` vs `0`
dev; `0` vs `2/22/29` held-out) and won held-out bend (`2/3`), while
losing dev bend (`1/4`) and surface p95 on all `8/8` (`1.2428x` dev,
`1.4684x` held-out; `controls.valid=true`, blind PASS). Separately,
`retopo-2` and `retopo-7` (`9cfeefa1`, 2026-11-20) froze the 3 held-out
creatures in `bench/creature/heldout.json` and proved `budget_finder.py`
against `retopo-3`'s 8-frame posed game-camera check (`15,000` tris
`-16.7%` on `creature_1`, `17,999` tris on `creature_2`/`creature_3`, blind
`cant_tell` `3/3` PASS).

**When that changes (unpark roadmap for Opus 5.5 review):** `retopo` goes
back on the creature route only if it passes `~/Games/RETO.md` Gates 1–8
on both the 5 dev creatures and 3 held-out creatures: beating decimate on
bend tears on most creatures, keeping median surface p95 ratio `<= 1.10x`,
zero open edges on closed inputs, `< 5 min` runtime per cut, and winning
blind game-camera judging. Two tracks are queued for Opus 5.5's unpark
review:
1. **Low-cost bend check (`retopo-4`, `~/Games/_tools/docs/plans/retopo.md`):**
   one stop-ruled experiment (`cut.py decimate-weighted`) using Blender
   `DECIMATE`'s `vertex_group` biased toward joint rings and thin pointed
   horns/claws/spines (and away from unseen faces) to test whether weighted
   decimation beats plain decimate on tears before investing in a full
   remesher overhaul.
2. **Engine overhaul (`~/Games/RETO.md` 6 AutoRemesher faults + `docs/exoside-gap.md`):**
   fix in order: (1) stray spike vertices, (2) open holes on closed
   inputs, (3) split/dropped islands, (4) melted horns, spines, and claws,
   (5) pinned joint loops + automatic hard-edge/crease guide loops (plus
   layout-level symmetry and realized density painting), and (6) 20–35 min
   worst-case runtime (`< 5 min` target).

**What retopo is for meanwhile:** making a new base mesh for
`bodywrap` (`wrapforge`), by hand (guides, symmetry, density, then cleaned
in Blender), and one-off hard cases (gear, armor, capes, hair pieces)
where editable quad topology is wanted. The scope below still holds for
that.

---

Date: 2026-10-01. Owner-approved summary. Read this before proposing
engine work.

## Scope: retopo vs wrapforge

- **wrapforge** (`~/Games/_blender/bodywrap`, see its `PLAN.md`) wraps one good
  base mesh onto an AI shape. It handles anything that shares a body
  plan with a base: all humanoids (including humanoid monsters: goblins,
  orcs, skeletons) and creature families once a base exists (one
  quadruped base for every wolf-like enemy).
- **retopo** handles everything that fits no base:
  - one-off monsters and bosses (six arms, spiders, tentacles, slimes),
  - one-off **pieces** (any separate mesh attached to the body: capes,
    hair, armor, clothes, belts); a piece style reused on 3+ characters
    (a tunic, boots, gloves) can instead get its own piece base, built
    from the body base's faces so it inherits the body's weights, and be
    wrapped,
  - **making new bases**: remesh the first creature of a new body plan
    carefully (guides, symmetry, density), clean it by hand, rig it, and
    it becomes the base wrapforge reuses.
- **Rule of three**: if you will make 3 or more of the same shape (body
  plan or piece style), build a base once and wrap; a one-off -> remesh.

Faces and hands on humanoids are therefore wrapforge's job (the base
already has good loops and separate fingers). Do not spend retopo
effort on human face/hand topology.

## Engine direction: patch-layout back end

**Update 2026-10-01 (decision): do NOT build the rewrite yet.** On a real
AI character (owner corpus, 150k-tri input), at matched counts (~13.5k
quads) against QuadWild + Bi-MDF's best edge-flow config: ours 6.3%
irregular vs 7.8%, 10 vs 18 degrees corner error, equal mean surface
error; QuadWild wins worst-case error (2.0% vs 3.2%) and has 0 non-quads
(ours 186). The owner's visual check agreed ours looked cleaner. Do the
targeted fixes in `~/Games/_tools/docs/work-list.md` (formerly `roadmap.md`)
and `~/Games/_tools/docs/plans/retopo.md` instead; revisit the patch back
end only if a second character reverses this. The patch back end WILL be
built, as an **optional extension** (a selectable back end, e.g.
`--backend patch`, to try and compare), never a replacement: the current
engine stays the default, and the patch back end serves cases that need
all quads or tighter worst-case accuracy (its measured strengths). Owner
decision, 2026-10-01. The plan below describes that extension.

Evidence (`docs/igm-validity-spike.md`): the current back end (least-
squares cover -> one-shot rounding -> heuristic extraction) produces
folded uv maps, integer layouts that cannot be untangled, and tilings
that change under invisible input noise. Against the QuadWild + Bi-MDF
reference (black-box, GPL, not vendored; `bench/quadwild.py`) at
**equal quad counts** (`bench/matched.py`, 10 cases incl. the owner's
character) the result is mixed: QuadWild wins surface accuracy
(especially worst-case) and is always all-quads; ours wins irregular
vertices (8/10) and corner angles (8/10). (An earlier comparison at
unequal counts overstated QuadWild.) The patch back end should aim for
both: QuadWild's all-quad layout and fidelity with our field quality.

Build a Rust patch-layout back end (clean-room from the papers: QuadWild
2021, Bi-MDF quantization 2023), keeping the existing front end (weld,
isotropic remesh, cross field, singularity simplifier, density,
symmetry, guides). Patch boundaries come from three sources:

1. **automatic**: traced from the cross field's singularities;
2. **joint loops**: rings across each bone at elbows, knees, neck, tail
   base, jaw, placed from rigforge's landmarks
   (`~/Games/_blender/gamerig/tools/detect_landmarks.py`, biped and quadruped
   sets), because monsters bend there;
3. **owner strokes**: a few lines drawn in Blender (eye ring, spine
   line, jaw ring) become forced patch boundaries; the tool fills every
   patch with clean quads (sketch-based quad meshing, Takayama et al.
   2013).

Low LOD rungs are not a target for this back end: far rungs are a few
pixels tall, so decimating the hero mesh (the game engine's own LODs) is fine.
Optimize the hero mesh (roughly 5k-15k triangles per the pipeline doc).

## Measure deformation, not just the static mesh

`bench/score.py` judges a mesh standing still. For monsters the honest
test is a posed mesh: rig it, pose it (knee bent 90 degrees, neck
turned), and measure stretch and volume loss at the joints. This
deformation score becomes the main gate for the patch back end.

## First experiment (cheap, decides how much to build)

Run the deformation test on a few monsters in three versions: current
remesh, remesh with joint-loop guides, and a plain decimated triangle
mesh with a baked normal map. Small or background enemies may be fine
as triangles (the game engine renders triangles anyway), which would mean they
skip retopology entirely. The result decides how many monsters need the
full patch back end.
