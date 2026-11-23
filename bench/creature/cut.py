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
import array
import json
import math
import os
import struct
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


VALID_MODES = ("quadriflow", "decimate", "decimate-repaired", "artist", "clean-copy")


def parse_args(argv):
    opts = {"repair": False, "holes": None, "report": None}
    holes_explicit = False
    repair_flag = False
    pos = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--repair":
            opts["repair"] = True
            repair_flag = True
            i += 1
        elif tok == "--holes":
            if i + 1 >= len(argv):
                raise SystemExit("cut: --holes requires a numeric share")
            raw_holes = argv[i + 1]
            try:
                opts["holes"] = float(raw_holes)
            except ValueError as err:
                raise SystemExit(f"cut: --holes must be numeric (got {raw_holes!r})") from err
            holes_explicit = True
            i += 2
        elif tok.startswith("--holes="):
            raw_holes = tok.split("=", 1)[1]
            try:
                opts["holes"] = float(raw_holes)
            except ValueError as err:
                raise SystemExit(f"cut: --holes must be numeric (got {raw_holes!r})") from err
            holes_explicit = True
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
    if len(pos) > 4:
        raise SystemExit(f"cut: unexpected extra arguments: {' '.join(pos[4:])}")
    how, src, out, raw_tris = pos[0], pos[1], pos[2], pos[3]
    if how not in VALID_MODES:
        raise SystemExit(
            f"cut: unknown mode {how!r} (expected one of {', '.join(VALID_MODES)})"
        )
    try:
        tris = int(raw_tris)
    except ValueError as err:
        raise SystemExit(
            f"cut: target triangle count must be an integer (got {raw_tris!r})"
        ) from err
    if how != "artist" and tris <= 0:
        raise SystemExit(f"cut: target triangle count must be > 0 (got {tris})")
    if tris < 0:
        raise SystemExit(f"cut: target triangle count cannot be negative (got {tris})")
    if how == "decimate-repaired":
        opts["repair"] = True
    if how not in ("decimate", "decimate-repaired") and (
        repair_flag or holes_explicit or opts["report"] is not None
    ):
        raise SystemExit(
            f"cut: --repair/--holes/--report only apply to decimate / decimate-repaired (got {how!r})"
        )
    if holes_explicit and not opts["repair"]:
        raise SystemExit("cut: --holes requires --repair or decimate-repaired")
    if opts["holes"] is None:
        opts["holes"] = 5.0 if opts["repair"] else 0.0
    if not math.isfinite(opts["holes"]) or opts["holes"] < 0.0:
        raise SystemExit(f"cut: --holes must be finite and >= 0 (got {opts['holes']!r})")
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


def _check_obj_finite(path):
    """Fail cleanly before `bpy.ops.wm.obj_import` silently substitutes 0.0 for NaN/Inf."""
    with open(path, "r", encoding="utf-8", errors="replace") as fp:
        for lineno, line in enumerate(fp, 1):
            if not line.startswith("v "):
                continue
            parts = line.split()
            if len(parts) < 4:
                raise SystemExit(f"cut: malformed vertex on line {lineno} in {path}")
            try:
                vals = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError as err:
                raise SystemExit(f"cut: non-numeric vertex on line {lineno} in {path}") from err
            if not all(math.isfinite(c) for c in vals):
                raise SystemExit(
                    f"cut: non-finite vertex coordinates on line {lineno} in {path}"
                )


