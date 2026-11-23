#!/usr/bin/env python3
"""Procedural 3-creature `retopo-4` benchmark & before/after render sheet generator.

Runs `cut.py decimate` (raw) vs `cut.py decimate-repaired` (4-stage Tripo hole
repair bridge) across three distinct procedural broken-Tripo creatures that carry
both Tripo-style defects (open holes, pinched figure-8 rims, 3+-face tangles,
unwelded seam cracks) and thin valid geometry (horns, spines, claws, wings,
tapered tails). Zero private corpus files are used.

    python3 bench/creature/retopo4_bench.py --out OUT_DIR [--tris 4500] [--size 480] [--gate]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import blind  # noqa: E402
import gamebench  # noqa: E402


def find_blender():
    env = os.environ.get("BLENDER")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    mac = "/Applications/Blender.app/Contents/MacOS/Blender"
    if os.path.isfile(mac) and os.access(mac, os.X_OK):
        return mac
    return shutil.which("blender")


BUILD_SUITE_SCRIPT = r"""
import json, math, os, sys
import bmesh, bpy
from mathutils import Vector

out_dir = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out_dir, exist_ok=True)


def add_ellipsoid(bm, center, radii, segs=40, rings=22):
    ret = bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=1.0)
    for v in ret["verts"]:
        v.co = Vector((
            v.co.x * radii[0] + center[0],
            v.co.y * radii[1] + center[1],
            v.co.z * radii[2] + center[2],
        ))


def add_tapered_limb(bm, p0, p1, r0, r1, segs=14, rings=10):
    v0, v1 = Vector(p0), Vector(p1)
    z = (v1 - v0).normalized()
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
            rv.append(bm.verts.new(c + (x * math.cos(ang) + y * math.sin(ang)) * r))
        ring_verts.append(rv)
    for k in range(rings):
        for s in range(segs):
            s2 = (s + 1) % segs
            bm.faces.new((ring_verts[k][s], ring_verts[k][s2], ring_verts[k + 1][s2], ring_verts[k + 1][s]))
    bm.faces.new(list(reversed(ring_verts[0])))
    bm.faces.new(ring_verts[-1])


def add_thin_wing(bm, root, tip, thickness=0.018, segs_u=22, segs_v=12):
    rx, ry, rz = root
    tx, ty, tz = tip
    sx = 1.0 if tx >= rx else -1.0
    grid = []
    for iu in range(segs_u + 1):
        u = iu / float(segs_u)
        chord = 0.42 * (1.0 - 0.92 * u)
        col = []
        for iv in range(segs_v + 1):
            v = iv / float(segs_v)
            x = rx + (tx - rx) * u
            y = ry + (ty - ry) * u + (v - 0.5) * chord
            z = rz + (tz - rz) * u + math.sin(u * math.pi) * 0.16
            col.append((x, y, z))
        grid.append(col)
    # Closed thin slab (top + bottom + rim), tapering toward a narrow fragile wingtip
    top_v = [[bm.verts.new(Vector((x, y, z + thickness * 0.5 * (1.0 - 0.60 * iu / segs_u)))) for (x, y, z) in col] for iu, col in enumerate(grid)]
    bot_v = [[bm.verts.new(Vector((x, y, z - thickness * 0.5 * (1.0 - 0.60 * iu / segs_u)))) for (x, y, z) in col] for iu, col in enumerate(grid)]
    for iu in range(segs_u):
        for iv in range(segs_v):
            a, b, c, d = top_v[iu][iv], top_v[iu + 1][iv], top_v[iu + 1][iv + 1], top_v[iu][iv + 1]
            bm.faces.new((a, b, c, d) if sx > 0 else (a, d, c, b))
            ab, bb, cb, db = bot_v[iu][iv], bot_v[iu + 1][iv], bot_v[iu + 1][iv + 1], bot_v[iu][iv + 1]
            bm.faces.new((ab, db, cb, bb) if sx > 0 else (ab, bb, cb, db))
    for iu in range(segs_u):
        for iv_edge, flip in ((0, sx > 0), (segs_v, sx <= 0)):
            fverts = (top_v[iu][iv_edge], bot_v[iu][iv_edge], bot_v[iu + 1][iv_edge], top_v[iu + 1][iv_edge])
            # Split cleanly with bot_v[iu+1]
            t1 = (top_v[iu][iv_edge], bot_v[iu][iv_edge], bot_v[iu + 1][iv_edge])
            t2 = (top_v[iu][iv_edge], bot_v[iu + 1][iv_edge], top_v[iu + 1][iv_edge])
            bm.faces.new(t1 if flip else t1[::-1])
            bm.faces.new(t2 if flip else t2[::-1])
    for iv in range(segs_v):
        for iu_edge, flip in ((0, sx <= 0), (segs_u, sx > 0)):
            t1 = (top_v[iu_edge][iv], bot_v[iu_edge][iv], bot_v[iu_edge][iv + 1])
            t2 = (top_v[iu_edge][iv], bot_v[iu_edge][iv + 1], top_v[iu_edge][iv + 1])
            bm.faces.new(t1 if flip else t1[::-1])
            bm.faces.new(t2 if flip else t2[::-1])


