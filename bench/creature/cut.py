"""Blender: the bench's rival cuts.

    blender -b --factory-startup -P bench/creature/cut.py -- \
        quadriflow|decimate|decimate-repaired|artist|clean-copy IN OUT.obj TRIS \
        [--repair] [--holes SHARE] [--report R.json]

quadriflow         Blender's QuadriFlow at TRIS/2 quads; the input is first
                   decimated to max(6 x TRIS, 150k) triangles (as meshkit's
                   clean_copy does), and quadriflow's mesh is laid back onto the
                   source surface (nearest point), so its rounded shape does not
                   count against it. A surface QuadriFlow refuses (open,
                   non-manifold) gets Blender's voxel remesh first, its voxel
                   sized so the shell has about that many faces.
decimate           Blender's collapse decimate to TRIS triangles (raw, no hole
                   repair unless `--repair` is passed).
decimate-repaired  4-stage Tripo hole repair (`untangle` -> `mend_holes` ->
                   Blender collapse `DECIMATE` -> `untangle` -> `mend_holes` ->
                   `cap_rims` -> `close_rims_in_obj`), defaulting `--holes` to
                   5.0 so closed monster sculpts seal without eating thin valid
                   geometry (pass `--holes 0.08` to keep large openings open,
                   or `0` to count only).
artist             IN is the artist's GLB: its mesh, welded, its quads recovered
                   (consecutive triangle pairs; TRIS ignored)
clean-copy         meshkit's tools/clean_copy.py (--steps shell,cut to TRIS
                   triangles); resolves MESHKIT_DIR env var or sibling ../meshkit
"""
import json
import os
import subprocess
import sys
import tempfile

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hole_repair as hr  # noqa: E402


def parse_args(argv):
    opts = {"repair": False, "holes": None, "report": None}
    pos = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--repair":
            opts["repair"] = True
            i += 1
        elif tok == "--holes":
            if i + 1 >= len(argv):
                raise SystemExit("cut: --holes requires a numeric share")
            opts["holes"] = float(argv[i + 1])
            i += 2
        elif tok.startswith("--holes="):
            opts["holes"] = float(tok.split("=", 1)[1])
            i += 1
        elif tok == "--report":
            if i + 1 >= len(argv):
                raise SystemExit("cut: --report requires a path")
            opts["report"] = argv[i + 1]
            i += 2
        elif tok.startswith("--report="):
            opts["report"] = tok.split("=", 1)[1]
            i += 1
        elif tok.startswith("--"):
            raise SystemExit(f"cut: unknown flag {tok!r}")
        else:
            pos.append(tok)
            i += 1
    if len(pos) < 4:
        raise SystemExit(__doc__)
    how, src, out, tris = pos[0], pos[1], pos[2], int(pos[3])
    if how == "decimate-repaired":
        opts["repair"] = True
    if opts["holes"] is None:
        opts["holes"] = 5.0 if opts["repair"] else 0.0
    return how, src, out, tris, opts


def find_meshkit():
    """Locate meshkit's tools/clean_copy.py via MESHKIT_DIR or sibling ../meshkit."""
    env = os.environ.get("MESHKIT_DIR")
    if env:
        cc = os.path.join(os.path.abspath(os.path.expanduser(env)), "tools", "clean_copy.py")
        if os.path.isfile(cc):
            return cc
        raise SystemExit(f"cut: MESHKIT_DIR={env!r} has no tools/clean_copy.py")
    repo_root = os.path.abspath(os.path.join(HERE, "..", ".."))
    sib = os.path.abspath(os.path.join(repo_root, "..", "meshkit", "tools", "clean_copy.py"))
    if os.path.isfile(sib):
        return sib
    raise SystemExit("cut: cannot find meshkit tools/clean_copy.py; set MESHKIT_DIR to your meshkit checkout")


def tri_count(ob):
    return sum(len(p.vertices) - 2 for p in ob.data.polygons)


