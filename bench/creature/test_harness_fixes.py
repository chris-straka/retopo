#!/usr/bin/env python3
"""RED -> GREEN regression tests for the four creature bench harness fixes (a)-(d).

Run standalone:
    python3 bench/creature/test_harness_fixes.py
    REQUIRE_BLENDER=1 python3 bench/creature/test_harness_fixes.py
"""
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

import blind  # noqa: E402
import posed_image_metrics as pim  # noqa: E402


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


def _write_box_obj(path):
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
        for fc in faces:
            f.write("f " + " ".join(str(i) for i in fc) + "\n")


GENERIC_FIXTURE_SCRIPT = r"""
import math, os, sys
import bmesh, bpy
from mathutils import Vector

out_dir = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out_dir, exist_ok=True)


def add_ellipsoid(bm, center, radii, segs=20, rings=10):
    ret = bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=1.0)
    for v in ret["verts"]:
        v.co = Vector((
            v.co.x * radii[0] + center[0],
            v.co.y * radii[1] + center[1],
            v.co.z * radii[2] + center[2],
        ))


def add_limb(bm, p0, p1, r0, r1, segs=12, rings=8):
    v0, v1 = Vector(p0), Vector(p1)
    axis = v1 - v0
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


def skin_and_export(bm, arm_specs, dense_obj_path, glb_path):
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new("creature")
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new("creature", mesh)
    bpy.context.scene.collection.objects.link(ob)

    if dense_obj_path:
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.wm.obj_export(
            filepath=dense_obj_path,
            export_selected_objects=True,
            export_uv=False,
            export_normals=False,
            export_materials=False,
        )

    arm_data = bpy.data.armatures.new("arm")
    arm = bpy.data.objects.new("arm", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones
    created = {}
    for bname, head, tail, parent in arm_specs:
        b = eb.new(bname)
        b.head = Vector(head)
        b.tail = Vector(tail)
        if parent:
            b.parent = created[parent]
        created[bname] = b
    bpy.ops.object.mode_set(mode="OBJECT")

    ob.parent = arm
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    vgs = {bname: ob.vertex_groups.new(name=bname) for bname, _, _, _ in arm_specs}
    for v in ob.data.vertices:
        p = v.co
        dists = []
        for bname, head, tail, _ in arm_specs:
            h, t = Vector(head), Vector(tail)
            ab = t - h
            ab2 = max(ab.length_squared, 1e-8)
            u = max(0.0, min(1.0, (p - h).dot(ab) / ab2))
            closest = h + ab * u
            dists.append(((p - closest).length, bname))
        dists.sort()
        if len(dists) == 1:
            vgs[dists[0][1]].add([v.index], 1.0, "REPLACE")
        else:
            w0 = 1.0 / (dists[0][0] + 0.04) ** 2
            w1 = 1.0 / (dists[1][0] + 0.04) ** 2
            s = w0 + w1
            vgs[dists[0][1]].add([v.index], w0 / s, "REPLACE")
            vgs[dists[1][1]].add([v.index], w1 / s, "REPLACE")

    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.export_scene.gltf(
        filepath=glb_path,
        use_selection=True,
        export_format="GLB",
        export_materials="NONE",
        export_animations=False,
        export_skins=True,
    )


# 1) Generic-name multi-spine quadruped: Bone..Bone.012
bpy.ops.wm.read_factory_settings(use_empty=True)
bm = bmesh.new()
add_ellipsoid(bm, (0, 0.0, 0.88), (0.34, 0.58, 0.28), segs=24, rings=12)
add_limb(bm, (0, -0.42, 0.92), (0, -0.72, 1.28), 0.14, 0.11, segs=12, rings=6)
add_ellipsoid(bm, (0, -0.78, 1.32), (0.17, 0.21, 0.16), segs=16, rings=10)
add_limb(bm, (0, 0.42, 0.86), (0, 0.92, 1.02), 0.10, 0.03, segs=10, rings=6)
for sx in (-1, 1):
    for sy in (-0.35, 0.35):
        hip = (sx * 0.26, sy, 0.82)
        knee = (sx * 0.26, sy - 0.03, 0.42)
        foot = (sx * 0.26, sy, 0.02)
        add_limb(bm, hip, knee, 0.11, 0.08, segs=10, rings=6)
        add_limb(bm, knee, foot, 0.08, 0.055, segs=10, rings=6)

quad_bones = [
    ("Bone",     (0.0,  0.35, 0.85), (0.0,  0.05, 0.86), None),
    ("Bone.001", (0.0,  0.05, 0.86), (0.0, -0.15, 0.88), "Bone"),
    ("Bone.002", (0.0, -0.15, 0.88), (0.0, -0.38, 0.92), "Bone.001"),
    ("Bone.003", (-0.26, -0.35, 0.82), (-0.26, -0.38, 0.42), "Bone.002"),
    ("Bone.004", (-0.26, -0.38, 0.42), (-0.26, -0.35, 0.02), "Bone.003"),
    ("Bone.005", ( 0.26, -0.35, 0.82), ( 0.26, -0.38, 0.42), "Bone.002"),
    ("Bone.006", ( 0.26, -0.38, 0.42), ( 0.26, -0.35, 0.02), "Bone.005"),
    ("Bone.007", (-0.26,  0.35, 0.82), (-0.26,  0.32, 0.42), "Bone"),
    ("Bone.008", (-0.26,  0.32, 0.42), (-0.26,  0.35, 0.02), "Bone.007"),
    ("Bone.009", ( 0.26,  0.35, 0.82), ( 0.26,  0.32, 0.42), "Bone"),
    ("Bone.010", ( 0.26,  0.32, 0.42), ( 0.26,  0.35, 0.02), "Bone.009"),
    ("Bone.011", (0.0, -0.42, 0.94), (0.0, -0.72, 1.28), "Bone.002"),
    ("Bone.012", (0.0,  0.42, 0.86), (0.0,  0.92, 1.02), "Bone"),
]
skin_and_export(bm, quad_bones, os.path.join(out_dir, "quad.obj"), os.path.join(out_dir, "quad_generic.glb"))

# 2) Single-bone rigged mesh (0 limb bones can be posed)
bpy.ops.wm.read_factory_settings(use_empty=True)
bm1 = bmesh.new()
add_ellipsoid(bm1, (0, 0, 0.5), (0.3, 0.3, 0.4), segs=16, rings=8)
skin_and_export(
    bm1,
    [("Bone", (0.0, 0.0, 0.2), (0.0, 0.0, 0.8), None)],
    os.path.join(out_dir, "onebone.obj"),
    os.path.join(out_dir, "onebone.glb"),
)

# 3) Asymmetric limbed rig (< 2 mirrored limb pairs within 2% of span)
bpy.ops.wm.read_factory_settings(use_empty=True)
bm2 = bmesh.new()
add_ellipsoid(bm2, (0, 0, 0.85), (0.35, 0.45, 0.25), segs=18, rings=10)
asym_limbs = [
    ("Bone.001", (-0.28,  0.00, 0.82), (-0.28,  0.00, 0.02)),
    ("Bone.002", ( 0.42, -0.35, 0.82), ( 0.42, -0.35, 0.02)),
    ("Bone.003", ( 0.24,  0.40, 0.82), ( 0.24,  0.40, 0.02)),
]
for _, p0, p1 in asym_limbs:
    add_limb(bm2, p0, p1, 0.09, 0.06, segs=10, rings=6)
asym_bones = [("Bone", (0.0, 0.2, 0.85), (0.0, -0.2, 0.88), None)] + [
    (bn, p0, p1, "Bone") for bn, p0, p1 in asym_limbs
]
skin_and_export(bm2, asym_bones, None, os.path.join(out_dir, "asym_limbed.glb"))
print("GENERIC_FIXTURES_OK")
"""


