# Headless Blender integration tests for scale-aware Cycles bake ray extrusion
# and Object-Mode freehand surface guide drawing.
#
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --factory-startup --python-exit-code 1 \
#       --python blender/tests/test_bake_and_guides.py

import os
import re
import shutil
import sys
import tempfile

try:
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
except ImportError:
    bpy = None
    Vector = None
    BVHTree = None

TMP = tempfile.gettempdir()
REPO = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
sys.path.insert(0, os.path.join(REPO, "blender"))

import retopo  # noqa: E402


def check(condition, message):
    print(("PASS" if condition else "FAIL") + ": " + message)
    if not condition:
        raise SystemExit(1)


def parse_guides_file(path):
    polylines = [[]]
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.split("#", 1)[0].strip()
            if not stripped:
                if polylines[-1]:
                    polylines.append([])
                continue
            parts = stripped.split()
            check(len(parts) == 3, f"guide line has 3 coords: {line!r}")
            polylines[-1].append(tuple(float(v) for v in parts))
    return [p for p in polylines if p]


def make_mat(name, color):
    mat = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0) or (
        mat.node_tree is None and hasattr(mat, "use_nodes")
    ):
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = color
    return mat


def add_annotation_stroke(scene, points_world):
    layer = retopo.ensure_guide_annotation_layer(scene)
    frame = layer.frames[0] if len(layer.frames) else layer.frames.new(scene.frame_current)
    stroke = frame.strokes.new()
    stroke.points.add(len(points_world))
    for i, pt in enumerate(points_world):
        stroke.points[i].co = pt
    return stroke


