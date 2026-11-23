#!/usr/bin/env python3
"""Gate-first RED -> GREEN tests for the 4-stage hole-repair bridge in `cut.py` (`retopo-4`).

Run standalone:
    python3 bench/creature/test_hole_repair_cut.py
    REQUIRE_BLENDER=1 python3 bench/creature/test_hole_repair_cut.py
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import gamebench  # noqa: E402


def find_blender():
    env = os.environ.get("BLENDER")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    mac = "/Applications/Blender.app/Contents/MacOS/Blender"
    if os.path.isfile(mac) and os.access(mac, os.X_OK):
        return mac
    return shutil.which("blender")


def require_blender_or_skip(testcase):
    bl = find_blender()
    if not bl:
        if os.environ.get("REQUIRE_BLENDER") == "1":
            testcase.fail("REQUIRE_BLENDER=1 but no Blender binary found")
        testcase.skipTest("Blender not found")
    return bl


def _import_hole_repair(testcase):
    try:
        return importlib.import_module("hole_repair")
    except ImportError as exc:
        testcase.fail(f"hole_repair module missing (RED expected before bridge): {exc}")


def _obj_edges_summary(path):
    """Return (verts, faces, open_edges, non_manifold_edges) from raw OBJ indices."""
    verts = []
    faces = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("v "):
                parts = line.split()
                verts.append(tuple(float(x) for x in parts[1:4]))
            elif line.startswith("f "):
                vs = [int(tok.split("/")[0]) - 1 for tok in line.split()[1:]]
                if len(vs) >= 3:
                    faces.append(vs)
    edge_counts = {}
    for fc in faces:
        n = len(fc)
        for i in range(n):
            a, b = fc[i], fc[(i + 1) % n]
            if a == b:
                continue
            e = (a, b) if a < b else (b, a)
            edge_counts[e] = edge_counts.get(e, 0) + 1
    open_e = sum(1 for c in edge_counts.values() if c == 1)
    non_man = sum(1 for c in edge_counts.values() if c > 2)
    return verts, faces, open_e, non_man


def _write_simple_obj(path, verts, faces):
    with open(path, "w", encoding="utf-8") as f:
        for x, y, z in verts:
            f.write(f"v {x:.8f} {y:.8f} {z:.8f}\n")
        for fc in faces:
            f.write("f " + " ".join(str(i + 1) for i in fc) + "\n")


BUILD_BROKEN_CREATURE_SCRIPT = r"""
import json, math, os, sys
import bmesh, bpy
from mathutils import Matrix, Vector

out_dir = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out_dir, exist_ok=True)


def add_ellipsoid(bm, center, radii, segs=28, rings=16):
    ret = bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=1.0)
    for v in ret["verts"]:
        v.co = Vector((
            v.co.x * radii[0] + center[0],
            v.co.y * radii[1] + center[1],
            v.co.z * radii[2] + center[2],
        ))
    return ret["verts"]


def add_tapered_limb(bm, p0, p1, r0, r1, segs=14, rings=10):
    v0, v1 = Vector(p0), Vector(p1)
    axis = v1 - v0
    z = axis.normalized()
    up = Vector((0, 1, 0)) if abs(z.y) < 0.9 else Vector((1, 0, 0))
    x = z.cross(up).normalized()
    y = z.cross(x).normalized()
    ring_verts = []
    for k in range(rings + 1):
        t = k / float(rings)
        c = v0.lerp(v1, t)
        r = r0 * (1.0 - t) + r1 * t
        rv = []
        for s in range(segs):
            ang = 2.0 * math.pi * s / segs
            p = c + (x * math.cos(ang) + y * math.sin(ang)) * r
            rv.append(bm.verts.new(p))
        ring_verts.append(rv)
    for k in range(rings):
        for s in range(segs):
            s2 = (s + 1) % segs
            bm.faces.new((ring_verts[k][s], ring_verts[k][s2], ring_verts[k + 1][s2], ring_verts[k + 1][s]))
    bm.faces.new(list(reversed(ring_verts[0])))
    bm.faces.new(ring_verts[-1])
    return ring_verts[-1]


