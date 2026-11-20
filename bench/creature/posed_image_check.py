"""Blender: 8-frame posed game-camera silhouette and shading check (`retopo-3`).

    blender -b --factory-startup -P bench/creature/posed_image_check.py -- \
        REF_RIGGED.glb CAND_RIGGED.glb FRAME_MESH.obj OUT.json \
        [--frames 8] [--width 320] [--height 360] [--cache DIR]

Both REF_RIGGED.glb (dense source rigged with reference weights) and
CAND_RIGGED.glb (cut rigged with the same weights) are rendered across
`--frames` (default 8) evenly-spaced poses at the shared HLH follow camera
framed strictly from FRAME_MESH.obj, with transparent background RGBA PNGs.
Writes silhouette and shading differences to OUT.json.
"""
import json
import math
import os
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_frame as gf  # noqa: E402
import metrics_lib as ml  # noqa: E402
import posed_image_metrics as pim  # noqa: E402


def parse_args():
    a = sys.argv[sys.argv.index("--") + 1:]
    o = {"frames": str(pim.DEFAULT_FRAME_COUNT), "width": "320", "height": "360", "cache": None}
    pos = []
    i = 0
    while i < len(a):
        if a[i].startswith("--"):
            o[a[i][2:]] = a[i + 1]
            i += 2
        else:
            pos.append(a[i])
            i += 1
    if len(pos) != 4:
        raise SystemExit(__doc__)
    return pos[0], pos[1], pos[2], pos[3], o