def test_scale_aware_bake():
    # 1. Auto scaling across tiny (0.02) and huge (100.0) meshes, plus neighbor
    #    isolation ("thin finger near leg"): green neighbor sits 0.03*scale away.
    def run_neighbor_fixture(scale):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.preferences.addon_enable(module="retopo")
        params = bpy.context.scene.retopo_params
        params.bake_size = 64
        params.bake_normal = False
        params.bake_roughness = False
        params.bake_metallic = False
        params.bake_ao = False
        params.bake_emission = False
        params.bake_extrusion_auto = True

        bpy.ops.mesh.primitive_cube_add(size=0.1 * scale, location=(0.0, 0.0, 0.0))
        high = bpy.context.active_object
        high.name = f"High_{scale}"
        high.data.materials.append(make_mat("Red", (1.0, 0.0, 0.0, 1.0)))

        # Neighbor thin limb (0.02*scale thick) sits 0.02*scale away from +X face of target box.
        bpy.ops.mesh.primitive_cube_add(
            size=0.02 * scale, location=(0.08 * scale, 0.0, 0.0)
        )
        neighbor = bpy.context.active_object
        neighbor.data.materials.append(make_mat("Green", (0.0, 1.0, 0.0, 1.0)))
        bpy.ops.object.select_all(action="DESELECT")
        high.select_set(True)
        neighbor.select_set(True)
        bpy.context.view_layer.objects.active = high
        bpy.ops.object.join()

        bpy.ops.mesh.primitive_cube_add(
            size=0.098 * scale, location=(0.0, 0.0, 0.0)
        )
        low = bpy.context.active_object
        low.name = f"Low_{scale}"

        diff_path = os.path.join(TMP, f"{low.name}_diffuse.png")
        if os.path.isfile(diff_path):
            os.remove(diff_path)

        bpy.ops.object.select_all(action="DESELECT")
        low.select_set(True)
        high.select_set(True)
        bpy.context.view_layer.objects.active = high
        bpy.context.scene.render.bake.cage_extrusion = 0.123
        bpy.context.scene.render.bake.max_ray_distance = 0.456
        res = bpy.ops.retopo.bake_textures()
        check("FINISHED" in res, f"auto bake at scale={scale} finished")
        check(
            abs(bpy.context.scene.render.bake.cage_extrusion - 0.123) < 1e-6
            and abs(bpy.context.scene.render.bake.max_ray_distance - 0.456) < 1e-6,
            f"scale={scale} restored scene bake extrusion and max_ray_distance",
        )
        report = bpy.context.scene.retopo_last_report
        m = re.search(
            r"rays: auto .*?\(extrusion=([0-9.eE+-]+), max_ray=([0-9.eE+-]+)\)",
            report,
        )
        check(m is not None, f"scale={scale} report logs auto ray settings: {report!r}")
        auto_extr = float(m.group(1))
        auto_ray = float(m.group(2))

        probe = bpy.data.images.load(diff_path)
        try:
            px = list(probe.pixels)
            reds = sum(
                1 for i in range(0, len(px), 4) if px[i] > 0.5 and px[i + 1] < 0.2
            )
            greens = sum(
                1 for i in range(0, len(px), 4) if px[i + 1] > 0.5 and px[i] < 0.2
            )
        finally:
            bpy.data.images.remove(probe)
        os.remove(diff_path)
        check(
            reds > 0 and greens == 0,
            f"scale={scale} auto bake isolated target from neighbor "
            f"(red={reds}, green={greens})",
        )
        return auto_extr, auto_ray, high, low

    extr_small, ray_small, _, _ = run_neighbor_fixture(0.02)
    extr_large, ray_large, _, _ = run_neighbor_fixture(100.0)
    ratio = extr_large / extr_small
    check(
        abs(ratio - 5000.0) < 10.0,
        f"extrusion scales linearly across 5000x mesh sizes ({extr_small:.6g} -> {extr_large:.6g}, ratio={ratio:.2f})",
    )
    check(
        abs((ray_large / ray_small) - 5000.0) < 10.0,
        "max_ray_distance scales linearly across 5000x mesh sizes",
    )

    # 2. Manual user override + scene length units: editing params.bake_extrusion
    #    automatically flips bake_extrusion_auto off and honors exact units.
    extr_unit, _, high_u, low_u = run_neighbor_fixture(1.0)
    params = bpy.context.scene.retopo_params
    bpy.context.scene.unit_settings.scale_length = 0.01
    bpy.context.scene.unit_settings.length_unit = "CENTIMETERS"
    params.bake_extrusion = 0.05  # explicit override reaching across 0.03 gap
    check(
        params.bake_extrusion_auto is False,
        "assigning bake_extrusion flips bake_extrusion_auto off",
    )
    params.bake_ray_distance = 0.0  # unlimited
    bpy.context.scene.retopo_last_report = ""
    diff_u = os.path.join(TMP, f"{low_u.name}_diffuse.png")
    if os.path.isfile(diff_u):
        os.remove(diff_u)
    bpy.ops.object.select_all(action="DESELECT")
    low_u.select_set(True)
    high_u.select_set(True)
    bpy.context.view_layer.objects.active = high_u
    res = bpy.ops.retopo.bake_textures()
    check("FINISHED" in res, "manual override bake finished")
    check(
        "rays: manual (extrusion=0.05, max_ray=0)"
        in bpy.context.scene.retopo_last_report,
        "manual override report preserves exact extrusion & ray_distance",
    )
    probe = bpy.data.images.load(diff_u)
    try:
        px = list(probe.pixels)
        greens = sum(
            1 for i in range(0, len(px), 4) if px[i + 1] > 0.5 and px[i] < 0.2
        )
    finally:
        bpy.data.images.remove(probe)
    os.remove(diff_u)
    check(
        greens > 0,
        f"manual 0.05 override reached neighbor (green={greens}), proving auto {extr_unit:.4g} isolated it",
    )

    # 3. Remesh + Bake All on a huge-scale (100x) mesh succeeds cleanly.
    binary = retopo.find_retopo_binary(
        bpy.context.preferences.addons[retopo.ADDON_ID].preferences.retopo_binary
    )
    if binary:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.preferences.addon_enable(module="retopo")
        bpy.ops.mesh.primitive_cube_add(size=100.0)
        big = bpy.context.active_object
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.subdivide(number_cuts=2)
        bpy.ops.object.mode_set(mode="OBJECT")
        big.data.materials.append(make_mat("BigA", (1.0, 0.0, 0.0, 1.0)))
        big.data.materials.append(make_mat("BigB", (0.0, 0.0, 1.0, 1.0)))
        for p in big.data.polygons:
            p.material_index = 0 if p.center.x < 0.0 else 1
        params = bpy.context.scene.retopo_params
        params.target_quads = 120
        params.bake_size = 128
        params.bake_normal = True
        params.bake_roughness = False
        params.bake_metallic = False
        params.bake_ao = False
        params.bake_emission = False
        res = bpy.ops.retopo.remesh_and_bake()
        check("FINISHED" in res, f"remesh_and_bake on 100-unit mesh finished: {res}")
        low_big = bpy.data.objects.get(f"{big.name}_retopo")
        check(low_big is not None, "remesh_and_bake created _retopo object")
        for kind in ("diffuse", "normal"):
            p = os.path.join(TMP, f"{low_big.name}_{kind}.png")
            check(os.path.isfile(p), f"100-unit {kind} baked")
            if kind == "diffuse":
                probe = bpy.data.images.load(p)
                try:
                    spread = max(probe.pixels) - min(probe.pixels)
                    check(
                        spread > 0.5,
                        f"100-unit remesh_and_bake captured colors (spread {spread:.3f})",
                    )
                finally:
                    bpy.data.images.remove(probe)
            os.remove(p)


