#!/usr/bin/env python3
"""Protected tests for retopo-3: 8-frame posed game-camera silhouette + shading check.

    python3 bench/creature/test_posed_image_check.py
    REQUIRE_BLENDER=1 python3 bench/creature/test_posed_image_check.py
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gamebench as gb  # noqa: E402


def write_rgba_png(path, w, h, rgba):
    def chunk(ctype, data):
        return (
            struct.pack(">I", len(data))
            + ctype
            + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)
        raw.extend(rgba[y * stride:(y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 1))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(png)


def find_blender():
    env = os.environ.get("BLENDER")
    if env and os.path.exists(env):
        return env
    mac = "/Applications/Blender.app/Contents/MacOS/Blender"
    if os.path.exists(mac):
        return mac
    return shutil.which("blender")


# Procedural Blender generator for 5 distinct articulated creatures (dense OBJ + rigged GLB
# + decimate rigged GLB + planted coarse swapped-limb rigged GLB) so retopo-3's 5-of-5 gate
# runs hermetically anywhere Blender is installed without relying on private corpus files.
BLENDER_FIXTURE_SCRIPT = r"""
import math, os, sys
import bmesh, bpy
from mathutils import Vector

out_dir = sys.argv[sys.argv.index("--") + 1]
here = sys.argv[sys.argv.index("--") + 2]
sys.path.insert(0, here)
import game_frame as gf

CREATURES = [
    ("creature_quad",  dict(body=(0.35, 0.70, 0.28), leg_h=0.65, leg_x=0.28, leg_y=0.45, horns=False, tail=0.55, crest=False)),
    ("creature_horn",  dict(body=(0.32, 0.55, 0.34), leg_h=0.72, leg_x=0.26, leg_y=0.35, horns=True,  tail=0.40, crest=False)),
    ("creature_biped", dict(body=(0.34, 0.24, 0.55), leg_h=0.85, leg_x=0.22, leg_y=0.00, horns=False, tail=0.25, crest=True)),
    ("creature_wing",  dict(body=(0.28, 0.45, 0.32), leg_h=0.60, leg_x=0.24, leg_y=0.28, horns=True,  tail=0.65, crest=True)),
    ("creature_heavy", dict(body=(0.48, 0.68, 0.40), leg_h=0.55, leg_x=0.36, leg_y=0.42, horns=True,  tail=0.48, crest=False)),
]

def add_ellipsoid(bm, center, radii, segs=18, rings=10):
    ret = bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=1.0)
    for v in ret["verts"]:
        v.co = Vector((v.co.x * radii[0] + center[0], v.co.y * radii[1] + center[1], v.co.z * radii[2] + center[2]))

def add_limb(bm, p0, p1, r0, r1, segs=12, rings=8):
    v0, v1 = Vector(p0), Vector(p1)
    axis = v1 - v0
    length = axis.length
    z = axis.normalized()
    up = Vector((0, 1, 0)) if abs(z.y) < 0.9 else Vector((1, 0, 0))
    x = z.cross(up).normalized()
    y = z.cross(x).normalized()
    ring_verts = []
    for k in range(rings + 1):
        t = k / rings
        c = v0.lerp(v1, t)
        r = r0 * (1 - t) + r1 * t
        rv = []
        for s in range(segs):
            ang = 2 * math.pi * s / segs
            p = c + (x * math.cos(ang) + y * math.sin(ang)) * r
            rv.append(bm.verts.new(p))
        ring_verts.append(rv)
    for k in range(rings):
        for s in range(segs):
            s2 = (s + 1) % segs
            bm.faces.new((ring_verts[k][s], ring_verts[k][s2], ring_verts[k + 1][s2], ring_verts[k + 1][s]))
    bm.faces.new(list(reversed(ring_verts[0])))
    bm.faces.new(ring_verts[-1])