def compute_follow_camera(frame_mesh, width, height_px):
    """Exact HLH follow camera framing from FRAME_MESH.obj (matches game_frame.py)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    f_objs = ml.imported(lambda: bpy.ops.wm.obj_import(filepath=frame_mesh))
    fm = next(x for x in f_objs if x.type == "MESH")
    fv = ml.coords(fm)
    lo, hi = np.percentile(fv, 1, axis=0), np.percentile(fv, 99, axis=0)
    body_h = max(float(hi[2] - lo[2]), 1e-4)
    body_w = float(max(hi[0] - lo[0], hi[1] - lo[1]))
    aspect = width / float(height_px)
    fov_y = math.radians(45.0)
    fov_x = 2.0 * math.atan(math.tan(fov_y * 0.5) * aspect)
    dist_h = 3.0 * (body_h / 1.75)
    dist_w = (body_w / 0.72) / (2.0 * math.tan(fov_x * 0.5))
    arm_len = max(dist_h, dist_w)
    focus = Vector(((lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5, lo[2] + 0.52 * body_h))
    pitch = math.radians(18.3)
    azimuth = Vector((-0.65, -1.0, 0.0))
    azimuth.normalize()
    cam_pos = focus + Vector((
        azimuth.x * math.cos(pitch),
        azimuth.y * math.cos(pitch),
        math.sin(pitch),
    )) * arm_len
    return {
        "focus": [float(focus.x), float(focus.y), float(focus.z)],
        "cam_pos": [float(cam_pos.x), float(cam_pos.y), float(cam_pos.z)],
        "arm_len": float(arm_len),
        "fov_y": float(fov_y),
    }


def render_clip_frames(glb_path, cam_spec, out_dir, prefix, n_frames, width, height_px):
    """Render `n_frames` posed transparent RGBA PNGs for one rigged GLB."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = ml.imported(lambda: bpy.ops.import_scene.gltf(filepath=glb_path, merge_vertices=True))
    arm = next(x for x in objs if x.type == "ARMATURE")
    c = max((x for x in objs if x.type == "MESH"), key=lambda x: len(x.data.vertices))
    for x in list(bpy.data.objects):
        if x.type == "MESH" and x is not c:
            bpy.data.objects.remove(x, do_unlink=True)

    # Use embedded animation clip if present with >= 2 frames; otherwise synthetic 36-frame stride.
    use_action = False
    action = arm.animation_data.action if (arm.animation_data and arm.animation_data.action) else None
    if action is not None:
        f0, f1 = action.frame_range
        if f1 - f0 >= 1.5:
            use_action = True
            frame_ids = pim.sample_frame_indices(f0, f1, n_frames)
    if not use_action:
        for x in bpy.data.objects:
            x.animation_data_clear()
        frame_ids = pim.sample_frame_indices(0, 31, n_frames)

    own_paint = gf.has_own_paint(c)
    if not own_paint:
        c.data.materials.clear()
        mat = bpy.data.materials.new("clay")
        mat.diffuse_color = (0.74, 0.72, 0.68, 1.0)
        c.data.materials.append(mat)
    c.data.shade_smooth()
    if not any(m.type == "ARMATURE" for m in c.modifiers):
        am = c.modifiers.new("Armature", "ARMATURE")
        am.object = arm

    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "TEXTURE" if own_paint else "MATERIAL"
    sc.display.shading.show_cavity = False
    sc.display.shading.show_specular_highlight = True
    sc.display.render_aa = "OFF"
    sc.render.resolution_x = width
    sc.render.resolution_y = height_px
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "Standard"

    cam_data = bpy.data.cameras.new("gamecam")
    cam_data.sensor_fit = "VERTICAL"
    try:
        cam_data.angle_y = cam_spec["fov_y"]
    except (AttributeError, TypeError):
        cam_data.angle = cam_spec["fov_y"]
    cam_data.clip_start = cam_spec["arm_len"] * 0.01
    cam_data.clip_end = cam_spec["arm_len"] * 20.0
    cam = bpy.data.objects.new("gamecam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam_pos = Vector(cam_spec["cam_pos"])
    focus = Vector(cam_spec["focus"])
    cam.location = cam_pos
    cam.rotation_euler = (focus - cam_pos).to_track_quat("-Z", "Y").to_euler()

    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for idx, fnum in enumerate(frame_ids):
        if use_action:
            sc.frame_set(fnum)
        else:
            gf.apply_stride_pose(arm, fnum)
        bpy.context.view_layer.update()
        out_png = os.path.join(out_dir, f"{prefix}_{idx:02d}.png")
        sc.render.filepath = out_png
        bpy.ops.render.render(write_still=True)
        paths.append((fnum, out_png))
    return paths


def main():
    ref_glb, cand_glb, frame_mesh, out_json, o = parse_args()
    n_frames = int(o["frames"])
    width = int(o["width"])
    height_px = int(o["height"])
    cache = o["cache"]

    with tempfile.TemporaryDirectory() as tmp:
        ref_dir = cache if cache else os.path.join(tmp, "ref")
        cam_file = os.path.join(ref_dir, f"cam_{width}x{height_px}.json")
        if cache and os.path.isfile(cam_file):
            with open(cam_file) as f:
                cam_spec = json.load(f)
        else:
            cam_spec = compute_follow_camera(frame_mesh, width, height_px)
            os.makedirs(ref_dir, exist_ok=True)
            with open(cam_file, "w") as f:
                json.dump(cam_spec, f)

        expected_ref = [
            os.path.join(ref_dir, f"ref_{i:02d}.png") for i in range(n_frames)
        ]
        if cache and all(os.path.isfile(p) for p in expected_ref):
            ref_frames = [(i, p) for i, p in enumerate(expected_ref)]
        else:
            ref_frames = render_clip_frames(
                ref_glb, cam_spec, ref_dir, "ref", n_frames, width, height_px
            )

        cand_dir = os.path.join(tmp, "cand")
        cand_frames = render_clip_frames(
            cand_glb, cam_spec, cand_dir, "cand", n_frames, width, height_px
        )

        per_frame = []
        for idx, ((_, rp), (fnum, cp)) in enumerate(zip(ref_frames, cand_frames)):
            m = pim.compare_frame_pair(rp, cp)
            m["index"] = idx
            m["frame"] = fnum
            per_frame.append(m)

        res = pim.aggregate_frames(per_frame)
        os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
        with open(out_json, "w") as f:
            json.dump(res, f, indent=1, sort_keys=True)
        print(
            f"POSED_IMAGE_CHECK frames={res['frames']} "
            f"sil_mean={res['sil_diff_mean_pct']:.3f}% "
            f"shade_rmse={res['shade_rmse_mean']:.3f}"
        )


main()
