#!/usr/bin/env python3
"""Gate-first RED -> GREEN tests for Wave 4H determinism, edge cases, and ResourceWarning hygiene.

Run standalone:
    python3 bench/creature/test_w4_determinism.py
    REQUIRE_BLENDER=1 python3 bench/creature/test_w4_determinism.py
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CUT_PY = os.path.join(HERE, "cut.py")


def find_blender():
    env = os.environ.get("BLENDER")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    for cand in (
        "/Applications/Blender.app/Contents/MacOS/Blender",
        os.path.expanduser("~/.local/bin/blender"),
    ):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return shutil.which("blender")


def require_blender_or_skip(testcase):
    bl = find_blender()
    if not bl:
        if os.environ.get("REQUIRE_BLENDER") == "1":
            testcase.fail("REQUIRE_BLENDER=1 but no Blender binary found")
        testcase.skipTest("Blender not found")
    return bl


def run_cut(blender_bin, args):
    env = dict(os.environ)
    env["PYTHONWARNINGS"] = "error::ResourceWarning"
    cmd = [
        blender_bin,
        "-b",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        CUT_PY,
        "--",
    ] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


def make_empty_glb_bytes():
    doc = b'{"asset":{"version":"2.0"}}'
    while len(doc) % 4:
        doc += b" "
    total = 12 + 8 + len(doc)
    return (
        b"glTF"
        + struct.pack("<II", 2, total)
        + struct.pack("<II", len(doc), 0x4E4F534A)
        + doc
    )


def make_nan_glb_bytes():
    bin_bytes = struct.pack(
        "<9f3H2x",
        float("nan"), 0.0, 0.0,
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
        0, 1, 2,
    )
    doc = (
        b'{"asset":{"version":"2.0"},'
        b'"buffers":[{"byteLength":44}],'
        b'"bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":36},'
        b'{"buffer":0,"byteOffset":36,"byteLength":6}],'
        b'"accessors":[{"bufferView":0,"componentType":5126,"count":3,"type":"VEC3"},'
        b'{"bufferView":1,"componentType":5123,"count":3,"type":"SCALAR"}],'
        b'"meshes":[{"primitives":[{"attributes":{"POSITION":0},"indices":1}]}],'
        b'"nodes":[{"mesh":0}],"scenes":[{"nodes":[0]}],"scene":0}'
    )
    while len(doc) % 4:
        doc += b" "
    total = 12 + 8 + len(doc) + 8 + len(bin_bytes)
    return (
        b"glTF"
        + struct.pack("<II", 2, total)
        + struct.pack("<II", len(doc), 0x4E4F534A)
        + doc
        + struct.pack("<II", len(bin_bytes), 0x004E4942)
        + bin_bytes
    )


BUILD_BROKEN_MESH_SCRIPT = r"""
import os, sys
import bmesh, bpy
from mathutils import Vector

out_dir = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bm = bmesh.new()
bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0)
for v in bm.verts:
    # Asymmetric perturbation + UV sphere-like proportions
    v.co = Vector((
        v.co.x * (1.1 + 0.08 * v.co.y),
        v.co.y * 0.85,
        v.co.z * (1.25 - 0.05 * v.co.x),
    ))
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.faces.ensure_lookup_table()
# Knock out one triangle for an open small hole and add a T-fin for untangle.
victim = max(bm.faces, key=lambda f: f.calc_center_median().x)
fin_edge = victim.edges[0]
bm.faces.remove(victim)
v_tip = bm.verts.new(fin_edge.verts[0].co + Vector((0.02, 0.03, 0.04)))
bm.faces.new((fin_edge.verts[0], fin_edge.verts[1], v_tip))
mesh = bpy.data.meshes.new("BrokenCreature")
bm.to_mesh(mesh)
bm.free()
ob = bpy.data.objects.new("BrokenCreature", mesh)
bpy.context.collection.objects.link(ob)
bpy.context.view_layer.objects.active = ob
ob.select_set(True)

