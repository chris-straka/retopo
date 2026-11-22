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


TRUNK_NAME_WORDS = ("spine", "hips", "pelvis", "root", "torso", "head", "neck", "tail")
TRUNK_HUB_WORDS = ("spine", "hips", "pelvis", "root", "torso", "chest")
LIMB_NAME_WORDS = ("leg", "arm", "tail", "neck", "wing", "extra")
RIGHT_SUFFIXES = (".R", "_R", "Right", ".r", "_r")
LEFT_SUFFIXES = (".L", "_L", "Left", ".l", "_l")


def _find_armature(mesh_ob, arm=None):
    if arm is not None:
        return arm
    if mesh_ob.parent is not None and mesh_ob.parent.type == "ARMATURE":
        return mesh_ob.parent
    for mod in mesh_ob.modifiers:
        if mod.type == "ARMATURE" and mod.object is not None:
            return mod.object
    return None


def _rig_span(arm, mesh_ob=None):
    pts = []
    if arm is not None:
        pts.extend(arm.matrix_world @ b.head_local for b in arm.data.bones)
        pts.extend(arm.matrix_world @ b.tail_local for b in arm.data.bones)
    arm_span = 0.0
    if pts:
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        zs = [p.z for p in pts]
        arm_span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    mesh_span = 0.0
    if mesh_ob is not None and len(mesh_ob.data.vertices) > 0:
        mv = ml.coords(mesh_ob)
        mesh_span = float(np.max(np.max(mv, axis=0) - np.min(mv, axis=0)))
    return max(arm_span, mesh_span, 1e-4)


def _rig_sym_x(arm):
    """Return the rig's lateral symmetry plane X from its root bone (or 0.0)."""
    if arm is None or not arm.data.bones:
        return 0.0
    roots = [b for b in arm.data.bones if b.parent is None]
    if len(roots) == 1:
        return float((arm.matrix_world @ roots[0].head_local).x)
    return 0.0


def swap_limb_groups(mesh_ob, arm=None):
    """Swap left/right limb vertex groups so opposite limbs pull each other's skin.
    Pairs named L/R groups first, then remaining limb groups by mirrored bone heads
    (x -> 2*sym_x - x within 2% of span), failing loudly below 2 pairs on a limbed rig."""
    arm = _find_armature(mesh_ob, arm)
    vgs = mesh_ob.vertex_groups
    by_name = {g.name: g for g in vgs}
    counts = {g.index: 0 for g in vgs}
    for v in mesh_ob.data.vertices:
        for g in v.groups:
            if g.weight > 0.01:
                counts[g.group] = counts.get(g.group, 0) + 1

    pairs = []
    seen = set()
    suffixes = ((".L", ".R"), ("_L", "_R"), (".l", ".r"), ("_l", "_r"), ("Left", "Right"))
    for name in sorted(by_name):
        if name in seen:
            continue
        low = name.lower()
        if any(k in low for k in TRUNK_NAME_WORDS):
            continue
        for lsuf, rsuf in suffixes:
            if name.endswith(lsuf):
                partner = name[:-len(lsuf)] + rsuf
                if partner in by_name and partner not in seen:
                    pairs.append((by_name[name], by_name[partner]))
                    seen.update((name, partner))
                    break

    is_limbed = False
    if arm is not None:
        span = _rig_span(arm, mesh_ob)
        sym_x = _rig_sym_x(arm)
        tol = 0.02 * span
        mid_eps = 0.01 * span
        heads = {b.name: arm.matrix_world @ b.head_local for b in arm.data.bones}
        tails = {b.name: arm.matrix_world @ b.tail_local for b in arm.data.bones}
        off_midline = [
            b for b in arm.data.bones
            if not any(k in b.name.lower() for k in TRUNK_NAME_WORDS)
            and max(abs(heads[b.name].x - sym_x), abs(tails[b.name].x - sym_x)) > 0.02 * span
        ]
        is_limbed = len(off_midline) >= 2

        left_cands = []
        right_cands = []
        for b in arm.data.bones:
            if b.name not in by_name or b.name in seen:
                continue
            if any(k in b.name.lower() for k in TRUNK_NAME_WORDS):
                continue
            hx = heads[b.name].x
            if hx < sym_x - mid_eps:
                left_cands.append(b)
            elif hx > sym_x + mid_eps:
                right_cands.append(b)

        matches = []
        for la in left_cands:
            ha = heads[la.name]
            ta = tails[la.name]
            mirror_ha = Vector((2.0 * sym_x - ha.x, ha.y, ha.z))
            mirror_ta = Vector((2.0 * sym_x - ta.x, ta.y, ta.z))
            for rb in right_cands:
                hb = heads[rb.name]
                d_head = (mirror_ha - hb).length
                if d_head <= tol:
                    d_tail = (mirror_ta - tails[rb.name]).length
                    matches.append((d_head, d_tail, la.name, rb.name))
        matches.sort()
        for _, _, na, nb in matches:
            if na in seen or nb in seen:
                continue
            pairs.append((by_name[na], by_name[nb]))
            seen.update((na, nb))
    else:
        non_trunk = [
            g for g in vgs
            if not any(k in g.name.lower() for k in TRUNK_NAME_WORDS)
        ]
        is_limbed = len(non_trunk) >= 3

    active_pairs = [
        (ga, gb) for ga, gb in pairs
        if counts.get(ga.index, 0) > 0 or counts.get(gb.index, 0) > 0
    ]
    if is_limbed and len(active_pairs) < 2:
        raise SystemExit(
            f"plant_swap: found only {len(active_pairs)} mirrored limb pair(s) (< 2) on limbed rig"
        )

    for idx, (ga, gb) in enumerate(pairs):
        na, nb = ga.name, gb.name
        ga.name = f"__swap_tmp_{idx}__"
        gb.name = na
        ga.name = nb
    return len(active_pairs)