def build_creature(name, spec):
    cdir = os.path.join(out_dir, name)
    os.makedirs(cdir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    bx, by, bz = spec["body"]
    lh = spec["leg_h"]
    lx, ly = spec["leg_x"], spec["leg_y"]
    body_z = lh + bz * 0.7

    bm = bmesh.new()
    add_ellipsoid(bm, (0, 0, body_z), (bx, by, bz), segs=24, rings=14)
    # Head + neck
    head_y = -by - 0.25
    head_z = body_z + bz * 0.45
    add_limb(bm, (0, -by * 0.75, body_z + bz * 0.15), (0, head_y, head_z), 0.14, 0.11, segs=14, rings=8)
    add_ellipsoid(bm, (0, head_y - 0.08, head_z + 0.05), (0.18, 0.22, 0.17), segs=18, rings=10)
    if spec["horns"]:
        for sx in (-1, 1):
            add_limb(bm, (sx * 0.10, head_y, head_z + 0.14), (sx * 0.34, head_y + 0.18, head_z + 0.48), 0.055, 0.012, segs=10, rings=8)
    if spec["crest"]:
        add_limb(bm, (0, -0.05, body_z + bz * 0.8), (0, 0.28, body_z + bz * 1.45), 0.09, 0.02, segs=10, rings=8)
    # Tail
    tail_len = spec["tail"]
    add_limb(bm, (0, by * 0.85, body_z + 0.04), (0, by + tail_len, body_z + 0.18), 0.10, 0.025, segs=12, rings=8)
    # Limbs (2 or 4 legs + optional arms for biped)
    limb_defs = []
    if ly > 0.01:
        for side, sx in (("L", -1), ("R", 1)):
            for end, sy in (("front", -ly), ("back", ly)):
                hip = (sx * lx, sy, lh + 0.08)
                knee = (sx * lx * 1.05, sy - 0.04, lh * 0.5)
                foot = (sx * lx, sy, 0.02)
                add_limb(bm, hip, knee, 0.11, 0.08, segs=12, rings=8)
                add_limb(bm, knee, foot, 0.08, 0.055, segs=12, rings=8)
                limb_defs.append((f"leg_{end}_{side}", hip, knee, foot))
    else:
        for side, sx in (("L", -1), ("R", 1)):
            hip = (sx * lx, 0.0, lh + 0.05)
            knee = (sx * lx, -0.03, lh * 0.5)
            foot = (sx * lx, 0.0, 0.02)
            add_limb(bm, hip, knee, 0.12, 0.085, segs=12, rings=8)
            add_limb(bm, knee, foot, 0.085, 0.06, segs=12, rings=8)
            limb_defs.append((f"leg_{side}", hip, knee, foot))
            sh = (sx * (bx + 0.06), 0.0, body_z + bz * 0.45)
            el = (sx * (bx + 0.24), -0.04, body_z + bz * 0.10)
            hand = (sx * (bx + 0.30), -0.10, body_z - bz * 0.25)
            add_limb(bm, sh, el, 0.085, 0.065, segs=10, rings=6)
            add_limb(bm, el, hand, 0.065, 0.045, segs=10, rings=6)
            limb_defs.append((f"arm_{side}", sh, el, hand))

    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)

    # Export dense.obj
    dense_obj = os.path.join(cdir, "dense.obj")
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.wm.obj_export(filepath=dense_obj, export_selected_objects=True, export_uv=False, export_normals=False, export_materials=False)

    # Build simple armature matching game_frame.apply_stride_pose bone names
    arm_data = bpy.data.armatures.new(name + "_arm")
    arm = bpy.data.objects.new(name + "_arm", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones
    spine = eb.new("spine")
    spine.head = Vector((0, 0.1, body_z))
    spine.tail = Vector((0, -0.1, body_z + 0.1))
    neck = eb.new("neck")
    neck.head = Vector((0, -by * 0.7, body_z + bz * 0.15))
    neck.tail = Vector((0, head_y, head_z))
    neck.parent = spine
    tail = eb.new("tail")
    tail.head = Vector((0, by * 0.85, body_z + 0.04))
    tail.tail = Vector((0, by + tail_len, body_z + 0.18))
    tail.parent = spine
    bones_info = [("spine", spine.head.copy(), spine.tail.copy()),
                  ("neck", neck.head.copy(), neck.tail.copy()),
                  ("tail", tail.head.copy(), tail.tail.copy())]
    for bname, p0, p1, p2 in limb_defs:
        u = eb.new(bname)
        u.head, u.tail, u.parent = Vector(p0), Vector(p1), spine
        l = eb.new(bname + "_lower")
        l.head, l.tail, l.parent = Vector(p1), Vector(p2), u
        bones_info.append((bname, Vector(p0), Vector(p1)))
        bones_info.append((bname + "_lower", Vector(p1), Vector(p2)))
    bpy.ops.object.mode_set(mode="OBJECT")

    def skin_mesh(mesh_ob):
        mesh_ob.parent = arm
        mod = mesh_ob.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
        mesh_ob.vertex_groups.clear()
        vgs = {bn: mesh_ob.vertex_groups.new(name=bn) for bn, _, _ in bones_info}
        for v in mesh_ob.data.vertices:
            p = v.co
            dists = []
            for bn, h, t in bones_info:
                ab = t - h
                ab2 = max(ab.length_squared, 1e-8)
                u = max(0.0, min(1.0, (p - h).dot(ab) / ab2))
                closest = h + ab * u
                dists.append(((p - closest).length, bn))
            dists.sort()
            w0 = 1.0 / (dists[0][0] + 0.04) ** 2
            w1 = 1.0 / (dists[1][0] + 0.04) ** 2
            s = w0 + w1
            vgs[dists[0][1]].add([v.index], w0 / s, "REPLACE")
            vgs[dists[1][1]].add([v.index], w1 / s, "REPLACE")

    def export_pair(mesh_ob, path):
        bpy.ops.object.select_all(action="DESELECT")
        arm.select_set(True)
        mesh_ob.select_set(True)
        bpy.context.view_layer.objects.active = mesh_ob
        bpy.ops.export_scene.gltf(
            filepath=path, use_selection=True, export_format="GLB",
            export_materials="NONE", export_animations=False, export_skins=True,
        )

    skin_mesh(ob)
    export_pair(ob, os.path.join(cdir, "dense_rigged.glb"))

    # Decimate cut (~60% of dense faces)
    dec = ob.copy()
    dec.data = ob.data.copy()
    bpy.context.scene.collection.objects.link(dec)
    dec.modifiers.clear()
    m = dec.modifiers.new("dec", "DECIMATE")
    m.ratio = 0.55
    bpy.context.view_layer.objects.active = dec
    bpy.ops.object.modifier_apply(modifier=m.name)
    skin_mesh(dec)
    export_pair(dec, os.path.join(cdir, "decimate_rigged.glb"))
    bpy.data.objects.remove(dec, do_unlink=True)

    # Planted cut: coarse decimate (6% of faces) + swapped L/R limb weights
    plt = ob.copy()
    plt.data = ob.data.copy()
    bpy.context.scene.collection.objects.link(plt)
    plt.modifiers.clear()
    m2 = plt.modifiers.new("dec", "DECIMATE")
    m2.ratio = 0.06
    bpy.context.view_layer.objects.active = plt
    bpy.ops.object.modifier_apply(modifier=m2.name)
    skin_mesh(plt)
    gf.swap_limb_groups(plt)
    export_pair(plt, os.path.join(cdir, "planted_rigged.glb"))
    bpy.data.objects.remove(plt, do_unlink=True)

for name, spec in CREATURES:
    build_creature(name, spec)
print("FIXTURES_BUILT", len(CREATURES))
"""


class PosedImageMetricsTest(unittest.TestCase):
    def test_identical_rgba_frames_score_exact_zero(self):
        import posed_image_metrics as pim

        with tempfile.TemporaryDirectory() as tmp:
            w, h = 32, 32
            rgba = bytearray(w * h * 4)
            for y in range(6, 26):
                for x in range(8, 24):
                    o = (y * w + x) * 4
                    rgba[o:o + 4] = bytes((120 + x, 90 + y, 160, 255))
            p1 = os.path.join(tmp, "f1.png")
            p2 = os.path.join(tmp, "f2.png")
            write_rgba_png(p1, w, h, rgba)
            write_rgba_png(p2, w, h, rgba)

            m = pim.compare_frame_pair(p1, p2)
            self.assertEqual(m["sil_diff_pct"], 0.0)
            self.assertEqual(m["shade_rmse"], 0.0)
            self.assertEqual(m["shade_l1_pct"], 0.0)
            agg = pim.aggregate_frames([m] * 8)
            self.assertEqual(agg["frames"], 8)
            self.assertEqual(agg["sil_diff_mean_pct"], 0.0)
            self.assertEqual(agg["shade_rmse_mean"], 0.0)

    def test_shifted_and_reshaded_frames_score_strictly_positive(self):
        import posed_image_metrics as pim

        with tempfile.TemporaryDirectory() as tmp:
            w, h = 32, 32
            ref = bytearray(w * h * 4)
            cand = bytearray(w * h * 4)
            for y in range(6, 26):
                for x in range(8, 24):
                    o = (y * w + x) * 4
                    ref[o:o + 4] = bytes((180, 170, 160, 255))
            for y in range(6, 26):
                for x in range(12, 28):
                    o = (y * w + x) * 4
                    cand[o:o + 4] = bytes((100, 95, 90, 255))
            p1 = os.path.join(tmp, "ref.png")
            p2 = os.path.join(tmp, "cand.png")
            write_rgba_png(p1, w, h, ref)
            write_rgba_png(p2, w, h, cand)

            m = pim.compare_frame_pair(p1, p2)
            self.assertGreater(m["sil_diff_pct"], 25.0)
            self.assertGreater(m["shade_rmse"], 40.0)
            self.assertGreater(m["shade_l1_pct"], 15.0)

    def test_verdict_checks_planted_image_worse_when_image_check_present(self):
        def make_row(pl_sil, pl_shade):
            return {
                "subject": "c1",
                "ok": True,
                "cuts": {
                    "retopo": {
                        "bend": {"tears": 5.0, "score": 80.0},
                        "dist_p95": 0.20,
                        "image_check": {"frames": 8, "sil_diff_mean_pct": 1.2, "shade_rmse_mean": 4.0},
                    },
                    "decimate": {
                        "bend": {"tears": 8.0, "score": 70.0},
                        "dist_p95": 0.20,
                        "image_check": {"frames": 8, "sil_diff_mean_pct": 1.0, "shade_rmse_mean": 3.5},
                    },
                    "planted": {
                        "bend": {"tears": 25.0, "score": 20.0},
                        "dist_p95": 1.40,
                        "image_check": {"frames": 8, "sil_diff_mean_pct": pl_sil, "shade_rmse_mean": pl_shade},
                    },
                },
            }

        v_ok = gb.verdict([make_row(9.5, 18.0)], tools=("retopo", "decimate"))
        self.assertTrue(v_ok["controls"]["valid"])
        self.assertTrue(v_ok["controls"]["planted_image_worse_on_all"])

        v_bad = gb.verdict([make_row(0.5, 1.0)], tools=("retopo", "decimate"))
        self.assertFalse(v_bad["controls"]["planted_image_worse_on_all"])
        self.assertFalse(v_bad["controls"]["valid"])
        self.assertIsNone(v_bad["winner"])

    def test_five_creatures_planted_worse_than_decimate_and_self_is_zero(self):
        blender = find_blender()
        if not blender:
            if os.environ.get("REQUIRE_BLENDER"):
                self.fail("REQUIRE_BLENDER=1 but no Blender executable found")
            self.skipTest("Blender not installed")

        check_script = os.path.join(HERE, "posed_image_check.py")
        self.assertTrue(os.path.isfile(check_script), "missing bench/creature/posed_image_check.py")

        with tempfile.TemporaryDirectory() as tmp:
            gen_py = os.path.join(tmp, "make_creatures.py")
            with open(gen_py, "w") as f:
                f.write(BLENDER_FIXTURE_SCRIPT)
            r = subprocess.run(
                [blender, "-b", "--factory-startup", "-P", gen_py, "--", tmp, HERE],
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertEqual(r.returncode, 0, f"fixture gen failed:\n{r.stdout}\n{r.stderr}")

            creatures = [
                "creature_quad",
                "creature_horn",
                "creature_biped",
                "creature_wing",
                "creature_heavy",
            ]
            for name in creatures:
                cdir = os.path.join(tmp, name)
                dense_obj = os.path.join(cdir, "dense.obj")
                dense_glb = os.path.join(cdir, "dense_rigged.glb")
                dec_glb = os.path.join(cdir, "decimate_rigged.glb")
                plt_glb = os.path.join(cdir, "planted_rigged.glb")
                cache = os.path.join(cdir, "ref_cache")

                # 1) Decimate against itself MUST score exact 0.0 over 8 frames.
                self_json = os.path.join(cdir, "self.json")
                r_self = subprocess.run(
                    [
                        blender, "-b", "--factory-startup", "-P", check_script, "--",
                        dec_glb, dec_glb, dense_obj, self_json,
                        "--frames", "8", "--width", "256", "--height", "288",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                self.assertEqual(r_self.returncode, 0, f"{name} self failed:\n{r_self.stdout}\n{r_self.stderr}")
                self_res = json.load(open(self_json))
                self.assertEqual(self_res["frames"], 8, f"{name}: {self_res}")
                self.assertEqual(self_res["sil_diff_mean_pct"], 0.0, f"{name}: {self_res}")
                self.assertEqual(self_res["shade_rmse_mean"], 0.0, f"{name}: {self_res}")

                # 2) Decimate vs dense source, and planted vs dense source (sharing ref cache).
                dec_json = os.path.join(cdir, "dec.json")
                plt_json = os.path.join(cdir, "plt.json")
                for cand_glb, out_json in ((dec_glb, dec_json), (plt_glb, plt_json)):
                    rc = subprocess.run(
                        [
                            blender, "-b", "--factory-startup", "-P", check_script, "--",
                            dense_glb, cand_glb, dense_obj, out_json,
                            "--frames", "8", "--width", "256", "--height", "288", "--cache", cache,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=300,
                    )
                    self.assertEqual(rc.returncode, 0, f"{name} {cand_glb} failed:\n{rc.stdout}\n{rc.stderr}")

                dec_res = json.load(open(dec_json))
                plt_res = json.load(open(plt_json))
                self.assertEqual(dec_res["frames"], 8)
                self.assertEqual(plt_res["frames"], 8)
                self.assertGreater(
                    plt_res["sil_diff_mean_pct"],
                    dec_res["sil_diff_mean_pct"],
                    f"{name}: planted sil {plt_res['sil_diff_mean_pct']} must beat decimate {dec_res['sil_diff_mean_pct']}",
                )
                self.assertGreater(
                    plt_res["shade_rmse_mean"],
                    dec_res["shade_rmse_mean"],
                    f"{name}: planted shade {plt_res['shade_rmse_mean']} must beat decimate {dec_res['shade_rmse_mean']}",
                )


if __name__ == "__main__":
    unittest.main()
