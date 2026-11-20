#!/usr/bin/env python3
"""Tests for meshkit-1 portable creature-bench wiring (`cut.py clean-copy` & hole tracking).

Run standalone:
    REQUIRE_BLENDER=1 MESHKIT_DIR=/path/to/meshkit python3 bench/creature/test_clean_copy_cut.py
"""
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


def find_meshkit():
    env = os.environ.get("MESHKIT_DIR")
    if env and os.path.isfile(os.path.join(env, "tools", "clean_copy.py")):
        return os.path.abspath(env)
    sib = os.path.abspath(os.path.join(REPO_ROOT, "..", "meshkit"))
    if os.path.isfile(os.path.join(sib, "tools", "clean_copy.py")):
        return sib
    return None


def _write_box_obj(path, skip_faces=()):
    verts = [
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
        (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
    ]
    faces = [
        (1, 4, 3, 2),
        (5, 6, 7, 8),
        (1, 2, 6, 5),
        (3, 4, 8, 7),
        (2, 3, 7, 6),
        (4, 1, 5, 8),
    ]
    with open(path, "w") as f:
        for x, y, z in verts:
            f.write(f"v {x} {y} {z}\n")
        for idx, fc in enumerate(faces):
            if idx in skip_faces:
                continue
            f.write("f " + " ".join(str(i) for i in fc) + "\n")


class TestObjHoles(unittest.TestCase):
    def test_closed_and_open_boxes(self):
        with tempfile.TemporaryDirectory() as td:
            closed = os.path.join(td, "closed.obj")
            one_hole = os.path.join(td, "one_hole.obj")
            two_holes = os.path.join(td, "two_holes.obj")
            _write_box_obj(closed)
            _write_box_obj(one_hole, skip_faces=(0,))
            _write_box_obj(two_holes, skip_faces=(0, 1))

            self.assertEqual(gamebench.obj_holes(closed), (0, 0))
            self.assertEqual(gamebench.obj_holes(one_hole), (1, 4))
            self.assertEqual(gamebench.obj_holes(two_holes), (2, 8))


class TestVerdictHoles(unittest.TestCase):
    def _row(self, name, cc_holes, dec_holes, cc_tears=2.0, dec_tears=3.0):
        return {
            "name": name,
            "ok": True,
            "cuts": {
                "clean-copy": {
                    "bend": {"tears": cc_tears, "score": 88.0},
                    "dist_p95": 0.008,
                    "holes": cc_holes,
                    "open_edges": cc_holes * 4,
                },
                "decimate": {
                    "bend": {"tears": dec_tears, "score": 82.0},
                    "dist_p95": 0.010,
                    "holes": dec_holes,
                    "open_edges": dec_holes * 4,
                },
                "planted": {
                    "bend": {"tears": 25.0, "score": 40.0},
                    "dist_p95": 0.040,
                    "holes": dec_holes + 2,
                    "open_edges": (dec_holes + 2) * 4,
                },
                "control": {
                    "bend": {"tears": dec_tears, "score": 82.0},
                    "dist_p95": 0.010,
                    "holes": dec_holes,
                    "open_edges": dec_holes * 4,
                },
            },
        }

    def test_holes_no_worse_pass(self):
        rows = [self._row("c1", 0, 2), self._row("c2", 0, 0), self._row("c3", 1, 1)]
        v = gamebench.verdict(rows, tools=("clean-copy", "decimate"))
        self.assertTrue(v["holes_no_worse_on_all"])
        self.assertEqual(v["challenger_holes_max"], 1)
        self.assertEqual(v["baseline_holes_max"], 2)

    def test_holes_worse_fails_flag(self):
        rows = [self._row("c1", 0, 2), self._row("c2", 3, 1)]
        v = gamebench.verdict(rows, tools=("clean-copy", "decimate"))
        self.assertFalse(v["holes_no_worse_on_all"])
        self.assertEqual(v["challenger_holes_max"], 3)
        self.assertEqual(v["baseline_holes_max"], 2)


class TestCutCleanCopyBlender(unittest.TestCase):
    def test_bad_meshkit_dir_refuses_loudly(self):
        bl = find_blender()
        if not bl:
            if os.environ.get("REQUIRE_BLENDER") == "1":
                self.fail("REQUIRE_BLENDER=1 but no Blender binary found")
            self.skipTest("Blender not found")

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "box.obj")
            out = os.path.join(td, "out.obj")
            _write_box_obj(src)
            env = dict(os.environ, MESHKIT_DIR=os.path.join(td, "no_such_meshkit"))
            r = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(HERE, "cut.py"), "--", "clean-copy", src, out, "400"],
                capture_output=True, text=True, env=env,
            )
            combined = (r.stdout + "\n" + r.stderr)
            self.assertNotEqual(r.returncode, 0, "bad MESHKIT_DIR must fail non-zero")
            self.assertIn("MESHKIT_DIR", combined)

    def test_clean_copy_closes_open_creature_against_decimate(self):
        bl = find_blender()
        mk = find_meshkit()
        if not bl or not mk:
            if os.environ.get("REQUIRE_BLENDER") == "1":
                self.fail(f"REQUIRE_BLENDER=1 missing dependency: blender={bl!r} meshkit={mk!r}")
            self.skipTest(f"missing blender={bl!r} or meshkit={mk!r}")

        with tempfile.TemporaryDirectory() as td:
            src_glb = os.path.join(td, "open_creature.glb")
            # Build a dense procedural quadruped with an open belly hole so decimate
            # keeps a hole while clean-copy carves a watertight 0-hole shell.
            build_script = os.path.join(td, "make_creature.py")
            with open(build_script, "w") as f:
                f.write("""
import sys, bmesh, bpy
from mathutils import Matrix
out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bm = bmesh.new()
bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=32, radius=0.35)
for v in bm.verts:
    v.co = (v.co.x * 0.9, v.co.y * 1.4, v.co.z * 0.8 + 0.65)
# Cut an open hole in the underside of the torso
kill = [fc for fc in bm.faces if fc.calc_center_median().z < 0.42 and abs(fc.calc_center_median().x) < 0.14]
bmesh.ops.delete(bm, geom=kill, context="FACES")
for sx, sy in ((-0.16, -0.22), (0.16, -0.22), (-0.16, 0.22), (0.16, 0.22)):
    leg = bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=12, radius=1.0)
    bmesh.ops.transform(bm, matrix=Matrix.Translation((sx, sy, 0.25)) @ Matrix.Diagonal((0.08, 0.08, 0.28, 1.0)), verts=leg["verts"])
head = bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=16, radius=0.18)
bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, -0.52, 0.88)), verts=head["verts"])
me = bpy.data.meshes.new("creature")
bm.to_mesh(me); bm.free()
ob = bpy.data.objects.new("creature", me)
bpy.context.scene.collection.objects.link(ob)
bpy.ops.object.select_all(action="DESELECT")
ob.select_set(True)
bpy.context.view_layer.objects.active = ob
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True, export_yup=True)
""")
            r_build = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1", "-P", build_script, "--", src_glb],
                capture_output=True, text=True,
            )
            self.assertEqual(r_build.returncode, 0, r_build.stderr[-800:])

            dec_obj = os.path.join(td, "decimate.obj")
            cc_obj = os.path.join(td, "clean_copy.obj")
            env = dict(os.environ, MESHKIT_DIR=mk)

            r_dec = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(HERE, "cut.py"), "--", "decimate", src_glb, dec_obj, "1200"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(r_dec.returncode, 0, r_dec.stderr[-800:])
            self.assertTrue(os.path.isfile(dec_obj))

            r_cc = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(HERE, "cut.py"), "--", "clean-copy", src_glb, cc_obj, "1200"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(r_cc.returncode, 0, (r_cc.stdout + "\n" + r_cc.stderr)[-1200:])
            self.assertTrue(os.path.isfile(cc_obj))

            dec_tris, _ = gamebench.obj_census(dec_obj)
            cc_tris, _ = gamebench.obj_census(cc_obj)
            dec_holes, _ = gamebench.obj_holes(dec_obj)
            cc_holes, cc_open = gamebench.obj_holes(cc_obj)

            self.assertGreaterEqual(dec_holes, 1, "decimate should preserve the open source hole")
            self.assertEqual(cc_holes, 0, f"clean-copy must close holes (got {cc_holes} holes, {cc_open} open edges)")
            self.assertEqual(cc_open, 0, f"clean-copy must have 0 open edges (got {cc_open})")
            self.assertTrue(
                850 <= cc_tris <= 1550,
                f"clean-copy triangle count {cc_tris} far from budget 1200 (decimate={dec_tris})",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