def export_bm(bm, path):
    me = bpy.data.meshes.new("m")
    bm.to_mesh(me)
    ob = bpy.data.objects.new("m", me)
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


def inject_defects(bm, front_hole_center, pinch_target, fin_target, crack_target):
    # 1) Open front/flank hole clearly visible from 3/4 game camera
    fx, fy, fz = front_hole_center
    kill_front = [
        f for f in bm.faces
        if (f.calc_center_median() - Vector((fx, fy, fz))).length < 0.16
    ]
    if kill_front:
        bmesh.ops.delete(bm, geom=kill_front, context="FACES")

    # 2) Pinched figure-8 hole on visible side
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    pv = min(
        [v for v in bm.verts if len(v.link_faces) >= 4],
        key=lambda v: (v.co - Vector(pinch_target)).length,
    )
    ring = list(pv.link_faces)
    if len(ring) >= 4:
        bmesh.ops.delete(bm, geom=[ring[0], ring[len(ring) // 2]], context="FACES")

    # 3) Non-manifold protruding baffle fin + duplicate-face tangle
    bm.edges.ensure_lookup_table()
    e_fin = min(
        [e for e in bm.edges if len(e.link_faces) == 2],
        key=lambda e: ((e.verts[0].co + e.verts[1].co) * 0.5 - Vector(fin_target)).length,
    )
    va, vb = e_fin.verts
    out_dir = ((va.co + vb.co) * 0.5 - Vector((0.0, 0.0, 0.75))).normalized()
    fin_tip = bm.verts.new((va.co + vb.co) * 0.5 + out_dir * 0.18)
    bm.faces.new((va, vb, fin_tip))
    # Also weld a duplicate triangle over one neighbor face (4 faces on edge)
    f0 = e_fin.link_faces[0]
    vs0 = list(f0.verts)
    vx = bm.verts.new(vs0[2].co + out_dir * 0.025)
    bm.faces.new((vs0[0], vs0[1], vx))

    # 4) Unwelded micro-crack (< 1e-5 * size)
    bm.faces.ensure_lookup_table()
    cf = min(bm.faces, key=lambda f: (f.calc_center_median() - Vector(crack_target)).length)
    cvs = list(cf.verts)
    vd = bm.verts.new(cvs[0].co + Vector((0.0, 0.0, 4e-6)))
    bmesh.ops.delete(bm, geom=[cf], context="FACES")
    bm.faces.new((vd, cvs[1], cvs[2]))


def build_creature(name, kind):
    cdir = os.path.join(out_dir, name)
    os.makedirs(cdir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bm = bmesh.new()
    tips = {}
    if kind == "quadruped":
        add_ellipsoid(bm, (0.0, 0.0, 0.72), (0.36, 0.60, 0.30), segs=64, rings=36)
        add_tapered_limb(bm, (0.0, -0.46, 0.78), (0.0, -0.74, 1.12), 0.14, 0.11, segs=24, rings=12)
        add_ellipsoid(bm, (0.0, -0.80, 1.16), (0.19, 0.23, 0.17), segs=36, rings=20)
        for sx in (-1.0, 1.0):
            for sy in (-0.36, 0.36):
                add_tapered_limb(bm, (sx * 0.25, sy, 0.66), (sx * 0.25, sy - 0.03, 0.34), 0.10, 0.075, segs=20, rings=12)
                add_tapered_limb(bm, (sx * 0.25, sy - 0.03, 0.34), (sx * 0.25, sy, 0.02), 0.075, 0.05, segs=20, rings=12)
                add_tapered_limb(bm, (sx * 0.25, sy - 0.03, 0.03), (sx * 0.25, sy - 0.11, 0.015), 0.016, 0.005, segs=14, rings=8)
        add_tapered_limb(bm, (-0.11, -0.78, 1.28), (-0.30, -0.68, 1.56), 0.044, 0.006, segs=18, rings=10)
        add_tapered_limb(bm, (0.11, -0.78, 1.28), (0.30, -0.68, 1.56), 0.044, 0.006, segs=18, rings=10)
        add_tapered_limb(bm, (0.0, 0.50, 0.72), (0.0, 1.08, 0.94), 0.08, 0.007, segs=20, rings=12)
        tips = {"horn_L": [-0.30, -0.68, 1.56], "horn_R": [0.30, -0.68, 1.56], "tail_tip": [0.0, 1.08, 0.94], "claw_FL": [-0.25, -0.47, 0.015]}
        defect_args = ((-0.26, -0.30, 0.78), (-0.33, 0.08, 0.74), (-0.28, -0.10, 0.96), (-0.24, 0.28, 0.84))
        focus_pt = (-0.26, -0.12, 0.80)
    elif kind == "biped":
        add_ellipsoid(bm, (0.0, 0.0, 1.12), (0.32, 0.24, 0.42), segs=64, rings=36)
        add_tapered_limb(bm, (0.0, -0.02, 1.46), (0.0, -0.08, 1.66), 0.11, 0.09, segs=24, rings=12)
        add_ellipsoid(bm, (0.0, -0.10, 1.76), (0.17, 0.19, 0.18), segs=36, rings=20)
        for sx in (-1.0, 1.0):
            add_tapered_limb(bm, (sx * 0.16, 0.0, 0.78), (sx * 0.18, -0.03, 0.40), 0.10, 0.075, segs=20, rings=12)
            add_tapered_limb(bm, (sx * 0.18, -0.03, 0.40), (sx * 0.18, 0.0, 0.02), 0.075, 0.052, segs=20, rings=12)
            add_tapered_limb(bm, (sx * 0.28, 0.0, 1.38), (sx * 0.46, -0.06, 1.05), 0.085, 0.06, segs=18, rings=10)
            add_tapered_limb(bm, (sx * 0.46, -0.06, 1.05), (sx * 0.52, -0.14, 0.72), 0.06, 0.04, segs=18, rings=10)
            add_tapered_limb(bm, (sx * 0.52, -0.14, 0.72), (sx * 0.54, -0.22, 0.62), 0.018, 0.005, segs=14, rings=8)
        add_tapered_limb(bm, (-0.10, -0.08, 1.88), (-0.28, -0.02, 2.18), 0.04, 0.006, segs=18, rings=10)
        add_tapered_limb(bm, (0.10, -0.08, 1.88), (0.28, -0.02, 2.18), 0.04, 0.006, segs=18, rings=10)
        for sz in (1.00, 1.18, 1.36):
            add_tapered_limb(bm, (0.0, 0.18, sz), (0.0, 0.38, sz + 0.08), 0.03, 0.005, segs=14, rings=8)
        tips = {"horn_L": [-0.28, -0.02, 2.18], "horn_R": [0.28, -0.02, 2.18], "spine_mid": [0.0, 0.38, 1.26], "claw_L": [-0.54, -0.22, 0.62]}
        defect_args = ((-0.16, -0.20, 1.14), (-0.28, -0.05, 1.04), (-0.12, -0.21, 1.30), (-0.20, 0.14, 1.12))
        focus_pt = (-0.18, -0.14, 1.14)
    else:
        add_ellipsoid(bm, (0.0, 0.0, 0.78), (0.42, 0.56, 0.36), segs=64, rings=36)
        add_tapered_limb(bm, (0.0, -0.44, 0.88), (0.0, -0.74, 1.18), 0.16, 0.13, segs=24, rings=12)
        add_ellipsoid(bm, (0.0, -0.82, 1.22), (0.20, 0.24, 0.19), segs=36, rings=20)
        for sx in (-1.0, 1.0):
            for sy in (-0.34, 0.34):
                add_tapered_limb(bm, (sx * 0.32, sy, 0.68), (sx * 0.32, sy, 0.02), 0.11, 0.065, segs=22, rings=14)
        add_thin_wing(bm, (-0.34, -0.04, 0.96), (-0.98, 0.24, 1.48), thickness=0.022)
        add_thin_wing(bm, (0.34, -0.04, 0.96), (0.98, 0.24, 1.48), thickness=0.022)
        add_tapered_limb(bm, (0.0, -0.76, 1.36), (0.0, -0.62, 1.66), 0.045, 0.006, segs=18, rings=10)
        add_tapered_limb(bm, (0.0, 0.48, 0.76), (0.0, 1.12, 0.92), 0.09, 0.008, segs=20, rings=12)
        tips = {"wing_L": [-0.98, 0.24, 1.48], "wing_R": [0.98, 0.24, 1.48], "crest": [0.0, -0.62, 1.66], "tail_tip": [0.0, 1.12, 0.92]}
        defect_args = ((-0.26, -0.34, 0.80), (-0.38, -0.02, 0.76), (-0.18, -0.42, 0.92), (-0.30, 0.24, 0.88))
        focus_pt = (-0.26, -0.24, 0.82)

    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    export_bm(bm, os.path.join(cdir, "intact_ref.obj"))

    inject_defects(bm, *defect_args)
    export_bm(bm, os.path.join(cdir, "broken.obj"))
    bm.free()
    with open(os.path.join(cdir, "meta.json"), "w") as fp:
        json.dump({"tips": tips, "focus": list(focus_pt)}, fp, indent=1)


for name, kind in (
    ("creature_1_quadruped", "quadruped"),
    ("creature_2_horned_biped", "biped"),
    ("creature_3_winged_heavy", "heavy"),
):
    build_creature(name, kind)
print("BENCH_SUITE_BUILT 3")
"""


SCORE_AND_RENDER_SCRIPT = r"""
import json, math, os, sys
import bmesh, bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

cand_obj, ref_obj, meta_json, out_json, out_whole_png, out_close_png, size_str = sys.argv[sys.argv.index("--") + 1:]
size = int(size_str)

def load_applied(path):
    bpy.ops.wm.obj_import(filepath=path)
    ob = next(o for o in reversed(bpy.context.scene.objects) if o.type == "MESH")
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return ob

bpy.ops.wm.read_factory_settings(use_empty=True)
cand = load_applied(cand_obj)
bm = bmesh.new()
bm.from_mesh(cand.data)
open_edges = sum(1 for e in bm.edges if len(e.link_faces) == 1)
non_manifold_edges = sum(1 for e in bm.edges if len(e.link_faces) > 2)
bsize = max(cand.dimensions) or 1.0
degen_faces = sum(1 for f in bm.faces if f.calc_area() < (1e-6 * bsize) ** 2)
tris = sum(len(f.verts) - 2 for f in bm.faces)
cv = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
cf = [tuple(p.vertices) for p in cand.data.polygons]
bm.free()

ref = load_applied(ref_obj)
rv = np.array([v.co[:] for v in ref.data.vertices], dtype=np.float64)
rf = [tuple(p.vertices) for p in ref.data.polygons]
diag = float(np.linalg.norm(np.ptp(rv, 0))) or 1.0
rbvh = BVHTree.FromPolygons([tuple(x) for x in rv], rf)
cbvh = BVHTree.FromPolygons([tuple(x) for x in cv], cf)
d1 = np.array([rbvh.find_nearest(tuple(p))[3] for p in cv])
d2 = np.array([cbvh.find_nearest(tuple(p))[3] for p in rv[::max(1, len(rv) // 6000)]])
both = np.concatenate([d1, d2]) / diag * 100.0
dist_mean = float(both.mean())
dist_p95 = float(np.percentile(both, 95))
dist_max = float(both.max())

with open(meta_json) as fp:
    meta = json.load(fp)
tip_errors = {}
for k, pt in meta["tips"].items():
    p = np.array(pt, dtype=np.float64)
    tip_errors[k] = round(float(np.linalg.norm(cv - p, axis=1).min()) / diag, 5)
thin_tip_max = max(tip_errors.values()) if tip_errors else 0.0

# Render clay + wireframe on contrasting slate-blue background with backface culling
# so open holes show unmistakably as gaps through to the background.
bpy.data.objects.remove(ref, do_unlink=True)
for p in cand.data.polygons:
    p.use_smooth = False
cand.data.materials.clear()
clay = bpy.data.materials.new("clay")
clay.diffuse_color = (0.76, 0.72, 0.65, 1.0)
cand.data.materials.append(clay)

edges = np.array([(cand.data.vertices[e.vertices[0]].co - cand.data.vertices[e.vertices[1]].co).length for e in cand.data.edges])
wire = cand.copy()
wire.data = cand.data.copy()
bpy.context.scene.collection.objects.link(wire)
wire.data.materials.clear()
wmat = bpy.data.materials.new("wire")
wmat.diffuse_color = (0.09, 0.10, 0.13, 1.0)
wire.data.materials.append(wmat)
wm = wire.modifiers.new("wire", "WIREFRAME")
wm.thickness = float(np.median(edges)) * 0.055
wm.use_replace = True
wm.use_even_offset = True

sc = bpy.context.scene
sc.render.engine = "BLENDER_WORKBENCH"
sc.display.shading.light = "STUDIO"
sc.display.shading.color_type = "MATERIAL"
sc.display.shading.show_cavity = False
sc.display.shading.show_backface_culling = True
sc.render.resolution_x = sc.render.resolution_y = size
sc.render.film_transparent = False
sc.world = bpy.data.worlds.new("w")
sc.world.color = (0.16, 0.21, 0.29)
sc.view_settings.view_transform = "Standard"

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
sc.collection.objects.link(cam)
sc.camera = cam
cam.data.lens = 50

lo, hi = np.percentile(rv, 1, axis=0), np.percentile(rv, 99, axis=0)
ctr = (lo + hi) / 2.0
ext = float(np.linalg.norm(hi - lo))
view_dir = Vector((-0.62, -1.0, 0.28)).normalized()
cam.location = Vector(ctr) + view_dir * (ext * 1.32)
cam.rotation_euler = (Vector(ctr) - cam.location).to_track_quat("-Z", "Y").to_euler()
cam.data.clip_start = ext * 0.01
cam.data.clip_end = ext * 20.0
sc.render.filepath = out_whole_png
bpy.ops.render.render(write_still=True)

# Close-up centered on defect region
fpt = Vector(meta["focus"])
cam.location = fpt + Vector((-0.78, -0.82, 0.18)).normalized() * (ext * 0.44)
cam.rotation_euler = (fpt - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.render.filepath = out_close_png
bpy.ops.render.render(write_still=True)

res = {
    "tris": tris,
    "open_edges_bmesh": open_edges,
    "non_manifold_edges": non_manifold_edges,
    "degen_faces": degen_faces,
    "dist_mean_pct": round(dist_mean, 4),
    "dist_p95_pct": round(dist_p95, 4),
    "dist_max_pct": round(dist_max, 4),
    "thin_tip_max_diag": round(thin_tip_max, 5),
    "tip_errors_diag": tip_errors,
}
with open(out_json, "w") as fp:
    json.dump(res, fp, indent=1, sort_keys=True)
"""


def compose_3col_sheet(rows, col_key, title, out_path):
    """Compose a labeled 3-column PNG sheet using `blind.py`'s stdlib PNG helpers."""
    tiles = []
    tw = th = None
    for idx, row in enumerate(rows, 1):
        c_src = row["source"]
        c_raw = row["decimate"]
        c_rep = row["decimate-repaired"]
        w0, h0, rgb0 = blind.read_png(c_src[col_key])
        w1, h1, rgb1 = blind.read_png(c_raw[col_key])
        w2, h2, rgb2 = blind.read_png(c_rep[col_key])
        tw, th = w0, h0
        tiles.append((idx, row["label"], c_src, c_raw, c_rep, rgb0, rgb1, rgb2))
    if not tiles or tw is None:
        return None
    top_h = 34
    cap_h = 38
    sw = tw * 3
    sh = top_h + len(tiles) * (th + cap_h)
    out = bytearray(sw * sh * 3)
    blind.fill_rect(out, sw, sh, 0, 0, sw, top_h, blind.BG_HEADER)
    blind.draw_text(out, sw, sh, 12, 11, title, scale=1, color=blind.FG_TITLE)

    for k, (idx, label, cs, cr, cp, r0, r1, r2) in enumerate(tiles):
        y0 = top_h + k * (th + cap_h)
        blind.fill_rect(out, sw, sh, 0, y0, sw, cap_h, blind.BG_HEADER)
        blind.fill_rect(out, sw, sh, 0, y0, sw, 1, blind.LINE_HEADER)
        c0_txt = f"C{idx} BROKEN: H{cs['holes']} OPEN {cs['open_edges']} NMAN {cs['non_manifold_edges']}"
        c1_txt = f"DECIMATE: H{cr['holes']} OPEN {cr['open_edges']} NMAN {cr['non_manifold_edges']}"
        c2_txt = f"REPAIRED: H{cp['holes']} OPEN {cp['open_edges']} NMAN {cp['non_manifold_edges']} D95 {cp['dist_p95_pct']:.2f}%"
        blind.draw_text(out, sw, sh, 10, y0 + 13, c0_txt, scale=1, color=blind.FG_TITLE)
        blind.draw_text(out, sw, sh, tw + 10, y0 + 13, c1_txt, scale=1, color=blind.FG_TITLE)
        blind.draw_text(out, sw, sh, 2 * tw + 10, y0 + 13, c2_txt, scale=1, color=blind.FG_TITLE)

        rb = tw * 3
        for y in range(th):
            dst = (y0 + cap_h + y) * sw * 3
            src = y * rb
            out[dst:dst + rb] = r0[src:src + rb]
            out[dst + rb:dst + 2 * rb] = r1[src:src + rb]
            out[dst + 2 * rb:dst + 3 * rb] = r2[src:src + rb]

    blind.write_png(out_path, sw, sh, out)
    return out_path


def run_bench(out_dir, tris=4500, size=440, gate=False):
    bl = find_blender()
    if not bl:
        raise SystemExit("retopo4_bench: Blender binary not found (set BLENDER or put blender on PATH)")
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "work")
    os.makedirs(work, exist_ok=True)

    build_py = os.path.join(work, "build_suite.py")
    with open(build_py, "w", encoding="utf-8") as fp:
        fp.write(BUILD_SUITE_SCRIPT)
    r_build = subprocess.run(
        [bl, "-b", "--factory-startup", "--python-exit-code", "1", "-P", build_py, "--", work],
        capture_output=True, text=True, timeout=300,
    )
    if r_build.returncode != 0:
        raise SystemExit(f"build_suite failed:\n{r_build.stdout}\n{r_build.stderr}")

    score_py = os.path.join(work, "score_render.py")
    with open(score_py, "w", encoding="utf-8") as fp:
        fp.write(SCORE_AND_RENDER_SCRIPT)

    creatures = [
        ("creature_1_quadruped", "QUADRUPED"),
        ("creature_2_horned_biped", "HORNED BIPED"),
        ("creature_3_winged_heavy", "WINGED HEAVY"),
    ]
    rows = []
    all_ok = True

    for cname, label in creatures:
        cdir = os.path.join(work, cname)
        intact = os.path.join(cdir, "intact_ref.obj")
        broken = os.path.join(cdir, "broken.obj")
        meta = os.path.join(cdir, "meta.json")

        cuts = {}
        # 1) Measure & render broken input
        src_json = os.path.join(cdir, "source.json")
        src_whole = os.path.join(cdir, "source.whole.png")
        src_close = os.path.join(cdir, "source.close.png")
        subprocess.run(
            [bl, "-b", "--factory-startup", "--python-exit-code", "1", "-P", score_py, "--",
             broken, intact, meta, src_json, src_whole, src_close, str(size)],
            check=True, capture_output=True, text=True, timeout=180,
        )
        src_m = json.load(open(src_json))
        sh, so = gamebench.obj_holes(broken)
        src_m.update(holes=sh, open_edges=so, whole_png=src_whole, close_png=src_close, elapsed_s=0.0)
        cuts["source"] = src_m

        # 2) Run cut.py decimate vs cut.py decimate-repaired
        for mode in ("decimate", "decimate-repaired"):
            cand_obj = os.path.join(cdir, f"{mode}.obj")
            rep_json = os.path.join(cdir, f"{mode}.repair.json")
            cmd = [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                   "-P", os.path.join(HERE, "cut.py"), "--", mode, broken, cand_obj, str(tris)]
            if mode == "decimate-repaired":
                cmd += ["--report", rep_json]
            t0 = time.monotonic()
            rc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            dt = round(time.monotonic() - t0, 2)
            if rc.returncode != 0:
                raise SystemExit(f"{cname} {mode} failed:\n{rc.stdout}\n{rc.stderr}")

            m_json = os.path.join(cdir, f"{mode}.metrics.json")
            w_png = os.path.join(cdir, f"{mode}.whole.png")
            cl_png = os.path.join(cdir, f"{mode}.close.png")
            subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1", "-P", score_py, "--",
                 cand_obj, intact, meta, m_json, w_png, cl_png, str(size)],
                check=True, capture_output=True, text=True, timeout=180,
            )
            m = json.load(open(m_json))
            h, oe = gamebench.obj_holes(cand_obj)
            m.update(holes=h, open_edges=oe, whole_png=w_png, close_png=cl_png, elapsed_s=dt)
            if os.path.isfile(rep_json):
                m["repair_report"] = json.load(open(rep_json))
            cuts[mode] = m

        cr = cuts["decimate-repaired"]
        cd = cuts["decimate"]
        creature_ok = (
            (cd["holes"] > 0 or cd["non_manifold_edges"] > 0)
            and cr["holes"] == 0
            and cr["open_edges"] == 0
            and cr["open_edges_bmesh"] == 0
            and cr["non_manifold_edges"] == 0
            and cr["degen_faces"] == 0
            and cr["dist_p95_pct"] < 0.6
            and cr["thin_tip_max_diag"] < 0.02
        )
        all_ok = all_ok and creature_ok
        rows.append({"name": cname, "label": label, "ok": creature_ok, **cuts})

    whole_sheet = compose_3col_sheet(
        rows, "whole_png",
        "RETOPO-4: BROKEN INPUT (L) VS DECIMATE (M) VS DECIMATE-REPAIRED (R)",
        os.path.join(out_dir, "retopo4_before_after_sheet.png"),
    )
    close_sheet = compose_3col_sheet(
        rows, "close_png",
        "RETOPO-4 CLOSE-UP: DEFECT REGION BEFORE (L/M) VS 4-STAGE REPAIR (R)",
        os.path.join(out_dir, "retopo4_closeup_sheet.png"),
    )

    lines = [
        "# retopo-4: cut.py 4-Stage Hole-Repair Bridge Benchmark",
        "",
        f"3 procedural broken-Tripo + thin-geo creatures cut to {tris} triangles (`decimate` vs `decimate-repaired`).",
        "",
        "| creature | src tris / holes / nman | decimate tris / holes / open / nman / d95% | repaired tris / holes / open / nman / d95% / tip_max / s | pass |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        cs, cd, cr = r["source"], r["decimate"], r["decimate-repaired"]
        lines.append(
            f"| {r['name']} | {cs['tris']} / {cs['holes']} / {cs['non_manifold_edges']} | "
            f"{cd['tris']} / {cd['holes']} / {cd['open_edges']} / {cd['non_manifold_edges']} / {cd['dist_p95_pct']:.3f}% | "
            f"{cr['tris']} / {cr['holes']} / {cr['open_edges']} / {cr['non_manifold_edges']} / {cr['dist_p95_pct']:.3f}% / {cr['thin_tip_max_diag']:.4f} / {cr['elapsed_s']:.2f}s | "
            f"{'PASS' if r['ok'] else 'FAIL'} |"
        )
    lines.append("")
    lines.append(f"Overall: {'PASS' if all_ok else 'FAIL'} ({sum(int(r['ok']) for r in rows)}/{len(rows)})")
    md = "\n".join(lines) + "\n"

    summary = {"tris": tris, "all_ok": all_ok, "rows": rows}
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=1, sort_keys=True)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fp:
        fp.write(md)

    fleet_art = os.environ.get("FLEET_ARTIFACTS")
    if fleet_art:
        os.makedirs(fleet_art, exist_ok=True)
        for p in (whole_sheet, close_sheet, os.path.join(out_dir, "results.json"), os.path.join(out_dir, "report.md")):
            if p and os.path.isfile(p):
                shutil.copy2(p, os.path.join(fleet_art, os.path.basename(p)))

    print(md)
    if gate and not all_ok:
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--tris", type=int, default=4500)
    ap.add_argument("--size", type=int, default=440)
    ap.add_argument("--gate", action="store_true")
    a = ap.parse_args()
    return run_bench(os.path.abspath(a.out), tris=a.tris, size=a.size, gate=a.gate)


if __name__ == "__main__":
    sys.exit(main())