def main():
    a = sys.argv[sys.argv.index("--") + 1:]
    how, src, out, tris, opts = parse_args(a)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if src.lower().endswith(".obj"):
        bpy.ops.wm.obj_import(filepath=src)
    else:
        bpy.ops.import_scene.gltf(filepath=src, merge_vertices=True)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    bpy.context.view_layer.update()
    world = ob.matrix_world.copy()  # a node above (a turn, a scale) counts
    ob.parent = None
    ob.matrix_world = world
    ob.modifiers.clear()
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    repair_info = None
    holes_limit = opts["holes"]

    if how in ("decimate", "decimate-repaired"):
        if opts["repair"]:
            tangles_before = hr.untangle(ob) if holes_limit >= 1.0 else None
            holes_before = hr.mend_holes(ob, holes_limit)
        else:
            tangles_before = holes_before = None

        source_tris = tri_count(ob)
        if not opts["repair"] or (tris > 0 and source_tris > tris):
            m = ob.modifiers.new("d", "DECIMATE")
            m.ratio = min(1.0, tris / max(1, source_tris))
            if opts["repair"]:
                m.use_collapse_triangulate = True
            bpy.ops.object.modifier_apply(modifier=m.name)

        if opts["repair"]:
            tangles_after_cut = hr.untangle(ob) if holes_limit >= 1.0 else None
            holes_after_cut = hr.mend_holes(ob, holes_limit)
            capped = hr.cap_rims(ob, holes_limit)
            if holes_limit >= 1.0 and capped.get("still_tangled", 0) > 0:
                hr.untangle(ob)
                hr.mend_holes(ob, holes_limit)
                capped = hr.cap_rims(ob, holes_limit)
            repair_info = {
                "mode": how,
                "holes_limit": holes_limit,
                "source_tris": source_tris,
                "tangles": tangles_before,
                "holes": holes_before,
                "tangles_after_cut": tangles_after_cut,
                "holes_after_cut": holes_after_cut,
                "capped": capped,
            }
    elif how == "quadriflow":
        if opts["repair"]:
            tangles_before = hr.untangle(ob) if holes_limit >= 1.0 else None
            holes_before = hr.mend_holes(ob, holes_limit)
            capped = hr.cap_rims(ob, holes_limit)
            repair_info = {
                "mode": how,
                "holes_limit": holes_limit,
                "source_tris": tri_count(ob),
                "tangles": tangles_before,
                "holes": holes_before,
                "tangles_after_cut": None,
                "holes_after_cut": None,
                "capped": capped,
            }
        n = len(ob.data.polygons)
        src_copy = ob.copy()
        src_copy.data = ob.data.copy()
        bpy.context.scene.collection.objects.link(src_copy)
        pre = max(6 * tris, 150000)
        if n > pre:
            m = ob.modifiers.new("d", "DECIMATE")
            m.ratio = pre / n
            bpy.ops.object.modifier_apply(modifier=m.name)
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        # Blender refuses a mesh with any edge shorter than 1e-4 units
        # ("needs to be manifold"), which a dense one-unit creature always
        # has: work at 100 units across, then scale back.
        up = 100.0 / max(ob.dimensions)
        for v in ob.data.vertices:
            v.co *= up
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=2e-4)
        bm.to_mesh(ob.data)
        bm.free()
        ob.data.update()

        def qf():
            try:
                return "FINISHED" in bpy.ops.object.quadriflow_remesh(
                    mode="FACES", target_faces=tris // 2, use_mesh_symmetry=False, use_preserve_sharp=False,
                    use_preserve_boundary=False, smooth_normals=False, seed=0)
            except RuntimeError:
                return False
        if not qf():
            # QuadriFlow wants a closed, consistently wound surface: give it
            # Blender's voxel remesh of the source (0.25% of the largest side)
            # sized from the area so it lands near `pre` faces with no
            # decimation (a collapse would break the closed surface again)
            area = sum(p.area for p in ob.data.polygons)
            ob.data.remesh_voxel_size = max((area / pre) ** 0.5, 1e-4 * max(ob.dimensions))
            bpy.ops.object.voxel_remesh()
            print("CUT quadriflow: voxel shell first")
            if not qf():
                raise SystemExit("cut: quadriflow failed")
        for v in ob.data.vertices:
            v.co /= up
        sv = [v.co.copy() for v in src_copy.data.vertices]
        bvh = BVHTree.FromPolygons(sv, [tuple(p.vertices) for p in src_copy.data.polygons])
        for v in ob.data.vertices:
            hit = bvh.find_nearest(v.co)
            if hit[0] is not None:
                v.co = hit[0]
        bpy.data.objects.remove(src_copy, do_unlink=True)
    elif how == "artist":
        # one vertex a position (the GLB splits at UV seams and hard edges)
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6 * max(ob.dimensions))
        # An exporter writes each quad as two triangles one after the other,
        # so the artist's quads are recovered exactly by pairing neighbours
        # in file order that share an edge (and are not folded more than 60
        # degrees); triangles the artist made stay triangles.
        import math
        bm.faces.ensure_lookup_table()
        join, i, n_faces = [], 0, len(bm.faces)
        while i + 1 < n_faces:
            f, g = bm.faces[i], bm.faces[i + 1]
            shared = [e for e in f.edges if e in g.edges]
            if len(f.verts) == 3 and len(g.verts) == 3 and len(shared) == 1 \
                    and f.normal.angle(g.normal, math.pi) < math.radians(60):
                join.append(shared[0])
                i += 2
            else:
                i += 1
        if join:
            bmesh.ops.dissolve_edges(bm, edges=join, use_verts=False)
        bm.to_mesh(ob.data)
        bm.free()
    elif how == "clean-copy":
        clean_copy_py = find_meshkit()
        with tempfile.TemporaryDirectory() as td:
            src_glb = os.path.join(td, "in.glb")
            cc_out = os.path.join(td, "out")
            for o in list(bpy.context.scene.objects):
                o.select_set(o is ob)
            bpy.context.view_layer.objects.active = ob
            bpy.ops.export_scene.gltf(
                filepath=src_glb,
                export_format="GLB",
                use_selection=True,
                export_apply=True,
                export_yup=True,
                export_animations=False,
            )
            cmd = [
                bpy.app.binary_path,
                "-b",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python",
                clean_copy_py,
                "--",
                src_glb,
                "--out",
                cc_out,
                "--steps",
                "shell,cut",
                "--tris",
                str(tris),
                "--name",
                "cc",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit(
                    f"cut: clean-copy failed (exit {r.returncode}):\n"
                    f"{(r.stdout + os.linesep + r.stderr)[-1200:]}"
                )
            cut_ply = os.path.join(cc_out, f"cut_{tris}.ply")
            if not os.path.isfile(cut_ply):
                raise SystemExit(f"cut: clean-copy did not write expected {cut_ply}")
            bpy.data.objects.remove(ob, do_unlink=True)
            before = set(bpy.data.objects)
            bpy.ops.wm.ply_import(filepath=cut_ply, forward_axis="Y", up_axis="Z")
            imported = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
            if not imported:
                raise SystemExit(f"cut: clean-copy imported no mesh from {cut_ply}")
            ob = imported[0]
            bpy.context.view_layer.objects.active = ob
            for o in list(bpy.context.scene.objects):
                o.select_set(o is ob)
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    else:
        raise SystemExit(f"cut: unknown {how}")

    for o in list(bpy.context.scene.objects):
        o.select_set(o is ob)
    export_kw = {
        "filepath": out,
        "export_selected_objects": True,
        "export_uv": False,
        "export_normals": False,
        "export_materials": False,
    }
    if opts["repair"]:
        export_kw["export_triangulated_mesh"] = True
    bpy.ops.wm.obj_export(**export_kw)

    if opts["repair"] and how in ("decimate", "decimate-repaired") and holes_limit >= 1.0:
        repair_info["holes_in_file"] = hr.close_rims_in_obj(out, limit=holes_limit)
    elif repair_info is not None:
        repair_info["holes_in_file"] = None

    if repair_info is not None:
        _, out_faces = hr._read_obj(out)
        repair_info["tris"] = len(out_faces)
        if opts["report"]:
            os.makedirs(os.path.dirname(os.path.abspath(opts["report"])), exist_ok=True)
            with open(opts["report"], "w", encoding="utf-8") as fp:
                json.dump(repair_info, fp, indent=1, sort_keys=True)
        print("CUT_REPAIR", json.dumps(repair_info))

    print("CUT", how, len(ob.data.polygons), "faces")


main()