def _check_glb_finite(path):
    """Reject corrupt GLB containers or non-finite POSITION floats before glTF import."""
    with open(path, "rb") as fp:
        blob = fp.read()
    if len(blob) < 12 or blob[:4] != b"glTF":
        raise SystemExit(f"cut: invalid GLB header in {path}")
    version, total_len = struct.unpack_from("<II", blob, 4)
    if version != 2 or total_len > len(blob) or len(blob) < 20:
        raise SystemExit(f"cut: truncated or unsupported GLB in {path}")
    off = 12
    json_bytes = None
    bin_bytes = b""
    while off + 8 <= total_len:
        clen, ctype = struct.unpack_from("<II", blob, off)
        off += 8
        if off + clen > total_len:
            raise SystemExit(f"cut: truncated GLB chunk in {path}")
        chunk = blob[off:off + clen]
        off += clen
        if ctype == 0x4E4F534A and json_bytes is None:
            json_bytes = chunk
        elif ctype == 0x004E4942 and bin_bytes == b"":
            bin_bytes = chunk
    if json_bytes is None:
        raise SystemExit(f"cut: missing GLB JSON chunk in {path}")
    try:
        doc = json.loads(json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as err:
        raise SystemExit(f"cut: malformed GLB JSON in {path}: {err}") from err
    if not isinstance(doc, dict):
        raise SystemExit(f"cut: malformed GLB document in {path}")
    buffers = doc.get("buffers") or []
    accessors = doc.get("accessors") or []
    views = doc.get("bufferViews") or []
    seen = set()
    for mesh in doc.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            acc_idx = (prim.get("attributes") or {}).get("POSITION")
            if acc_idx is None or acc_idx in seen:
                continue
            seen.add(acc_idx)
            if not (0 <= acc_idx < len(accessors)):
                raise SystemExit(f"cut: out-of-range POSITION accessor in {path}")
            acc = accessors[acc_idx]
            for bound_key in ("min", "max"):
                vals = acc.get(bound_key)
                if vals and not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals):
                    raise SystemExit(f"cut: non-finite vertex bounds in {path}")
            bv_idx = acc.get("bufferView")
            if bv_idx is None or acc.get("componentType") != 5126:
                continue
            if not (0 <= bv_idx < len(views)):
                raise SystemExit(f"cut: out-of-range POSITION bufferView in {path}")
            bv = views[bv_idx]
            buf_idx = bv.get("buffer", 0)
            if buf_idx != 0 or (buffers and buf_idx < len(buffers) and buffers[buf_idx].get("uri")):
                continue
            count = int(acc.get("count", 0))
            if count <= 0:
                continue
            bv_off = int(bv.get("byteOffset", 0))
            bv_len = int(bv.get("byteLength", 0))
            acc_off = int(acc.get("byteOffset", 0))
            stride = int(bv.get("byteStride") or 12)
            base = bv_off + acc_off
            if stride < 12 or bv_off + bv_len > len(bin_bytes) or base + (count - 1) * stride + 12 > bv_off + bv_len:
                raise SystemExit(f"cut: truncated POSITION buffer in {path}")
            if stride == 12:
                arr = array.array("f")
                arr.frombytes(bin_bytes[base:base + count * 12])
                if sys.byteorder != "little":
                    arr.byteswap()
                if not math.isfinite(sum(arr)):
                    raise SystemExit(f"cut: non-finite vertex coordinates in {path}")
            else:
                for k in range(count):
                    xyz = struct.unpack_from("<fff", bin_bytes, base + k * stride)
                    if not all(math.isfinite(c) for c in xyz):
                        raise SystemExit(f"cut: non-finite vertex coordinates in {path}")


def main():
    if "--" not in sys.argv:
        raise SystemExit("cut: expected -- before arguments")
    a = sys.argv[sys.argv.index("--") + 1:]
    how, src, out, tris, opts = parse_args(a)
    if not os.path.isfile(src):
        raise SystemExit(f"cut: missing input file {src!r}")
    src_ext = os.path.splitext(src)[1].lower()
    if src_ext not in (".obj", ".glb", ".gltf"):
        raise SystemExit(f"cut: unsupported input format {src_ext!r} in {src}")
    if src_ext == ".obj":
        _check_obj_finite(src)
    elif src_ext == ".glb":
        _check_glb_finite(src)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        if src_ext == ".obj":
            res = bpy.ops.wm.obj_import(filepath=src)
        else:
            res = bpy.ops.import_scene.gltf(filepath=src, merge_vertices=True)
    except Exception as err:
        raise SystemExit(f"cut: failed to import {src}: {err}") from err
    if isinstance(res, set) and "CANCELLED" in res:
        raise SystemExit(f"cut: failed to import {src}")
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit(f"cut: no mesh objects found in {src}")
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    if ob is None or len(ob.data.vertices) == 0 or len(ob.data.polygons) == 0:
        raise SystemExit(f"cut: empty mesh in {src}")
    bpy.context.view_layer.update()
    world = ob.matrix_world.copy()  # a node above (a turn, a scale) counts
    ob.parent = None
    ob.matrix_world = world
    ob.modifiers.clear()
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if not all(math.isfinite(c) for v in ob.data.vertices for c in v.co):
        raise SystemExit(f"cut: non-finite vertex coordinates in {src}")
    max_dim_in = max(ob.dimensions)
    if not math.isfinite(max_dim_in) or max_dim_in <= 0.0:
        raise SystemExit(f"cut: degenerate zero-dimension mesh in {src}")
    if not any(math.isfinite(p.area) and p.area > 0.0 for p in ob.data.polygons):
        raise SystemExit(f"cut: all faces have zero area in {src}")

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
        max_dim = max(ob.dimensions)
        if not math.isfinite(max_dim) or max_dim <= 0.0:
            raise SystemExit(f"cut: degenerate zero-dimension mesh in {src}")
        up = 100.0 / max_dim
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
        max_dim = max(ob.dimensions)
        if not math.isfinite(max_dim) or max_dim <= 0.0:
            raise SystemExit(f"cut: degenerate zero-dimension mesh in {src}")
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6 * max_dim)
        # An exporter writes each quad as two triangles one after the other,
        # so the artist's quads are recovered exactly by pairing neighbours
        # in file order that share an edge (and are not folded more than 60
        # degrees); triangles the artist made stay triangles.
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
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, check=False)
            except OSError as err:
                raise SystemExit(f"cut: clean-copy launch failed: {err}") from err
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
    out_dir = os.path.dirname(os.path.abspath(out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
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
        print("CUT_REPAIR", json.dumps(repair_info, sort_keys=True))

    print("CUT", how, len(ob.data.polygons), "faces")


main()