bpy.ops.wm.obj_export(
    filepath=os.path.join(out_dir, "broken.obj"),
    export_selected_objects=True,
    export_triangulated_mesh=True,
    export_uv=False,
    export_normals=False,
    export_materials=False,
)
bpy.ops.export_scene.gltf(
    filepath=os.path.join(out_dir, "broken.glb"),
    export_format="GLB",
    use_selection=True,
    export_apply=True,
    export_yup=True,
)
"""


class PythonHygieneAndShellTest(unittest.TestCase):
    def test_shell_scripts_pass_bash_n(self):
        scripts = []
        for root, _, files in os.walk(REPO_ROOT):
            if any(skip in root for skip in ("/.git", "/rust/target", "/bench/models", "/bench/results")):
                continue
            for fn in files:
                if fn.endswith(".sh"):
                    scripts.append(os.path.join(root, fn))
        self.assertGreaterEqual(len(scripts), 8)
        for sh_path in sorted(scripts):
            r = subprocess.run(["bash", "-n", sh_path], capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 0, f"bash -n failed for {sh_path}:\n{r.stderr}")

    def test_python_unit_suites_clean_under_error_resource_warning(self):
        suites = [
            os.path.join(REPO_ROOT, "bench", "test_rings.py"),
            os.path.join(REPO_ROOT, "bench", "creature", "test_budget_finder.py"),
            os.path.join(REPO_ROOT, "bench", "creature", "test_gamebench.py"),
            os.path.join(REPO_ROOT, "bench", "creature", "test_posed_image_check.py"),
            os.path.join(REPO_ROOT, "bench", "creature", "test_w2_hardening.py"),
            os.path.join(REPO_ROOT, "bench", "creature", "test_harness_fixes.py"),
        ]
        env = dict(os.environ)
        env["PYTHONWARNINGS"] = "error::ResourceWarning"
        for script in suites:
            r = subprocess.run(
                [sys.executable, "-W", "error::ResourceWarning", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            combined = r.stdout + "\n" + r.stderr
            self.assertEqual(
                r.returncode,
                0,
                f"{os.path.relpath(script, REPO_ROOT)} failed under -W error::ResourceWarning:\n{combined}",
            )
            self.assertNotIn(
                "ResourceWarning",
                combined,
                f"{os.path.relpath(script, REPO_ROOT)} emitted ResourceWarning:\n{combined}",
            )

        # Also exercise bench/rings.py directly under -W error::ResourceWarning so
        # its landmark JSON / OBJ reader paths execute cleanly.
        with tempfile.TemporaryDirectory() as tmp:
            mesh_obj = os.path.join(tmp, "cyl.obj")
            lm_json = os.path.join(tmp, "lm.json")
            out_rings = os.path.join(tmp, "rings.txt")
            with open(mesh_obj, "w", encoding="utf-8") as f:
                f.write(
                    "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
                    "v 0 0 2\nv 1 0 2\nv 1 1 2\nv 0 1 2\n"
                    "f 1 2 6 5\nf 2 3 7 6\nf 3 4 8 7\nf 4 1 5 8\n"
                )
            with open(lm_json, "w", encoding="utf-8") as f:
                json.dump({"kind": "biped", "neck_z": 1.0}, f)
            r_rings = subprocess.run(
                [
                    sys.executable,
                    "-W",
                    "error::ResourceWarning",
                    os.path.join(REPO_ROOT, "bench", "rings.py"),
                    "--mesh",
                    mesh_obj,
                    "--landmarks",
                    lm_json,
                    "--joints",
                    "neck",
                    "--output",
                    out_rings,
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            rings_out = r_rings.stdout + "\n" + r_rings.stderr
            self.assertEqual(
                r_rings.returncode,
                0,
                f"bench/rings.py failed under -W error::ResourceWarning:\n{rings_out}",
            )
            self.assertNotIn(
                "ResourceWarning",
                rings_out,
                f"bench/rings.py emitted ResourceWarning:\n{rings_out}",
            )


class CutDeterminismAndEdgeCasesTest(unittest.TestCase):
    def test_cut_decimate_repaired_repeat_runs_byte_identical_obj_and_glb(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            prep_py = os.path.join(tmp, "prep.py")
            with open(prep_py, "w", encoding="utf-8") as f:
                f.write(BUILD_BROKEN_MESH_SCRIPT)
            p = subprocess.run(
                [bl, "-b", "--factory-startup", "--python-exit-code", "1", "--python", prep_py, "--", tmp],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(p.returncode, 0, f"fixture prep failed:\n{p.stdout}\n{p.stderr}")

            for ext in ("obj", "glb"):
                src = os.path.join(tmp, f"broken.{ext}")
                out1 = os.path.join(tmp, f"out_{ext}_1.obj")
                out2 = os.path.join(tmp, f"out_{ext}_2.obj")
                rep1 = os.path.join(tmp, f"rep_{ext}_1.json")
                rep2 = os.path.join(tmp, f"rep_{ext}_2.json")

                r1 = run_cut(bl, ["decimate-repaired", src, out1, "400", "--holes", "5", "--report", rep1])
                r2 = run_cut(bl, ["decimate-repaired", src, out2, "400", "--holes", "5", "--report", rep2])
                self.assertEqual(r1.returncode, 0, f"run 1 failed ({ext}):\n{r1.stdout}\n{r1.stderr}")
                self.assertEqual(r2.returncode, 0, f"run 2 failed ({ext}):\n{r2.stdout}\n{r2.stderr}")
                self.assertNotIn("ResourceWarning", r1.stdout + "\n" + r1.stderr)
                self.assertNotIn("ResourceWarning", r2.stdout + "\n" + r2.stderr)

                with open(out1, "rb") as f1, open(out2, "rb") as f2:
                    b1, b2 = f1.read(), f2.read()
                self.assertGreater(len(b1), 0)
                self.assertEqual(b1, b2, f"cut.py decimate-repaired ({ext}) output OBJ drifted across runs")

                with open(rep1, "rb") as f1, open(rep2, "rb") as f2:
                    j1, j2 = f1.read(), f2.read()
                self.assertEqual(j1, j2, f"cut.py decimate-repaired ({ext}) report JSON drifted across runs")
                parsed = json.loads(j1.decode("utf-8"))
                self.assertEqual(parsed["holes_in_file"]["left"], 0)

                lines1 = [ln for ln in r1.stdout.splitlines() if ln.startswith("CUT_REPAIR ")]
                lines2 = [ln for ln in r2.stdout.splitlines() if ln.startswith("CUT_REPAIR ")]
                self.assertEqual(len(lines1), 1)
                self.assertEqual(lines1, lines2, "CUT_REPAIR stdout line must be deterministic")

    def test_cut_invalid_cli_flags_fail_cleanly_without_tracebacks(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            box = os.path.join(tmp, "box.obj")
            with open(box, "w", encoding="utf-8") as f:
                f.write(
                    "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
                    "v 0 0 1\nv 1 0 1\nv 1 1 1\nv 0 1 1\n"
                    "f 1 4 3 2\nf 5 6 7 8\nf 1 2 6 5\nf 3 4 8 7\nf 2 3 7 6\nf 4 1 5 8\n"
                )
            out = os.path.join(tmp, "out.obj")
            bad_cases = [
                (["decimate", box, out, "notanumber"], "non-numeric tris"),
                (["decimate-repaired", box, out, "100", "--holes", "bogus"], "non-numeric --holes"),
                (["decimate-repaired", box, out, "100", "--holes=-0.5"], "negative --holes"),
                (["decimate-repaired", box, out, "100", "--holes=nan"], "NaN --holes"),
                (["bogus-mode", os.path.join(tmp, "nonexistent.obj"), out, "100"], "unknown mode"),
                (["decimate", box, out, "100", "unexpected-extra"], "extra positional arg"),
                (["quadriflow", box, out, "100", "--repair"], "--repair on quadriflow"),
                (["decimate", box, out, "100", "--holes", "2.0"], "--holes without --repair"),
            ]
            for argv, label in bad_cases:
                r = run_cut(bl, argv)
                combined = r.stdout + "\n" + r.stderr
                self.assertNotEqual(r.returncode, 0, f"{label} must fail:\n{combined}")
                self.assertIn("cut:", combined, f"{label} must emit clean cut: error:\n{combined}")
                self.assertNotIn("Traceback", combined, f"{label} leaked traceback:\n{combined}")
                self.assertNotIn("ValueError", combined, f"{label} leaked ValueError:\n{combined}")
                self.assertFalse(os.path.exists(out), f"{label} must not leave output file behind")

    def test_cut_degenerate_and_corrupt_inputs_fail_cleanly(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as tmp:
            corrupt_glb = os.path.join(tmp, "corrupt.glb")
            with open(corrupt_glb, "wb") as f:
                f.write(b"NOT_A_GLB_FILE")

            empty_glb = os.path.join(tmp, "empty.glb")
            with open(empty_glb, "wb") as f:
                f.write(make_empty_glb_bytes())

            bad_ext = os.path.join(tmp, "mesh.txt")
            with open(bad_ext, "w", encoding="utf-8") as f:
                f.write("not a mesh\n")

            zero_obj = os.path.join(tmp, "zero.obj")
            with open(zero_obj, "w", encoding="utf-8") as f:
                f.write("v 0 0 0\nv 0 0 0\nv 0 0 0\nf 1 2 3\n")

            col_obj = os.path.join(tmp, "collinear.obj")
            with open(col_obj, "w", encoding="utf-8") as f:
                f.write("v 0 0 0\nv 1 0 0\nv 2 0 0\nf 1 2 3\n")

            nan_obj = os.path.join(tmp, "nan.obj")
            with open(nan_obj, "w", encoding="utf-8") as f:
                f.write("v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")

            nan_glb = os.path.join(tmp, "nan.glb")
            with open(nan_glb, "wb") as f:
                f.write(make_nan_glb_bytes())

            cases = [
                ("decimate-repaired", corrupt_glb),
                ("decimate-repaired", empty_glb),
                ("decimate-repaired", bad_ext),
                ("decimate", zero_obj),
                ("decimate-repaired", zero_obj),
                ("decimate", col_obj),
                ("decimate-repaired", col_obj),
                ("decimate", nan_obj),
                ("decimate-repaired", nan_obj),
                ("decimate-repaired", nan_glb),
            ]
            for idx, (mode, src) in enumerate(cases):
                out = os.path.join(tmp, f"out_{idx}.obj")
                r = run_cut(bl, [mode, src, out, "100"])
                combined = r.stdout + "\n" + r.stderr
                self.assertNotEqual(r.returncode, 0, f"{mode} on {os.path.basename(src)} must fail:\n{combined}")
                self.assertIn("cut:", combined, f"{mode} on {os.path.basename(src)} must emit cut: error:\n{combined}")
                self.assertNotIn("Traceback", combined, f"{mode} on {os.path.basename(src)} leaked traceback:\n{combined}")
                self.assertFalse(os.path.exists(out), f"{mode} on {os.path.basename(src)} must not write corrupt OBJ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