VERIFY_SWAP_SCRIPT = r"""
import json, sys
import bpy
import numpy as np
from mathutils.kdtree import KDTree

in_glb, out_glb, report_json = sys.argv[sys.argv.index("--") + 1:]


def read_dom(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    objs = [o for o in bpy.data.objects if o not in before]
    ob = max((o for o in objs if o.type == "MESH"), key=lambda o: len(o.data.vertices))
    m = np.array(ob.matrix_world)
    pts = []
    dom = []
    for v in ob.data.vertices:
        p = m[:3, :3] @ np.array(v.co) + m[:3, 3]
        pts.append((float(p[0]), float(p[1]), float(p[2])))
        if v.groups:
            best = max(v.groups, key=lambda g: g.weight)
            dom.append(ob.vertex_groups[best.group].name)
        else:
            dom.append(None)
    return pts, dom


pts_in, dom_in = read_dom(in_glb)
pts_out, dom_out = read_dom(out_glb)

kd = KDTree(len(pts_out))
for i, p in enumerate(pts_out):
    kd.insert(p, i)
kd.balance()

by_orig = {}
for p, orig_name in zip(pts_in, dom_in):
    _, idx, _ = kd.find(p)
    out_name = dom_out[idx]
    by_orig.setdefault(orig_name, {})[out_name] = by_orig.get(orig_name, {}).get(out_name, 0) + 1

with open(report_json, "w") as f:
    json.dump(by_orig, f, indent=1, sort_keys=True)
"""