def build_base_creature():
    bm = bmesh.new()
    # Torso + neck + head
    add_ellipsoid(bm, (0.0, 0.0, 0.72), (0.34, 0.56, 0.28), segs=36, rings=20)
    add_tapered_limb(bm, (0.0, -0.44, 0.78), (0.0, -0.72, 1.10), 0.14, 0.11, segs=16, rings=8)
    add_ellipsoid(bm, (0.0, -0.78, 1.15), (0.18, 0.22, 0.17), segs=24, rings=14)
    # 4 legs with thin tapered claws
    for sx in (-1.0, 1.0):
        for sy in (-0.36, 0.36):
            hip = (sx * 0.24, sy, 0.66)
            knee = (sx * 0.24, sy - 0.03, 0.34)
            foot = (sx * 0.24, sy, 0.02)
            add_tapered_limb(bm, hip, knee, 0.10, 0.075, segs=14, rings=8)
            add_tapered_limb(bm, knee, foot, 0.075, 0.05, segs=14, rings=8)
            add_tapered_limb(bm, (sx * 0.24, sy - 0.03, 0.03), (sx * 0.24, sy - 0.11, 0.015), 0.015, 0.004, segs=8, rings=4)
    # Thin valid horns, dorsal spines, and tapered tail
    horn_l_tip = (-0.28, -0.66, 1.54)
    horn_r_tip = (0.28, -0.66, 1.54)
    add_tapered_limb(bm, (-0.10, -0.76, 1.26), horn_l_tip, 0.042, 0.006, segs=12, rings=8)
    add_tapered_limb(bm, (0.10, -0.76, 1.26), horn_r_tip, 0.042, 0.006, segs=12, rings=8)
    spine_tips = []
    for k, sy in enumerate((-0.18, 0.0, 0.18)):
        tip = (0.0, sy + 0.04, 1.18)
        spine_tips.append(tip)
        add_tapered_limb(bm, (0.0, sy, 0.94), tip, 0.028, 0.005, segs=10, rings=6)
    tail_tip = (0.0, 1.05, 0.92)
    add_tapered_limb(bm, (0.0, 0.48, 0.72), tail_tip, 0.08, 0.007, segs=12, rings=8)

    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    tips = {
        "horn_L": list(horn_l_tip),
        "horn_R": list(horn_r_tip),
        "spine_mid": list(spine_tips[1]),
        "tail_tip": list(tail_tip),
        "claw_FL": [-0.24, -0.47, 0.015],
    }
    return bm, tips


def export_bm(bm, path, name="creature"):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.wm.obj_export(
        filepath=path,
        export_selected_objects=True,
        export_uv=False,
        export_normals=False,
        export_materials=False,
    )
    bpy.data.objects.remove(ob, do_unlink=True)
    bpy.data.meshes.remove(me)


bpy.ops.wm.read_factory_settings(use_empty=True)
bm_ref, tips = build_base_creature()
export_bm(bm_ref, os.path.join(out_dir, "intact_ref.obj"), "intact_ref")

# Inject Tripo-style defects onto a copy:
# 1) Open belly hole
belly = [f for f in bm_ref.faces if f.calc_center_median().z < 0.48 and abs(f.calc_center_median().x) < 0.14 and abs(f.calc_center_median().y) < 0.22]
bmesh.ops.delete(bm_ref, geom=belly, context="FACES")

