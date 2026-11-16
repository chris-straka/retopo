"""Blender: render one rigged candidate at one stride frame from the HLH game camera,
or build the planted bad-rig copy (swapped L/R limb weights).

Render mode:
    blender -b --factory-startup -P bench/creature/game_frame.py -- \
        CANDIDATE_RIGGED.glb FRAME_MESH.obj OUT.png [--frame 9] [--width 640] [--height 720]

Plant-swap mode (for the benchmark's planted bad case):
    blender -b --factory-startup -P bench/creature/game_frame.py -- \
        --plant-swap IN_RIGGED.glb OUT_RIGGED.glb

Blind game-camera rules (blind-gamecam-bench):
1. HLH follow camera (~3.0 m behind a 1.75 m figure, 45 deg vertical FOV so the
   body fills ~70% of the frame height, pitched down 18.3 deg at a three-quarter
   orbit heading so limbs, trunk, and silhouette read together).
2. Camera focus and distance come strictly from FRAME_MESH.obj (the shared dense
   source), never from the candidate, so both sides of a pair share the exact
   same camera matrix and clip planes even if a candidate drops horns or spikes.
3. Same animation tick/pose on the shared skeleton, smooth-shaded in the model's
   own paint (neutral studio clay when unpainted), with NO wireframes, flat
   facet lines, or diagnostic overlays that would leak quads vs triangles.
"""
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics_lib as ml  # noqa: E402


def parse_args():
    a = sys.argv[sys.argv.index("--") + 1:]
    if a and a[0] == "--plant-swap":
        if len(a) != 3:
            raise SystemExit("usage: --plant-swap IN_RIGGED.glb OUT_RIGGED.glb")
        return "plant_swap", a[1:], {}
    o = {"frame": "9", "width": "640", "height": "720"}
    pos = []
    i = 0
    while i < len(a):
        if a[i].startswith("--"):
            o[a[i][2:]] = a[i + 1]
            i += 2
        else:
            pos.append(a[i])
            i += 1
    if len(pos) != 3:
        raise SystemExit(__doc__)
    return "render", pos, o


def swap_limb_groups(mesh_ob):
    """Swap left/right limb vertex groups so opposite limbs pull each other's skin.
    Falls back to swapping the two largest non-trunk groups when a rig is asymmetric."""
    vgs = mesh_ob.vertex_groups
    by_name = {g.name: g for g in vgs}
    pairs = []
    seen = set()
    suffixes = ((".L", ".R"), ("_L", "_R"), (".l", ".r"), ("_l", "_r"), ("Left", "Right"))
    for name in sorted(by_name):
        if name in seen:
            continue
        low = name.lower()
        if any(k in low for k in ("spine", "hips", "pelvis", "root", "torso", "head", "neck", "tail")):
            continue
        for lsuf, rsuf in suffixes:
            if name.endswith(lsuf):
                partner = name[:-len(lsuf)] + rsuf
                if partner in by_name and partner not in seen:
                    pairs.append((by_name[name], by_name[partner]))
                    seen.update((name, partner))
                    break
    if not pairs:
        counts = {g.index: 0 for g in vgs}
        for v in mesh_ob.data.vertices:
            for g in v.groups:
                if g.weight > 0.05:
                    counts[g.group] = counts.get(g.group, 0) + 1
        non_trunk = [
            g for g in vgs
            if not any(k in g.name.lower() for k in ("spine", "hips", "pelvis", "root", "torso"))
        ]
        ranked = sorted(non_trunk if len(non_trunk) >= 2 else list(vgs),
                        key=lambda g: counts.get(g.index, 0), reverse=True)
        if len(ranked) >= 2:
            pairs.append((ranked[0], ranked[1]))
    for idx, (ga, gb) in enumerate(pairs):
        na, nb = ga.name, gb.name
        ga.name = f"__swap_tmp_{idx}__"
        gb.name = na
        ga.name = nb
    return len(pairs)


def run_plant_swap(in_glb, out_glb):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = ml.imported(lambda: bpy.ops.import_scene.gltf(filepath=in_glb))
    arm = next(x for x in objs if x.type == "ARMATURE")
    meshes = [x for x in objs if x.type == "MESH" and x.vertex_groups]
    if not meshes:
        raise SystemExit("plant_swap: no skinned mesh")
    swapped = sum(swap_limb_groups(m) for m in meshes)
    bpy.ops.object.select_all(action="DESELECT")
    for x in objs:
        if x is arm or x in meshes:
            x.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.export_scene.gltf(
        filepath=out_glb,
        use_selection=True,
        export_format="GLB",
        export_materials="NONE",
        export_animations=False,
        export_skins=True,
        export_all_influences=False,
    )
    print(f"PLANT_SWAP swapped_pairs={swapped} -> {out_glb}")