def _build_generic_fixtures(blender, tmp):
    gen_py = os.path.join(tmp, "make_generic.py")
    with open(gen_py, "w") as f:
        f.write(GENERIC_FIXTURE_SCRIPT)
    r = subprocess.run(
        [blender, "-b", "--factory-startup", "--python-exit-code", "1", "-P", gen_py, "--", tmp],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if r.returncode != 0:
        raise AssertionError(f"generic fixture gen failed:\n{r.stdout}\n{r.stderr}")


class HarnessFixesTest(unittest.TestCase):
    # (a) cut.py clean-copy uses meshkit defaults (no --voxel, no --directions < 150)
    def test_clean_copy_uses_meshkit_defaults(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as td:
            fake_mk = os.path.join(td, "fake_meshkit")
            os.makedirs(os.path.join(fake_mk, "tools"), exist_ok=True)
            argv_file = os.path.join(td, "captured_argv.json")
            with open(os.path.join(fake_mk, "tools", "clean_copy.py"), "w") as f:
                f.write("""
import json, os, sys
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
with open(os.environ["CAPTURE_ARGV_FILE"], "w") as fp:
    json.dump(argv, fp)

out_dir = argv[argv.index("--out") + 1]
tris = int(argv[argv.index("--tris") + 1].split(",")[0])
os.makedirs(out_dir, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1.0)
bpy.ops.wm.ply_export(
    filepath=os.path.join(out_dir, f"cut_{tris}.ply"),
    export_selected_objects=True,
    forward_axis="Y",
    up_axis="Z",
)
""")
            src = os.path.join(td, "box.obj")
            out = os.path.join(td, "out.obj")
            _write_box_obj(src)
            env = dict(os.environ, MESHKIT_DIR=fake_mk, CAPTURE_ARGV_FILE=argv_file)
            r = subprocess.run(
                [
                    bl, "-b", "--factory-startup", "--python-exit-code", "1",
                    "-P", os.path.join(HERE, "cut.py"),
                    "--", "clean-copy", src, out, "400",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
            self.assertEqual(r.returncode, 0, f"cut.py failed:\n{r.stdout}\n{r.stderr}")
            self.assertTrue(os.path.isfile(out))
            with open(argv_file) as f:
                captured = json.load(f)

            self.assertFalse(
                any(tok == "--voxel" or tok.startswith("--voxel=") for tok in captured),
                f"cut.py must not override --voxel (meshkit default height/700 applies), got: {captured}",
            )
            for idx, tok in enumerate(captured):
                if tok == "--directions" and idx + 1 < len(captured):
                    self.assertGreaterEqual(
                        int(captured[idx + 1]),
                        150,
                        f"cut.py must not pass --directions < 150, got: {captured}",
                    )
                elif tok.startswith("--directions="):
                    self.assertGreaterEqual(
                        int(tok.split("=", 1)[1]),
                        150,
                        f"cut.py must not pass --directions < 150, got: {captured}",
                    )

    # (b) generic Bone/Bone.001... rigs pose geometrically in game_frame.py & posed_image_check.py
    def test_generic_bone_names_pose_and_frames_differ(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as td:
            _build_generic_fixtures(bl, td)
            quad_obj = os.path.join(td, "quad.obj")
            quad_glb = os.path.join(td, "quad_generic.glb")
            f0 = os.path.join(td, "frame_00.png")
            f9 = os.path.join(td, "frame_09.png")

            for frame, out_png in ((0, f0), (9, f9)):
                r = subprocess.run(
                    [
                        bl, "-b", "--factory-startup", "--python-exit-code", "1",
                        "-P", os.path.join(HERE, "game_frame.py"),
                        "--", quad_glb, quad_obj, out_png, "--frame", str(frame),
                        "--width", "256", "--height", "288",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                self.assertEqual(r.returncode, 0, f"game_frame frame {frame} failed:\n{r.stdout}\n{r.stderr}")

            diff = pim.compare_frame_pair(f0, f9)
            self.assertGreater(
                diff["sil_diff_pct"],
                0.5,
                f"generic-bone fixture frames 0 and 9 must visibly differ when posed, got {diff}",
            )
            self.assertGreater(diff["shade_rmse"], 1.0, f"expected shading shift between poses, got {diff}")

            # Also verify posed_image_check.py renders distinct stride poses into cache for generic bones
            cache = os.path.join(td, "img_cache")
            out_json = os.path.join(td, "self_check.json")
            rp = subprocess.run(
                [
                    bl, "-b", "--factory-startup", "--python-exit-code", "1",
                    "-P", os.path.join(HERE, "posed_image_check.py"),
                    "--", quad_glb, quad_glb, quad_obj, out_json,
                    "--frames", "8", "--width", "256", "--height", "288", "--cache", cache,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertEqual(rp.returncode, 0, f"posed_image_check failed:\n{rp.stdout}\n{rp.stderr}")
            clip_diff = pim.compare_frame_pair(
                os.path.join(cache, "ref_00.png"),
                os.path.join(cache, "ref_02.png"),
            )
            self.assertGreater(
                clip_diff["sil_diff_pct"],
                0.5,
                f"posed_image_check ref_00 vs ref_02 must pose generic bones, got {clip_diff}",
            )

    def test_zero_posed_bones_fails_loudly(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as td:
            _build_generic_fixtures(bl, td)
            one_obj = os.path.join(td, "onebone.obj")
            one_glb = os.path.join(td, "onebone.glb")

            r_gf = subprocess.run(
                [
                    bl, "-b", "--factory-startup",
                    "-P", os.path.join(HERE, "game_frame.py"),
                    "--", one_glb, one_obj, os.path.join(td, "one.png"),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertNotEqual(r_gf.returncode, 0, "game_frame.py must fail loudly when 0 bones get posed")

            r_pic = subprocess.run(
                [
                    bl, "-b", "--factory-startup",
                    "-P", os.path.join(HERE, "posed_image_check.py"),
                    "--", one_glb, one_glb, one_obj, os.path.join(td, "one.json"),
                    "--frames", "8", "--width", "128", "--height", "144",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertNotEqual(r_pic.returncode, 0, "posed_image_check.py must fail loudly when 0 bones get posed")

    # (c) --plant-swap pairs all mirrored limb groups by bone heads, and fails below 2 pairs on a limbed rig
    def test_plant_swap_pairs_all_mirrored_generic_limbs(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as td:
            _build_generic_fixtures(bl, td)
            in_glb = os.path.join(td, "quad_generic.glb")
            swapped_glb = os.path.join(td, "quad_swapped.glb")

            r = subprocess.run(
                [
                    bl, "-b", "--factory-startup",
                    "-P", os.path.join(HERE, "game_frame.py"),
                    "--", "--plant-swap", in_glb, swapped_glb,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(r.returncode, 0, f"--plant-swap failed:\n{r.stdout}\n{r.stderr}")
            self.assertTrue(os.path.isfile(swapped_glb))

            verify_py = os.path.join(td, "verify_swap.py")
            with open(verify_py, "w") as f:
                f.write(VERIFY_SWAP_SCRIPT)
            report_json = os.path.join(td, "swap_report.json")
            rv = subprocess.run(
                [
                    bl, "-b", "--factory-startup", "--python-exit-code", "1",
                    "-P", verify_py, "--", in_glb, swapped_glb, report_json,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(rv.returncode, 0, f"verify_swap failed:\n{rv.stdout}\n{rv.stderr}")
            with open(report_json) as f:
                by_orig = json.load(f)

            expected_swaps = {
                "Bone.003": "Bone.005",
                "Bone.005": "Bone.003",
                "Bone.004": "Bone.006",
                "Bone.006": "Bone.004",
                "Bone.007": "Bone.009",
                "Bone.009": "Bone.007",
                "Bone.008": "Bone.010",
                "Bone.010": "Bone.008",
            }
            for src_b, dst_b in expected_swaps.items():
                counts = by_orig.get(src_b, {})
                total = sum(counts.values())
                self.assertGreater(total, 0, f"fixture vertex group {src_b} has no dominant vertices: {by_orig}")
                swapped_frac = counts.get(dst_b, 0) / float(total)
                self.assertGreater(
                    swapped_frac,
                    0.9,
                    f"expected {src_b} -> {dst_b} swap (>90%), got {counts}",
                )

            for trunk_b in ("Bone", "Bone.001", "Bone.002", "Bone.011", "Bone.012"):
                counts = by_orig.get(trunk_b, {})
                if counts:
                    total = sum(counts.values())
                    self.assertGreater(
                        counts.get(trunk_b, 0) / float(total),
                        0.9,
                        f"non-limb bone {trunk_b} must stay unswapped, got {counts}",
                    )

    def test_plant_swap_fails_below_two_pairs_on_limbed_rig(self):
        bl = require_blender_or_skip(self)
        with tempfile.TemporaryDirectory() as td:
            _build_generic_fixtures(bl, td)
            asym_glb = os.path.join(td, "asym_limbed.glb")
            out_glb = os.path.join(td, "asym_swapped.glb")

            r = subprocess.run(
                [
                    bl, "-b", "--factory-startup",
                    "-P", os.path.join(HERE, "game_frame.py"),
                    "--", "--plant-swap", asym_glb, out_glb,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertNotEqual(
                r.returncode,
                0,
                f"--plant-swap must fail loudly when < 2 mirrored pairs exist on a limbed rig:\n{r.stdout}\n{r.stderr}",
            )

    # (d) gamebench.sh runs blind.py + exports FLEET_ARTIFACTS even when gamebench.py exits 1
    def test_gamebench_sh_runs_blind_and_artifacts_when_gamebench_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            bin_dir = os.path.join(td, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            work = os.path.join(td, "work")
            out = os.path.join(td, "out")
            artifacts = os.path.join(td, "artifacts")
            os.makedirs(work, exist_ok=True)

            true_exec = os.path.join(bin_dir, "true_exec")
            with open(true_exec, "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(true_exec, 0o755)

            stub_gb = os.path.join(td, "stub_gamebench.py")
            with open(stub_gb, "w") as f:
                f.write(f"""
import json, os, sys
sys.path.insert(0, {HERE!r})
import blind

args = sys.argv[1:]
out_dir = args[args.index("--out") + 1]
os.makedirs(out_dir, exist_ok=True)
sd = os.path.join(out_dir, "creature_a")
os.makedirs(sd, exist_ok=True)
rgb_a = bytes((120, 140, 160)) * (32 * 36)
rgb_b = bytes((100, 160, 120)) * (32 * 36)
rgb_p = bytes((210, 50, 50)) * (32 * 36)
blind.write_png(os.path.join(sd, "frame_decimate#a.png"), 32, 36, rgb_a)
blind.write_png(os.path.join(sd, "frame_decimate#b.png"), 32, 36, rgb_b)
blind.write_png(os.path.join(sd, "frame_planted.png"), 32, 36, rgb_p)

row = {{
    "subject": "creature_a",
    "ok": True,
    "cuts": {{
        "decimate#a": {{"bend": {{"tears": 4.0, "score": 80.0}}, "dist_p95": 0.2}},
        "decimate#b": {{"bend": {{"tears": 4.0, "score": 80.0}}, "dist_p95": 0.2}},
        "planted": {{"bend": {{"tears": 30.0, "score": 20.0}}, "dist_p95": 1.2}},
    }},
}}
verdict = {{"controls": {{"valid": True}}, "challenger_beats_baseline": False, "winner": None}}
with open(os.path.join(out_dir, "results.json"), "w") as fp:
    json.dump({{"tools": ["decimate", "decimate"], "rows": [row], "verdict": verdict}}, fp)
with open(os.path.join(out_dir, "verdict.json"), "w") as fp:
    json.dump(verdict, fp)
with open(os.path.join(out_dir, "report.md"), "w") as fp:
    fp.write("# stub report\\n")
sys.exit(1)
""")

            py_shim = os.path.join(bin_dir, "python3")
            with open(py_shim, "w") as f:
                f.write(
                    "#!/usr/bin/env bash\n"
                    f"if [ \"${{1:-}}\" = \"bench/creature/gamebench.py\" ]; then\n"
                    f"  exec {sys.executable!r} {stub_gb!r} \"${{@:2}}\"\n"
                    "fi\n"
                    f"exec {sys.executable!r} \"$@\"\n"
                )
            os.chmod(py_shim, 0o755)

            env = dict(
                os.environ,
                PATH=bin_dir + os.pathsep + os.environ.get("PATH", ""),
                GAMEBENCH_WORK=work,
                GAMEBENCH_OUT=out,
                GAMEBENCH_TOOLS="decimate,decimate",
                FLEET_ARTIFACTS=artifacts,
                BLENDER=true_exec,
                WEIGHTS=true_exec,
            )
            r = subprocess.run(
                ["bash", os.path.join(HERE, "gamebench.sh"), "--gate"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(r.returncode, 1, f"expected gamebench.sh to preserve exit 1, got {r.returncode}:\n{r.stdout}\n{r.stderr}")
            self.assertTrue(
                os.path.isfile(os.path.join(out, "blind", "manifest.json")),
                f"expected blind/manifest.json even when gamebench.py exited 1:\n{r.stdout}\n{r.stderr}",
            )
            self.assertTrue(
                os.path.isfile(os.path.join(artifacts, "verdict.json")),
                f"expected $FLEET_ARTIFACTS/verdict.json even when gamebench.py exited 1:\n{r.stdout}\n{r.stderr}",
            )
            self.assertTrue(
                os.path.isfile(os.path.join(artifacts, "blind_manifest.json")),
                f"expected $FLEET_ARTIFACTS/blind_manifest.json even when gamebench.py exited 1:\n{r.stdout}\n{r.stderr}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