def test_object_mode_surface_guides():
    work = tempfile.mkdtemp(prefix="retopo_guides_test_")
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.preferences.addon_enable(module="retopo")
        scene = bpy.context.scene
        params = scene.retopo_params

        # 1. Start in Edit Mode on a dense subdivided cylinder (clearing primitive's
        #    initial select-all), then verify draw_guides switches cleanly to
        #    Object Mode + Surface placement.
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.5, depth=2.0)
        obj = bpy.context.active_object
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.subdivide(number_cuts=2)
        bpy.ops.mesh.select_all(action="DESELECT")
        res = bpy.ops.retopo.draw_guides()
        check("FINISHED" in res, f"draw_guides finished: {res}")
        check(bpy.context.mode == "OBJECT", "draw_guides keeps/switches to Object Mode")
        check(
            scene.tool_settings.annotation_stroke_placement_view3d == "SURFACE",
            "draw_guides set annotation placement to SURFACE",
        )
        check(params.guides_enabled is True, "draw_guides enabled guides_enabled")
        layer = retopo.get_guide_annotation_layer(scene)
        check(
            layer is not None and layer.info == retopo.GUIDE_ANNOTATION_LAYER_NAME,
            "dedicated RetopoForge Guides layer created",
        )
        check(
            all(
                abs(a - b) < 1e-5
                for a, b in zip(layer.color[:3], retopo.GUIDE_ANNOTATION_COLOR)
            ),
            "layer uses distinct guide color",
        )

        # Apply non-identity transform so local != world.
        obj.location = (2.0, -1.5, 3.0)
        obj.rotation_euler = (0.25, -0.15, 0.4)
        obj.scale = (1.5, 1.0, 2.0)
        bpy.context.view_layer.update()

        # Paint a scripted stroke on the +X side of the cylinder in world space,
        # with an off-surface spike in the middle and a tiny stub.
        mw = obj.matrix_world
        # Local surface points on +X side (r=0.5): (0.5*cos(a), 0.5*sin(a), z)
        stroke1_world = [
            mw @ Vector((0.5, 0.0, -0.6)),
            mw @ Vector((0.5, 0.0, -0.2)),
            mw @ Vector((0.5, 0.0, 0.0)),
            mw @ Vector((4.0, 0.0, 0.1)),  # far off mesh -> split gap
            mw @ Vector((0.5, 0.0, 0.25)),
            mw @ Vector((0.5, 0.0, 0.65)),
        ]
        add_annotation_stroke(scene, stroke1_world)
        # Tiny short stub (< 0.5 * edge_len) -> dropped
        add_annotation_stroke(
            scene,
            [mw @ Vector((0.0, 0.5, 0.0)), mw @ Vector((0.0, 0.5, 0.005))],
        )

        params.target_quads = 250
        params.guides_mirror = False
        guides_path = os.path.join(work, "surface_guides.txt")
        res = bpy.ops.retopo.export_guides(filepath=guides_path)
        check("FINISHED" in res, f"export_guides finished: {res}")
        polys = parse_guides_file(guides_path)
        check(
            len(polys) == 2,
            f"gap split stroke into 2 polylines and dropped stub (got {len(polys)})",
        )

        # Every exported local point must sit on the local mesh surface (< 1e-4).
        bvh = BVHTree.FromPolygons(
            [v.co for v in obj.data.vertices],
            [tuple(p.vertices) for p in obj.data.polygons],
        )
        max_surf_err = max(bvh.find_nearest(Vector(p))[3] for poly in polys for p in poly)
        check(
            max_surf_err < 1e-4,
            f"every exported point lies on local mesh surface (max err {max_surf_err:.2e})",
        )

        # Higher target_quads yields finer resampling density along a curved stroke.
        add_annotation_stroke(
            scene,
            [
                mw @ Vector((0.5 * 0.866, 0.5 * 0.5, 0.0)),
                mw @ Vector((0.5 * 0.5, 0.5 * 0.866, 0.0)),
                mw @ Vector((0.0, 0.5, 0.0)),
            ],
        )
        params.target_quads = 80
        coarse_polys = retopo.extract_annotation_guide_polylines(scene, obj, params)
        params.target_quads = 2000
        fine_polys = retopo.extract_annotation_guide_polylines(scene, obj, params)
        check(
            sum(len(p) for p in fine_polys) > sum(len(p) for p in coarse_polys),
            "target_quads controls stroke resampling density",
        )

        # 2. Mirror option: on a bilateral mesh (symmetric across X, slightly
        #    asymmetric in Y/Z so X is detected as the unique symmetry plane),
        #    a +X stroke mirrors onto -X while an X=0 centerline stroke is not duplicated.
        for v in obj.data.vertices:
            if v.co.y > 0.2:
                v.co.y += 0.18
            if v.co.z > 0.4:
                v.co.z += 0.15
        obj.data.update()
        # Centerline stroke at x=0 (must not be duplicated when mirrored across X).
        add_annotation_stroke(
            scene,
            [
                mw @ Vector((0.0, -0.45, -0.3)),
                mw @ Vector((0.0, -0.45, 0.3)),
            ],
        )
        params.target_quads = 250
        params.guides_mirror = False
        unmirrored_polys = retopo.extract_annotation_guide_polylines(scene, obj, params)
        params.guides_mirror = True
        mirrored_path = os.path.join(work, "mirrored_guides.txt")
        res = bpy.ops.retopo.export_guides(filepath=mirrored_path)
        check("FINISHED" in res, "mirrored export_guides finished")
        mirrored_polys = parse_guides_file(mirrored_path)
        off_plane_count = sum(
            1
            for poly in unmirrored_polys
            if any(abs(p[0]) > 0.1 for p in poly)
        )
        check(
            len(mirrored_polys) == len(unmirrored_polys) + off_plane_count,
            f"mirroring added mirrors for {off_plane_count} off-plane strokes "
            f"without duplicating centerline ({len(unmirrored_polys)} -> {len(mirrored_polys)})",
        )
        has_neg_x = any(p[0] < -0.2 for poly in mirrored_polys for p in poly)
        has_pos_x = any(p[0] > 0.2 for poly in mirrored_polys for p in poly)
        check(has_pos_x and has_neg_x, "mirrored guides populate both +X and -X sides")

        # Preview polylines in world space match exported local polylines transformed by matrix_world.
        params.guides_preview = True
        preview_world = retopo.guide_preview_world_polylines(scene, obj, params)
        check(
            len(preview_world) == len(mirrored_polys),
            "preview polyline count matches exported count",
        )
        max_prev_err = max(
            (mw @ Vector(lp) - Vector(wp)).length
            for lpoly, wpoly in zip(mirrored_polys, preview_world)
            for lp, wp in zip(lpoly, wpoly)
        )
        check(
            max_prev_err < 1e-5,
            f"preview world polylines match exported local polylines (err {max_prev_err:.2e})",
        )

        # 3. Merge test: Edit-Mode edge selection + Object-Mode strokes export together.
        top_z = max(v.co.z for v in obj.data.vertices)
        for e in obj.data.edges:
            e.select = all(obj.data.vertices[i].co.z > top_z - 1e-5 for i in e.vertices)
        merged_path = os.path.join(work, "merged_guides.txt")
        res = bpy.ops.retopo.export_guides(filepath=merged_path)
        check("FINISHED" in res, "merged export_guides finished")
        merged_polys = parse_guides_file(merged_path)
        check(
            len(merged_polys) > len(mirrored_polys),
            f"merged export includes both edge selection and surface strokes ({len(merged_polys)} > {len(mirrored_polys)})",
        )
        for e in obj.data.edges:
            e.select = False

        # 4. Save and reload persistence: guides layer + strokes survive .blend reload.
        obj_name = obj.name
        blend_path = os.path.join(work, "guides_persist.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        bpy.ops.wm.open_mainfile(filepath=blend_path)
        bpy.ops.preferences.addon_enable(module="retopo")
        reloaded_obj = bpy.data.objects[obj_name]
        reloaded_scene = bpy.context.scene
        reloaded_scene.view_layers[0].objects.active = reloaded_obj
        reloaded_obj.select_set(True)
        reloaded_layer = retopo.get_guide_annotation_layer(reloaded_scene)
        check(reloaded_layer is not None, "guides layer survived save/reload")
        reloaded_polys = retopo.collect_guide_polylines(
            reloaded_scene, reloaded_obj, reloaded_scene.retopo_params
        )
        check(
            len(reloaded_polys) == len(mirrored_polys),
            "reloaded guides produce identical polyline count",
        )

        # 5. End-to-end remesh consumes Object-Mode surface strokes (no edge selection).
        binary = retopo.find_retopo_binary(
            bpy.context.preferences.addons[retopo.ADDON_ID].preferences.retopo_binary
        )
        if binary:
            reloaded_scene.retopo_params.guides_enabled = True
            reloaded_scene.retopo_params.target_quads = 200
            res = bpy.ops.retopo.remesh()
            check("FINISHED" in res, f"Object-Mode surface-guided remesh finished: {res}")
            check(len(reloaded_obj.data.polygons) > 0, "guided remesh produced polygons")

        # 6. Clear guide strokes empties the layer cleanly.
        res = bpy.ops.retopo.clear_guide_strokes()
        check("FINISHED" in res, "clear_guide_strokes finished")
        check(
            len(retopo.raw_annotation_strokes(bpy.context.scene)) == 0,
            "clear_guide_strokes removed all strokes",
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    try:
        test_scale_aware_bake()
        test_object_mode_surface_guides()
        print("ALL BAKE AND GUIDE HEADLESS TESTS PASSED")
    finally:
        try:
            bpy.ops.preferences.addon_disable(module="retopo")
        except Exception:
            pass


if __name__ == "__main__":
    if bpy is None:
        raise SystemExit("test_bake_and_guides.py must be run inside Blender")
    main()