# 2) Pinched figure-8 hole on the right flank (two faces sharing only 1 pinch vertex)
bm_ref.verts.ensure_lookup_table()
bm_ref.faces.ensure_lookup_table()
pinch_v = min(
    [v for v in bm_ref.verts if v.co.x > 0.25 and abs(v.co.y) < 0.12 and 0.65 < v.co.z < 0.82 and len(v.link_faces) >= 4],
    key=lambda v: abs(v.co.y) + abs(v.co.z - 0.72),
)
ring_fs = list(pinch_v.link_faces)
if len(ring_fs) >= 4:
    # Delete two non-adjacent faces around pinch_v -> figure-8 open boundary!
    bmesh.ops.delete(bm_ref, geom=[ring_fs[0], ring_fs[len(ring_fs) // 2]], context="FACES")

# 3) Non-manifold tangles: a stray baffle fin welded to a torso edge (3 faces on edge)
#    plus a duplicate coincident face (4 faces on edge).
bm_ref.edges.ensure_lookup_table()
cand_edges = [
    e for e in bm_ref.edges
    if len(e.link_faces) == 2
    and e.verts[0].co.x < -0.22 and abs(e.verts[0].co.y) < 0.2 and 0.65 < e.verts[0].co.z < 0.85
]
e_fin = cand_edges[0]
va, vb = e_fin.verts
mid_in = bm_ref.verts.new((va.co + vb.co) * 0.5 + Vector((0.08, 0.0, 0.0)))
bm_ref.faces.new((va, vb, mid_in))  # edge now has 3 faces!

e_dup = cand_edges[min(4, len(cand_edges) - 1)]
f_orig = e_dup.link_faces[0]
vs_orig = list(f_orig.verts)
vc_off = bm_ref.verts.new(vs_orig[2].co + Vector((0.02, 0.01, 0.0)))
# Share edge (vs_orig[0], vs_orig[1]) twice -> non-manifold 4-face cluster
bm_ref.faces.new((vs_orig[0], vs_orig[1], vc_off))
v_extra = bm_ref.verts.new(vs_orig[2].co + Vector((0.03, -0.01, 0.0)))
bm_ref.faces.new((vs_orig[0], vs_orig[1], v_extra))

# 4) Unwelded duplicate-vertex micro-crack (< 1e-5 * size) on upper back
bm_ref.faces.ensure_lookup_table()
back_f = next(
    f for f in bm_ref.faces
    if abs(f.calc_center_median().x) > 0.15 and 0.18 < f.calc_center_median().y < 0.34 and f.calc_center_median().z > 0.82
)
old_vs = list(back_f.verts)
v_dup = bm_ref.verts.new(old_vs[0].co + Vector((0.0, 0.0, 4e-6)))
bmesh.ops.delete(bm_ref, geom=[back_f], context="FACES")
bm_ref.faces.new((v_dup, old_vs[1], old_vs[2]))

export_bm(bm_ref, os.path.join(out_dir, "broken_creature.obj"), "broken_creature")
bm_ref.free()

# Also build an open-hem cowl/cloak mesh (large intended opening > 0.5*size + small pinhole < 0.08*size + thin crest)
bm_cloak = bmesh.new()
add_ellipsoid(bm_cloak, (0.0, 0.0, 0.8), (0.35, 0.35, 0.55), segs=64, rings=36)
add_tapered_limb(bm_cloak, (0.0, 0.0, 1.32), (0.0, 0.18, 1.68), 0.04, 0.006, segs=12, rings=6)
bmesh.ops.triangulate(bm_cloak, faces=bm_cloak.faces[:])
bmesh.ops.recalc_face_normals(bm_cloak, faces=bm_cloak.faces[:])
# Large intended bottom hem opening (z < 0.42 -> rim ~ 1.9 * size)
hem = [f for f in bm_cloak.faces if f.calc_center_median().z < 0.42]
bmesh.ops.delete(bm_cloak, geom=hem, context="FACES")
# One small pinhole on the side (single triangle deleted -> rim ~ 0.07 * size, < 0.12 * size)
pinhole = next(
    f for f in bm_cloak.faces
    if f.calc_center_median().x > 0.28 and 0.70 < f.calc_center_median().z < 0.90
)
bmesh.ops.delete(bm_cloak, geom=[pinhole], context="FACES")
export_bm(bm_cloak, os.path.join(out_dir, "open_cloak.obj"), "open_cloak")
bm_cloak.free()

with open(os.path.join(out_dir, "tips.json"), "w") as fp:
    json.dump(tips, fp, indent=1)
print("BROKEN_FIXTURES_OK")
"""


INSPECT_OBJ_SCRIPT = r"""
import json, math, sys
import bmesh, bpy
import numpy as np
from mathutils.bvhtree import BVHTree

cand_obj, ref_obj, tips_json, out_json = sys.argv[sys.argv.index("--") + 1:]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.obj_import(filepath=cand_obj)
cand = next(o for o in bpy.context.scene.objects if o.type == "MESH")
bpy.context.view_layer.objects.active = cand
cand.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bm = bmesh.new()
bm.from_mesh(cand.data)
open_edges = sum(1 for e in bm.edges if len(e.link_faces) == 1)
non_manifold_edges = sum(1 for e in bm.edges if len(e.link_faces) > 2)
size = max(cand.dimensions) or 1.0
degen_faces = sum(1 for f in bm.faces if f.calc_area() < (1e-6 * size) ** 2)
cand_tris = sum(len(f.verts) - 2 for f in bm.faces)
cand_verts = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
cand_faces = [tuple(p.vertices) for p in cand.data.polygons]
bm.free()

bpy.ops.wm.obj_import(filepath=ref_obj)
ref = next(o for o in bpy.context.scene.objects if o.type == "MESH" and o is not cand)
bpy.ops.object.select_all(action="DESELECT")
ref.select_set(True)
bpy.context.view_layer.objects.active = ref
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
rv = np.array([v.co[:] for v in ref.data.vertices], dtype=np.float64)
rf = [tuple(p.vertices) for p in ref.data.polygons]
diag = float(np.linalg.norm(np.ptp(rv, 0))) or 1.0
rbvh = BVHTree.FromPolygons([tuple(x) for x in rv], rf)
cbvh = BVHTree.FromPolygons([tuple(x) for x in cand_verts], cand_faces)
d1 = np.array([rbvh.find_nearest(tuple(p))[3] for p in cand_verts])
d2 = np.array([cbvh.find_nearest(tuple(p))[3] for p in rv[::max(1, len(rv) // 4000)]])
both = np.concatenate([d1, d2]) / diag * 100.0
dist_p95 = float(np.percentile(both, 95))

with open(tips_json) as fp:
    tips = json.load(fp)
tip_errors = {}
for k, pt in tips.items():
    p = np.array(pt, dtype=np.float64)
    d = float(np.linalg.norm(cand_verts - p, axis=1).min()) / diag
    tip_errors[k] = round(d, 5)

res = {
    "tris": cand_tris,
    "open_edges": open_edges,
    "non_manifold_edges": non_manifold_edges,
    "degen_faces": degen_faces,
    "dist_p95_pct": round(dist_p95, 4),
    "tip_errors_diag": tip_errors,
}
with open(out_json, "w") as fp:
    json.dump(res, fp, indent=1, sort_keys=True)
"""


class Stage4ObjUnitTest(unittest.TestCase):
    def test_close_rims_in_obj_repairs_crack_sliver_and_figure8(self):
        hr = _import_hole_repair(self)
        with tempfile.TemporaryDirectory() as td:
            # 1) Open box (1 missing face)
            box = os.path.join(td, "open_box.obj")
            verts = [
                (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
            ]
            faces = [
                (0, 3, 2, 1),
                (0, 1, 5, 4),
                (2, 3, 7, 6),
                (1, 2, 6, 5),
                (3, 0, 4, 7),
            ]
            _write_simple_obj(box, verts, faces)
            self.assertEqual(gamebench.obj_holes(box), (1, 4))
            info = hr.close_rims_in_obj(box, limit=5.0)
            self.assertEqual(gamebench.obj_holes(box), (0, 0))
            self.assertGreaterEqual(info["closed"], 1)
            _, _, open_e, non_man = _obj_edges_summary(box)
            self.assertEqual(open_e, 0)
            self.assertEqual(non_man, 0)

            # 2) Unwelded crack (< 1e-5 * size) + zero-area sliver triangle
            crack = os.path.join(td, "crack.obj")
            c_verts = [
                (0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1),
                (0, 0, 1.0 + 4e-6),  # duplicate within 1e-5 * size
            ]
            c_faces = [
                (0, 2, 1),
                (0, 1, 3),
                (1, 2, 4),  # uses duplicate vertex 4 -> open edges
                (2, 0, 3),
                (0, 3, 4),  # zero-area sliver between 3 and 4
            ]
            _write_simple_obj(crack, c_verts, c_faces)
            self.assertGreater(gamebench.obj_holes(crack)[1], 0)
            hr.close_rims_in_obj(crack, limit=1.0)
            self.assertEqual(gamebench.obj_holes(crack), (0, 0))
            _, _, open_e, non_man = _obj_edges_summary(crack)
            self.assertEqual(open_e, 0)
            self.assertEqual(non_man, 0)

            # 3) Pinched figure-8 boundary (two open holes touching at shared center vertex 4)
            fig8 = os.path.join(td, "fig8.obj")
            # Closed octahedron-like double pyramid or grid? Use closed 3x3 cube-shell grid with two opposite corner quads removed sharing center vert!
            # Simpler: 8 outer verts + 1 center vert on front of a cube, two triangles removed sharing center vert 8.
            f_verts = [
                (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
                (0.5, 0.5, 1.0),  # 8: center on top face
            ]
            f_faces = [
                (0, 3, 2, 1),  # bottom
                (0, 1, 5, 4),  # front
                (1, 2, 6, 5),  # right
                (2, 3, 7, 6),  # back
                (3, 0, 4, 7),  # left
                # Top face split into 4 triangles around 8; omit (4, 5, 8) and (6, 7, 8) -> figure-8 sharing vertex 8!
                (5, 6, 8),
                (7, 4, 8),
            ]
            _write_simple_obj(fig8, f_verts, f_faces)
            self.assertGreater(gamebench.obj_holes(fig8)[1], 0)
            hr.close_rims_in_obj(fig8, limit=5.0)
            self.assertEqual(gamebench.obj_holes(fig8), (0, 0))
            _, _, open_e, non_man = _obj_edges_summary(fig8)
            self.assertEqual(open_e, 0)
            self.assertEqual(non_man, 0, "figure-8 rim closure must not introduce non-manifold edges")

            # 4) Perimeter limit respects --holes 0.08 (leaves open box untouched)
            open_big = os.path.join(td, "open_big.obj")
            _write_simple_obj(open_big, verts, faces)
            hr.close_rims_in_obj(open_big, limit=0.08)
            self.assertEqual(gamebench.obj_holes(open_big), (1, 4))


class CutHoleRepairBridgeTest(unittest.TestCase):
    def _build_fixtures(self, bl, td):
        script = os.path.join(td, "make_broken.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(BUILD_BROKEN_CREATURE_SCRIPT)
        r = subprocess.run(
            [bl, "-b", "--factory-startup", "--python-exit-code", "1", "-P", script, "--", td],
            capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(r.returncode, 0, f"fixture build failed:\n{r.stdout}\n{r.stderr}")

    def _inspect_obj(self, bl, td, cand_obj, ref_obj, tips_json, out_json):
        script = os.path.join(td, "inspect_obj.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(INSPECT_OBJ_SCRIPT)
        r = subprocess.run(
            [bl, "-b", "--factory-startup", "--python-exit-code", "1", "-P", script, "--",
             cand_obj, ref_obj, tips_json, out_json],
            capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(r.returncode, 0, f"inspect failed:\n{r.stdout}\n{r.stderr}")
        with open(out_json, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_broken_tripo_sculpt_repaired_without_eating_thin_geo(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as td:
            self._build_fixtures(bl, td)
            broken = os.path.join(td, "broken_creature.obj")
            intact = os.path.join(td, "intact_ref.obj")
            tips = os.path.join(td, "tips.json")

            # Verify raw decimate leaves open holes and non-manifold edges on broken Tripo input.
            raw_obj = os.path.join(td, "raw_decimate.obj")
            r_raw = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(HERE, "cut.py"), "--", "decimate", broken, raw_obj, "2400"],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(r_raw.returncode, 0, r_raw.stderr[-800:])
            raw_holes, raw_open = gamebench.obj_holes(raw_obj)
            self.assertGreaterEqual(raw_holes, 1, "raw decimate leaves broken holes open")
            self.assertGreater(raw_open, 0)
            raw_metrics = self._inspect_obj(bl, td, raw_obj, intact, tips, os.path.join(td, "raw_metrics.json"))
            self.assertGreater(raw_metrics["open_edges"], 0)
            self.assertGreater(raw_metrics["non_manifold_edges"], 0)

            # Bridge mode `decimate-repaired` must exist, seal all holes/tangles, and keep thin tips intact.
            rep_obj = os.path.join(td, "repaired_decimate.obj")
            rep_json = os.path.join(td, "repair_report.json")
            r_rep = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(HERE, "cut.py"), "--",
                 "decimate-repaired", broken, rep_obj, "2400", "--report", rep_json],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(
                r_rep.returncode, 0,
                f"cut.py decimate-repaired failed (RED expected before bridge):\n{r_rep.stdout}\n{r_rep.stderr}",
            )
            self.assertTrue(os.path.isfile(rep_obj))
            self.assertTrue(os.path.isfile(rep_json))

            rep_holes, rep_open = gamebench.obj_holes(rep_obj)
            self.assertEqual(rep_holes, 0, f"decimate-repaired left {rep_holes} holes ({rep_open} open edges)")
            self.assertEqual(rep_open, 0, f"decimate-repaired left {rep_open} open edges")

            metrics = self._inspect_obj(bl, td, rep_obj, intact, tips, os.path.join(td, "metrics.json"))
            self.assertEqual(metrics["open_edges"], 0, f"bmesh open_edges != 0: {metrics}")
            self.assertEqual(metrics["non_manifold_edges"], 0, f"non-manifold edges left: {metrics}")
            self.assertEqual(metrics["degen_faces"], 0, f"degenerate faces left: {metrics}")
            self.assertTrue(1800 <= metrics["tris"] <= 3000, f"triangle count {metrics['tris']} off target 2400")
            self.assertLess(metrics["dist_p95_pct"], 0.6, f"surface dist_p95 too high: {metrics}")
            for tip_name, err in metrics["tip_errors_diag"].items():
                self.assertLess(
                    err, 0.02,
                    f"thin valid feature {tip_name} eaten/blunted (nearest vert {err:.4f} * diag away): {metrics}",
                )

            with open(rep_json, "r", encoding="utf-8") as f:
                report = json.load(f)
            for key in ("tangles", "holes", "tangles_after_cut", "holes_after_cut", "capped", "holes_in_file"):
                self.assertIn(key, report, f"missing 4-stage telemetry key {key!r} in report")

    def test_holes_limit_respects_intended_openings_and_count_only_mode(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as td:
            self._build_fixtures(bl, td)
            cloak = os.path.join(td, "open_cloak.obj")

            # Small limit (0.12) closes the small pinhole while leaving the large bottom hem open.
            out_small = os.path.join(td, "cloak_small_holes.obj")
            r1 = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(HERE, "cut.py"), "--",
                 "decimate-repaired", cloak, out_small, "1500", "--holes", "0.12"],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(r1.returncode, 0, f"cut.py failed:\n{r1.stdout}\n{r1.stderr}")
            holes_s, _ = gamebench.obj_holes(out_small)
            self.assertEqual(holes_s, 1, f"expected 1 large opening left with --holes 0.12, got {holes_s}")

            # --holes 0 counts only and leaves both pinhole + hem open.
            out_zero = os.path.join(td, "cloak_zero_holes.obj")
            r0 = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(HERE, "cut.py"), "--",
                 "decimate", cloak, out_zero, "1500", "--repair", "--holes", "0"],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(r0.returncode, 0, f"cut.py failed:\n{r0.stdout}\n{r0.stderr}")
            holes_z, _ = gamebench.obj_holes(out_zero)
            self.assertEqual(holes_z, 2, f"expected 2 holes left with --holes 0, got {holes_z}")

    def test_decimate_repair_flag_matches_repaired_and_raw_stays_unrepaired(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as td:
            self._build_fixtures(bl, td)
            broken = os.path.join(td, "broken_creature.obj")

            out_raw = os.path.join(td, "raw.obj")
            r_raw = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(HERE, "cut.py"), "--", "decimate", broken, out_raw, "2400"],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(r_raw.returncode, 0, r_raw.stderr[-800:])
            self.assertGreaterEqual(gamebench.obj_holes(out_raw)[0], 1)

            out_flag = os.path.join(td, "flag_rep.obj")
            r_flag = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(HERE, "cut.py"), "--",
                 "decimate", broken, out_flag, "2400", "--repair"],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(r_flag.returncode, 0, f"cut.py --repair failed:\n{r_flag.stdout}\n{r_flag.stderr}")
            self.assertEqual(gamebench.obj_holes(out_flag), (0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