def run_plant_swap(in_glb, out_glb):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objs = ml.imported(lambda: bpy.ops.import_scene.gltf(filepath=in_glb))
    arm = next(x for x in objs if x.type == "ARMATURE")
    meshes = [x for x in objs if x.type == "MESH" and x.vertex_groups]
    if not meshes:
        raise SystemExit("plant_swap: no skinned mesh")
    swapped = sum(swap_limb_groups(m, arm=arm) for m in meshes)
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


def _name_kind(name):
    n = name.lower()
    return next((w for w in LIMB_NAME_WORDS if w in n), None)


def _is_named_trunk(name):
    n = name.lower()
    return any(w in n for w in TRUNK_HUB_WORDS)


def _classify_bones(arm):
    """Classify each poseable bone as (kind, is_root, side_phase), using bone names
    when descriptive and falling back to skeleton geometry/position otherwise."""
    bones = list(arm.data.bones)
    if not bones:
        return {}
    heads = {b.name: arm.matrix_world @ b.head_local for b in bones}
    tails = {b.name: arm.matrix_world @ b.tail_local for b in bones}
    span = _rig_span(arm)
    sym_x = _rig_sym_x(arm)
    mid_eps = 0.08 * span
    side_eps = 0.01 * span

    def subtree(root_bone):
        out = []
        stack = [root_bone]
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(cur.children)
        return out

    trunk_names = set()
    for b in bones:
        if _name_kind(b.name) is not None:
            continue
        if _is_named_trunk(b.name) or b.parent is None:
            trunk_names.add(b.name)
            continue
        is_midline = abs(heads[b.name].x - sym_x) <= mid_eps and abs(tails[b.name].x - sym_x) <= mid_eps
        if is_midline:
            sub = subtree(b)
            xs = [heads[d.name].x - sym_x for d in sub] + [tails[d.name].x - sym_x for d in sub]
            if min(xs) < -0.04 * span and max(xs) > 0.04 * span:
                trunk_names.add(b.name)

    # Any generic-named ancestor of a trunk/hub bone is also trunk (multi-bone spine).
    for name in list(trunk_names):
        cur = arm.data.bones[name].parent
        while cur is not None:
            if _name_kind(cur.name) is not None:
                break
            trunk_names.add(cur.name)
            cur = cur.parent

    # Find chain root for each non-trunk bone that lacks an explicit limb name.
    chain_roots = {}
    for b in bones:
        if _name_kind(b.name) is not None or b.name in trunk_names:
            continue
        cur = b
        while (
            cur.parent is not None
            and cur.parent.name not in trunk_names
            and _name_kind(cur.parent.name) is None
        ):
            cur = cur.parent
        chain_roots[b.name] = cur

    chain_members = {}
    for bname, croot in chain_roots.items():
        chain_members.setdefault(croot.name, []).append(arm.data.bones[bname])

    chain_info = {}
    for croot_name, members in chain_members.items():
        croot = arm.data.bones[croot_name]
        hub = croot.parent if croot.parent is not None else croot
        hub_head = heads[hub.name]
        root_head = heads[croot_name]
        member_names = {m.name for m in members}
        leaves = [m for m in members if not any(ch.name in member_names for ch in m.children)]
        tip_pts = [tails[m.name] for m in (leaves or members)]
        tip_min_z = min(p.z for p in tip_pts)
        tip_far = max(tip_pts, key=lambda p: (p - root_head).length)
        chain_x = tip_far.x if abs(tip_far.x - sym_x) > abs(root_head.x - sym_x) else root_head.x
        if tip_min_z < hub_head.z - 0.05 * span:
            gk = "leg"
        elif abs(chain_x - sym_x) > 0.12 * span:
            gk = "arm"
        elif tip_far.y < hub_head.y:
            gk = "neck"
        else:
            gk = "tail"
        chain_info[croot_name] = (gk, chain_x)

    classified = {}
    for b in bones:
        nk = _name_kind(b.name)
        if nk is not None:
            is_root = b.parent is None or _name_kind(b.parent.name) != nk
            sub = subtree(b)
            far_x = max(
                [heads[b.name].x] + [tails[d.name].x for d in sub],
                key=lambda x: abs(x - sym_x),
            )
            if b.name.endswith(RIGHT_SUFFIXES):
                side = 0.5
            elif b.name.endswith(LEFT_SUFFIXES):
                side = 0.0
            else:
                side = 0.5 if (far_x - sym_x) > side_eps else 0.0
            classified[b.name] = (nk, is_root, side)
        elif b.name in chain_roots:
            croot = chain_roots[b.name]
            gk, chain_x = chain_info[croot.name]
            is_root = b.name == croot.name
            if b.name.endswith(RIGHT_SUFFIXES):
                side = 0.5
            elif b.name.endswith(LEFT_SUFFIXES):
                side = 0.0
            else:
                side = 0.5 if (chain_x - sym_x) > side_eps else 0.0
            classified[b.name] = (gk, is_root, side)
    return classified


def apply_stride_pose(arm, frame, cycle=36):
    t = 2.0 * math.pi * frame / float(cycle)
    root_swing = math.radians(35.0)
    child_swing = math.radians(40.0)
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    classified = _classify_bones(arm)
    posed = 0
    for pb in arm.pose.bones:
        info = classified.get(pb.name)
        if not info:
            continue
        _, is_root, side = info
        head = arm.matrix_world @ pb.bone.head_local
        front = 0.20 if head.y < 0 else 0.0
        amp = root_swing if is_root else child_swing
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion(
            (1.0, 0.0, 0.0), amp * math.sin(t + 2.0 * math.pi * (side + front))
        )
        posed += 1
    if posed == 0:
        raise SystemExit(
            f"apply_stride_pose: 0 bones posed on armature {arm.name!r} "
            f"({len(arm.pose.bones)} bones)"
        )
    return posed


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