def has_own_paint(mesh_ob):
    if mesh_ob.data.color_attributes:
        return True
    for mat in mesh_ob.data.materials:
        if mat and mat.use_nodes and mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and getattr(node, "image", None) is not None:
                    return True
    return False


def apply_stride_pose(arm, frame, cycle=36):
    words = ("leg", "arm", "tail", "neck", "wing", "extra")

    def kind(name):
        n = name.lower()
        return next((w for w in words if w in n), None)

    t = 2.0 * math.pi * frame / float(cycle)
    root_swing = math.radians(35.0)
    child_swing = math.radians(40.0)
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    for pb in arm.pose.bones:
        k = kind(pb.name)
        if not k:
            continue
        is_root = pb.parent is None or kind(pb.parent.name) != k
        side = 0.5 if pb.name.endswith((".R", "_R", "Right", ".r", "_r")) else 0.0
        head = arm.matrix_world @ pb.bone.head_local
        front = 0.20 if head.y < 0 else 0.0
        amp = root_swing if is_root else child_swing
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion(
            (1.0, 0.0, 0.0), amp * math.sin(t + 2.0 * math.pi * (side + front))
        )


def run_render(rigged, frame_mesh, out_png, o):
    frame = int(o["frame"])
    width, height_px = int(o["width"]), int(o["height"])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = ml.imported(lambda: bpy.ops.import_scene.gltf(filepath=rigged, merge_vertices=True))
    arm = next(x for x in objs if x.type == "ARMATURE")
    for x in objs:
        x.animation_data_clear()
    c = max((x for x in objs if x.type == "MESH"), key=lambda x: len(x.data.vertices))
    for x in list(bpy.data.objects):
        if x.type == "MESH" and x is not c:
            bpy.data.objects.remove(x, do_unlink=True)

    own_paint = has_own_paint(c)
    if not own_paint:
        c.data.materials.clear()
        mat = bpy.data.materials.new("clay")
        mat.diffuse_color = (0.74, 0.72, 0.68, 1.0)
        c.data.materials.append(mat)
    c.data.shade_smooth()
    if not any(m.type == "ARMATURE" for m in c.modifiers):
        am = c.modifiers.new("Armature", "ARMATURE")
        am.object = arm

    apply_stride_pose(arm, frame)
    bpy.context.view_layer.update()

    # Shared camera strictly from FRAME_MESH.obj so every candidate shares one camera.
    f_objs = ml.imported(lambda: bpy.ops.wm.obj_import(filepath=frame_mesh))
    fm = next(x for x in f_objs if x.type == "MESH")
    fv = ml.coords(fm)
    for x in f_objs:
        bpy.data.objects.remove(x, do_unlink=True)

    lo, hi = np.percentile(fv, 1, axis=0), np.percentile(fv, 99, axis=0)
    body_h = max(float(hi[2] - lo[2]), 1e-4)
    body_w = float(max(hi[0] - lo[0], hi[1] - lo[1]))
    # HLH follow camera: 3.0 m behind a 1.75 m character at 45 deg vertical FOV (~70% height),
    # widened proportionally only for sprawling quadrupeds wider than tall so limbs stay in frame.
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

    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "TEXTURE" if own_paint else "MATERIAL"
    sc.display.shading.show_cavity = False
    sc.display.shading.show_specular_highlight = True
    sc.render.resolution_x = width
    sc.render.resolution_y = height_px
    sc.render.film_transparent = False
    sc.world = bpy.data.worlds.new("w")
    sc.world.color = (0.125, 0.137, 0.165)
    sc.view_settings.view_transform = "Standard"

    cam_data = bpy.data.cameras.new("gamecam")
    cam_data.sensor_fit = "VERTICAL"
    try:
        cam_data.angle_y = fov_y
    except (AttributeError, TypeError):
        cam_data.angle = fov_y
    cam_data.clip_start = arm_len * 0.01
    cam_data.clip_end = arm_len * 20.0
    cam = bpy.data.objects.new("gamecam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.location = cam_pos
    cam.rotation_euler = (focus - cam_pos).to_track_quat("-Z", "Y").to_euler()

    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    sc.render.filepath = out_png
    bpy.ops.render.render(write_still=True)
    print(f"GAME_FRAME frame={frame} body_h={body_h:.3f} arm={arm_len:.3f} -> {out_png}")


def main():
    mode, pos, o = parse_args()
    if mode == "plant_swap":
        run_plant_swap(pos[0], pos[1])
    else:
        run_render(pos[0], pos[1], pos[2], o)


if __name__ == "__main__":
    main()
