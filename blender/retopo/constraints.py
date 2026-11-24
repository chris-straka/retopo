# SPDX-License-Identifier: GPL-3.0-or-later
"""Constraint extraction and file serialization (`--guides`, `--features`,
`--density`, `--skeleton`) for RetopoForge."""

from __future__ import annotations

import math
import os
from typing import Any, Callable, Sequence

try:
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
except ImportError:  # Outside Blender (pure-Python unit tests).
    bpy = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]
    BVHTree = None  # type: ignore[assignment]

Point3 = tuple[float, float, float]

GUIDE_ANNOTATION_LAYER_NAME: str = "RetopoForge Guides"
GUIDE_ANNOTATION_COLOR: tuple[float, float, float] = (0.15, 0.85, 0.95)
GUIDE_ANNOTATION_THICKNESS: int = 3
_MIN_SYMMETRY_SCORE: float = 0.75
_MAX_SYMMETRY_SAMPLES: int = 16384
_MAX_RESAMPLE_POINTS: int = 4096

# Bend range (degrees) per joint role, from humanoid range-of-motion
# defaults. Matched in order on the normalized bone name, so "forearm"
# wins over "arm" and "upleg" over "leg". Unrecognized bones receive
# GENERIC_BEND_DEGREES.
JOINT_BEND_ROLES: tuple[tuple[tuple[str, ...], float], ...] = (
    (("forearm", "lowerarm", "elbow"), 140.0),
    (("upperarm", "upper_arm", "uparm"), 80.0),
    (("thigh", "upleg", "upperleg"), 90.0),
    (("shin", "calf", "lowerleg", "knee"), 140.0),
    (("clavicle", "shoulder", "collar"), 20.0),
    (("finger", "thumb", "index", "middle", "ring", "pinky"), 60.0),
    (("hand", "wrist"), 80.0),
    (("toe",), 30.0),
    (("foot", "ankle"), 40.0),
    (("spine", "chest", "torso", "neck", "head"), 45.0),
    (("arm",), 80.0),
    (("leg",), 140.0),
)
GENERIC_BEND_DEGREES: float = 45.0


def trace_edge_chains(mesh: Any) -> list[list[int]]:
    """Order the mesh's selected edges into polylines for `--guides`.

    Each chain is a list of vertex indices. Chains start at open ends
    (degree 1) first, then leftovers (loops, branches); a branch vertex
    ends every chain passing through it. Iteration is index-sorted, so
    the output is deterministic for a given selection.
    """
    edges = mesh.edges
    if hasattr(edges, "foreach_get"):
        buf = bytearray(len(edges))
        edges.foreach_get("select", buf)
        if not any(buf):
            return []
        selected = [idx for idx, flag in enumerate(buf) if flag]
    else:
        selected = sorted(e.index for e in edges if e.select)
    return _trace_chains(mesh, selected)


def trace_sharp_chains(mesh: Any) -> list[list[int]]:
    """Order the mesh's sharp-marked edges into polylines for `--features`.

    Same deterministic chain tracing as `trace_edge_chains`, but over the
    Edge > Mark Sharp flags instead of the edit-mode selection.
    """
    selected = sorted(e.index for e in mesh.edges if e.use_edge_sharp)
    return _trace_chains(mesh, selected)


def _trace_chains(mesh: Any, selected: list[int]) -> list[list[int]]:
    if not selected:
        return []
    neighbours: dict[int, list[int]] = {}
    for edge_index in selected:
        edge = mesh.edges[edge_index]
        v0, v1 = edge.vertices
        neighbours.setdefault(v0, []).append(v1)
        neighbours.setdefault(v1, []).append(v0)
    for vert in neighbours:
        neighbours[vert].sort()
    used: set[tuple[int, int]] = set()
    chains: list[list[int]] = []

    def walk(start: int) -> list[int]:
        chain = [start]
        vert = start
        while True:
            nxt = None
            for cand in neighbours[vert]:
                key = (min(vert, cand), max(vert, cand))
                if key not in used:
                    nxt = cand
                    break
            if nxt is None:
                return chain
            used.add((min(vert, nxt), max(vert, nxt)))
            chain.append(nxt)
            vert = nxt
            # An open end or a branch stops the chain; a loop closes it
            # by returning to the start vertex (no unused edge remains).
            if len(neighbours[vert]) != 2:
                return chain
            if vert == start:
                return chain

    endpoints = sorted(v for v, vs in neighbours.items() if len(vs) == 1)
    for vert in endpoints:
        if any((min(vert, c), max(vert, c)) not in used for c in neighbours[vert]):
            chains.append(walk(vert))
    for vert in sorted(neighbours):
        if any((min(vert, c), max(vert, c)) not in used for c in neighbours[vert]):
            chains.append(walk(vert))
    return [c for c in chains if len(c) >= 2]


def _write_polylines(
    obj: Any,
    polylines: Sequence[Sequence[Sequence[float]]],
    filepath: str,
    kind_label: str,
) -> int:
    name = getattr(obj, "name", str(obj))
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(
            f"# RetopoForge {kind_label} from '{name}' "
            f"({len(polylines)} polylines)\n"
        )
        for index, poly in enumerate(polylines):
            if index:
                f.write("\n")
            for pt in poly:
                f.write(f"{float(pt[0])!r} {float(pt[1])!r} {float(pt[2])!r}\n")
    return len(polylines)


def _write_chains(obj: Any, chains: list[list[int]], filepath: str, kind_label: str) -> int:
    verts = obj.data.vertices
    polylines = [
        [(verts[vert].co.x, verts[vert].co.y, verts[vert].co.z) for vert in chain]
        for chain in chains
    ]
    return _write_polylines(obj, polylines, filepath, kind_label)


def write_guide_polylines(
    obj: Any, polylines: Sequence[Sequence[Sequence[float]]], filepath: str
) -> int:
    """Write 3D local-space polylines as a `--guides` file.

    Format matches `write_guide_chains`: one `x y z` point per line, blank
    line between polylines. Returns polyline count.
    """
    return _write_polylines(obj, polylines, filepath, "flow guides")


def write_guide_chains(obj: Any, chains: list[list[int]], filepath: str) -> int:
    """Write vertex-index chains as a `--guides` file (local coords, one
    `x y z` per line, blank line between polylines). Returns polyline count."""
    return _write_chains(obj, chains, filepath, "flow guides")


def write_feature_chains(obj: Any, chains: list[list[int]], filepath: str) -> int:
    """Write vertex-index chains as a `--features` file (local coords, one
    `x y z` per line, blank line between polylines). Returns polyline count."""
    return _write_chains(obj, chains, filepath, "sharp features")


def _as_point3(pt: Sequence[float]) -> Point3:
    return (float(pt[0]), float(pt[1]), float(pt[2]))


def _dist3(a: Point3, b: Point3) -> float:
    return math.sqrt(
        (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2
    )


def polyline_length(points: Sequence[Sequence[float]]) -> float:
    """Total Euclidean arc length along `points`."""
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += _dist3(_as_point3(a), _as_point3(b))
    return total


def target_edge_length(area: float, target_quads: int) -> float:
    """Expected local quad edge length from mesh surface area and target quad count."""
    if area <= 0.0:
        return 0.0
    return math.sqrt(float(area) / float(max(1, int(target_quads))))


def resample_polyline(
    points: Sequence[Sequence[float]],
    spacing: float,
    max_points: int = _MAX_RESAMPLE_POINTS,
) -> list[Point3]:
    """Resample a 3D polyline evenly along arc length, preserving endpoints."""
    cleaned: list[Point3] = []
    for raw in points:
        pt = _as_point3(raw)
        if not cleaned or _dist3(cleaned[-1], pt) > 1e-12:
            cleaned.append(pt)
    if len(cleaned) < 2:
        return []
    cum = [0.0]
    for a, b in zip(cleaned, cleaned[1:]):
        cum.append(cum[-1] + _dist3(a, b))
    total = cum[-1]
    if total <= 1e-12:
        return []
    step = max(float(spacing), total / max(1, max_points - 1), 1e-12)
    n_segs = max(1, min(max_points - 1, int(math.ceil(total / step))))
    out: list[Point3] = [cleaned[0]]
    seg = 0
    for k in range(1, n_segs):
        target_s = (total * k) / n_segs
        while seg + 1 < len(cum) - 1 and cum[seg + 1] < target_s:
            seg += 1
        s0, s1 = cum[seg], cum[seg + 1]
        t = (target_s - s0) / (s1 - s0) if s1 > s0 else 0.0
        a, b = cleaned[seg], cleaned[seg + 1]
        out.append(
            (
                a[0] + (b[0] - a[0]) * t,
                a[1] + (b[1] - a[1]) * t,
                a[2] + (b[2] - a[2]) * t,
            )
        )
    out.append(cleaned[-1])
    return out


def _point_segment_distance(p: Point3, a: Point3, b: Point3) -> float:
    vx, vy, vz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    wx, wy, wz = p[0] - a[0], p[1] - a[1], p[2] - a[2]
    seg_len2 = vx * vx + vy * vy + vz * vz
    if seg_len2 <= 1e-24:
        return math.sqrt(wx * wx + wy * wy + wz * wz)
    t = max(0.0, min(1.0, (wx * vx + wy * vy + wz * vz) / seg_len2))
    dx = p[0] - (a[0] + t * vx)
    dy = p[1] - (a[1] + t * vy)
    dz = p[2] - (a[2] + t * vz)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def simplify_polyline(
    points: Sequence[Sequence[float]], epsilon: float
) -> list[Point3]:
    """Simplify a 3D polyline via Ramer-Douglas-Peucker within `epsilon`."""
    pts = [_as_point3(p) for p in points]
    if len(pts) <= 2 or epsilon <= 0.0:
        return pts
    if _dist3(pts[0], pts[-1]) <= 1e-9 and len(pts) >= 4:
        split = max(
            range(1, len(pts) - 1),
            key=lambda i: (
                (pts[i][0] - pts[0][0]) ** 2
                + (pts[i][1] - pts[0][1]) ** 2
                + (pts[i][2] - pts[0][2]) ** 2
            ),
        )
        left = simplify_polyline(pts[: split + 1], epsilon)
        right = simplify_polyline(pts[split:], epsilon)
        return left[:-1] + right
    keep = [False] * len(pts)
    keep[0] = True
    keep[-1] = True
    stack: list[tuple[int, int]] = [(0, len(pts) - 1)]
    while stack:
        start, end = stack.pop()
        if end - start <= 1:
            continue
        a, b = pts[start], pts[end]
        best_idx = -1
        best_dist = -1.0
        for i in range(start + 1, end):
            d = _point_segment_distance(pts[i], a, b)
            if d > best_dist:
                best_dist = d
                best_idx = i
        if best_idx >= 0 and best_dist > epsilon:
            keep[best_idx] = True
            stack.append((start, best_idx))
            stack.append((best_idx, end))
    return [pts[i] for i, flag in enumerate(keep) if flag]


def convert_stroke_points(
    local_points: Sequence[Sequence[float]],
    edge_length: float,
    nearest_fn: Callable[[Point3], tuple[Point3 | None, float]],
    *,
    max_dist_factor: float = 3.0,
    resample_factor: float = 0.5,
    min_length_factor: float = 0.5,
    simplify_factor: float = 0.1,
) -> list[list[Point3]]:
    """Resample, surface-project, split on gaps, drop short stubs, and simplify."""
    if len(local_points) < 2 or edge_length <= 0.0:
        return []
    resampled = resample_polyline(local_points, edge_length * resample_factor)
    if len(resampled) < 2:
        return []
    max_dist = edge_length * max_dist_factor
    min_len = edge_length * min_length_factor
    runs: list[list[Point3]] = []
    current: list[Point3] = []
    for pt in resampled:
        hit, dist = nearest_fn(pt)
        if hit is not None and dist <= max_dist:
            current.append(_as_point3(hit))
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    out: list[list[Point3]] = []
    eps = edge_length * simplify_factor
    for run in runs:
        if len(run) < 2 or polyline_length(run) < min_len:
            continue
        simplified = simplify_polyline(run, eps)
        if len(simplified) >= 2 and polyline_length(simplified) > 1e-9:
            out.append(simplified)
    return out


def detect_symmetry_plane(
    vertices: Sequence[Sequence[float]],
    preferred_axis: int | None = None,
) -> tuple[int, float, float]:
    """Detect dominant mirror plane `(axis, offset, score)` matching `retopo`.

    `preferred_axis` (0=X, 1=Y, 2=Z) pins that axis across its bbox center.
    Otherwise checks `[Y, Z, X]` against `MIN_SYMMETRY_SCORE` (0.75) and falls
    back to X `(0, center_x, score)` when no axis dominates.
    """
    if not vertices:
        axis = preferred_axis if preferred_axis in (0, 1, 2) else 0
        return axis, 0.0, 0.0
    step = max(1, len(vertices) // _MAX_SYMMETRY_SAMPLES)
    sampled = [_as_point3(vertices[i]) for i in range(0, len(vertices), step)]
    xs = [p[0] for p in sampled]
    ys = [p[1] for p in sampled]
    zs = [p[2] for p in sampled]
    lo = (min(xs), min(ys), min(zs))
    hi = (max(xs), max(ys), max(zs))
    diag = _dist3(lo, hi)
    if preferred_axis in (0, 1, 2):
        axis = int(preferred_axis)
        offset = 0.5 * (lo[axis] + hi[axis])
        tol = max(0.01 * diag, 1e-9)
        return axis, offset, _score_plane(sampled, axis, offset, tol)
    tol = max(0.01 * diag, 1e-9)
    best_axis = -1
    best_offset = 0.0
    best_score = 0.0
    for axis in (1, 2, 0):
        offset = 0.5 * (lo[axis] + hi[axis])
        score = _score_plane(sampled, axis, offset, tol)
        if score > best_score:
            best_axis = axis
            best_offset = offset
            best_score = score
    if best_axis >= 0 and best_score >= _MIN_SYMMETRY_SCORE:
        return best_axis, best_offset, best_score
    fallback_offset = 0.5 * (lo[0] + hi[0])
    return 0, fallback_offset, _score_plane(sampled, 0, fallback_offset, tol)


def _score_plane(
    points: Sequence[Point3], axis: int, offset: float, tol: float
) -> float:
    if not points or tol <= 0.0:
        return 0.0
    inv_cell = 1.0 / tol
    tol2 = tol * tol
    cells: dict[tuple[int, int, int], list[Point3]] = {}
    for pt in points:
        key = (
            math.floor(pt[0] * inv_cell),
            math.floor(pt[1] * inv_cell),
            math.floor(pt[2] * inv_cell),
        )
        cells.setdefault(key, []).append(pt)
    hits = 0
    for pt in points:
        m = list(pt)
        m[axis] = 2.0 * offset - pt[axis]
        cx = math.floor(m[0] * inv_cell)
        cy = math.floor(m[1] * inv_cell)
        cz = math.floor(m[2] * inv_cell)
        found = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for cand in cells.get((cx + dx, cy + dy, cz + dz), ()):
                        d2 = (
                            (cand[0] - m[0]) ** 2
                            + (cand[1] - m[1]) ** 2
                            + (cand[2] - m[2]) ** 2
                        )
                        if d2 <= tol2:
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if found:
            hits += 1
    return float(hits) / float(len(points))


def mirror_polyline(
    points: Sequence[Sequence[float]],
    axis: int,
    offset: float,
    plane_tolerance: float = 0.0,
    nearest_fn: Callable[[Point3], tuple[Point3 | None, float]] | None = None,
    max_snap_dist: float = 0.0,
) -> list[Point3] | None:
    """Mirror `points` across `p[axis] == offset`; returns `None` if wholly on-plane."""
    pts = [_as_point3(p) for p in points]
    if len(pts) < 2:
        return None
    if plane_tolerance > 0.0 and all(
        abs(p[axis] - offset) <= plane_tolerance for p in pts
    ):
        return None
    mirrored: list[Point3] = []
    for p in pts:
        q = list(p)
        q[axis] = 2.0 * offset - p[axis]
        qp = (q[0], q[1], q[2])
        if nearest_fn is not None and max_snap_dist > 0.0:
            hit, dist = nearest_fn(qp)
            if hit is not None and dist <= max_snap_dist:
                qp = _as_point3(hit)
        mirrored.append(qp)
    return mirrored


def scene_annotation_data(scene: Any) -> Any:
    """Return the scene's annotation datablock across Blender 4.2 and 5.x."""
    if scene is None:
        return None
    if hasattr(scene, "annotation"):
        return scene.annotation
    return getattr(scene, "grease_pencil", None)


def get_guide_annotation_layer(scene: Any) -> Any:
    """Return the `"RetopoForge Guides"` annotation layer on `scene`, or `None`."""
    ann = scene_annotation_data(scene)
    if ann is None or not hasattr(ann, "layers"):
        return None
    return ann.layers.get(GUIDE_ANNOTATION_LAYER_NAME)


def ensure_guide_annotation_layer(scene: Any) -> Any:
    """Get or create and activate the `"RetopoForge Guides"` annotation layer."""
    if scene is None or bpy is None:
        return None
    if hasattr(scene, "annotation"):
        ann = scene.annotation
        if ann is None:
            ann = bpy.data.annotations.new("Annotations")
            scene.annotation = ann
    else:
        ann = getattr(scene, "grease_pencil", None)
        if ann is None:
            ann = bpy.data.grease_pencils.new("Annotations")
            scene.grease_pencil = ann
    layer = ann.layers.get(GUIDE_ANNOTATION_LAYER_NAME)
    if layer is None:
        layer = ann.layers.new(GUIDE_ANNOTATION_LAYER_NAME, set_active=True)
    else:
        for idx, candidate in enumerate(ann.layers):
            if candidate == layer:
                if hasattr(ann.layers, "active_index"):
                    ann.layers.active_index = idx
                break
        if hasattr(ann.layers, "active"):
            ann.layers.active = layer
    try:
        layer.color = GUIDE_ANNOTATION_COLOR
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        layer.thickness = GUIDE_ANNOTATION_THICKNESS
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        layer.show_in_front = True
    except AttributeError:
        pass
    if not layer.frames:
        layer.frames.new(int(getattr(scene, "frame_current", 1)))
    return layer


def _annotation_frame_for_scene(layer: Any, scene: Any) -> Any:
    if layer is None:
        return None
    active = getattr(layer, "active_frame", None)
    if active is not None:
        return active
    frames = list(getattr(layer, "frames", ()))
    if not frames:
        return None
    cur = int(getattr(scene, "frame_current", 1))
    prior = [f for f in frames if getattr(f, "frame_number", cur) <= cur]
    return prior[-1] if prior else frames[0]


def raw_annotation_strokes(scene: Any) -> list[list[Point3]]:
    """Return raw world-space stroke polylines from `"RetopoForge Guides"`."""
    layer = get_guide_annotation_layer(scene)
    frame = _annotation_frame_for_scene(layer, scene)
    if frame is None:
        return []
    strokes: list[list[Point3]] = []
    for stroke in getattr(frame, "strokes", ()):
        pts = [_as_point3(pt.co) for pt in getattr(stroke, "points", ())]
        if len(pts) >= 2:
            strokes.append(pts)
    return strokes


def clear_guide_annotation_strokes(scene: Any) -> int:
    """Clear all strokes on `"RetopoForge Guides"`, returning strokes removed."""
    layer = get_guide_annotation_layer(scene)
    if layer is None:
        return 0
    removed = 0
    for frame in list(getattr(layer, "frames", ())):
        strokes = getattr(frame, "strokes", None)
        if strokes is None:
            continue
        removed += len(strokes)
        if hasattr(strokes, "clear"):
            strokes.clear()
        else:
            for st in list(strokes):
                strokes.remove(st)
    return removed


def _extract_mesh_for_guides(obj: Any, apply_modifiers: bool) -> tuple[Any, Any | None]:
    """Return `(mesh, eval_obj_to_clear)` in `obj`'s local space."""
    if apply_modifiers and bpy is not None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        return obj_eval.to_mesh(), obj_eval
    return obj.data, None


def extract_annotation_guide_polylines(
    scene: Any, obj: Any, params: Any
) -> list[list[Point3]]:
    """Convert scene `"RetopoForge Guides"` strokes into `obj`-local guide polylines."""
    strokes_world = raw_annotation_strokes(scene)
    if not strokes_world or obj is None or getattr(obj, "type", "") != "MESH":
        return []
    if bpy is None or BVHTree is None or Vector is None:
        return []
    matrix_world = obj.matrix_world
    if abs(matrix_world.determinant()) <= 1e-12:
        return []
    inv_world = matrix_world.inverted()
    apply_mods = bool(getattr(params, "apply_modifiers", False))
    mesh, eval_owner = _extract_mesh_for_guides(obj, apply_mods)
    try:
        if mesh is None or len(mesh.polygons) == 0:
            return []
        total_area = sum(poly.area for poly in mesh.polygons)
        edge_len = target_edge_length(
            total_area, int(getattr(params, "target_quads", 5000))
        )
        if edge_len <= 0.0:
            return []
        verts = [v.co for v in mesh.vertices]
        polys = [tuple(p.vertices) for p in mesh.polygons]
        bvh = BVHTree.FromPolygons(verts, polys)

        def nearest_local(pt: Point3) -> tuple[Point3 | None, float]:
            loc, _norm, _idx, dist = bvh.find_nearest(Vector(pt))
            if loc is None:
                return None, float("inf")
            return (float(loc.x), float(loc.y), float(loc.z)), float(dist)

        converted: list[list[Point3]] = []
        for stroke in strokes_world:
            local_pts = [
                _as_point3(inv_world @ Vector(pt)) for pt in stroke
            ]
            converted.extend(
                convert_stroke_points(local_pts, edge_len, nearest_local)
            )
        if not converted or not bool(getattr(params, "guides_mirror", False)):
            return converted

        preferred_axis = None
        if bool(getattr(params, "symmetry_enabled", False)):
            plane_name = str(getattr(params, "symmetry_plane", "AUTO")).upper()
            preferred_axis = {"X": 0, "Y": 1, "Z": 2}.get(plane_name)
        axis, offset, _ = detect_symmetry_plane(verts, preferred_axis=preferred_axis)
        max_snap = 3.0 * edge_len
        plane_tol = 0.25 * edge_len
        mirrored_all = list(converted)
        for poly in converted:
            mirrored = mirror_polyline(
                poly,
                axis,
                offset,
                plane_tolerance=plane_tol,
                nearest_fn=nearest_local,
                max_snap_dist=max_snap,
            )
            if mirrored is not None:
                mirrored_all.append(mirrored)
        return mirrored_all
    finally:
        if eval_owner is not None:
            eval_owner.to_mesh_clear()


def collect_guide_polylines(
    scene: Any, obj: Any, params: Any | None = None
) -> list[list[Point3]]:
    """Merge Edit-Mode edge selection chains and Object-Mode guide strokes."""
    if obj is None or getattr(obj, "data", None) is None:
        return []
    mesh = obj.data
    edge_chains = trace_edge_chains(mesh)
    polylines: list[list[Point3]] = [
        [_as_point3(mesh.vertices[vi].co) for vi in chain]
        for chain in edge_chains
    ]
    if scene is not None:
        polylines.extend(extract_annotation_guide_polylines(scene, obj, params))
    return polylines


def guide_preview_world_polylines(
    scene: Any, obj: Any, params: Any
) -> list[list[Point3]]:
    """World-space guide polylines matching what `export_guides` / `--guides` sends."""
    local_polys = collect_guide_polylines(scene, obj, params)
    if not local_polys or Vector is None:
        return local_polys
    world = obj.matrix_world
    return [
        [_as_point3(world @ Vector(pt)) for pt in poly]
        for poly in local_polys
    ]


def density_multipliers(obj: Any, group_name: str, lo: float, hi: float) -> list[float]:
    """Per-vertex density multipliers in mesh-vertex order.

    Group weights `0..1` map linearly onto `min..max` (swapped when
    `min > max`). Unassigned vertices stay neutral at `1.0`. Raises
    `RuntimeError` when `group_name` does not exist on `obj`.
    """
    group = obj.vertex_groups.get(group_name)
    if group is None:
        raise RuntimeError(f"Object '{obj.name}' has no vertex group '{group_name}'")
    lo, hi = (lo, hi) if lo <= hi else (hi, lo)
    multipliers: list[float] = []
    for vert in obj.data.vertices:
        try:
            weight = group.weight(vert.index)
        except RuntimeError:
            multipliers.append(1.0)
        else:
            multipliers.append(lo + weight * (hi - lo))
    return multipliers


def write_density_multipliers(
    obj: Any, group_name: str, multipliers: list[float], filepath: str
) -> int:
    """Write multipliers as a `--density` file (one per line, mesh-vertex
    order). Returns the number of entries written."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(
            f"# RetopoForge density mask from '{obj.name}' "
            f"group '{group_name}' ({len(multipliers)} vertices)\n"
        )
        for value in multipliers:
            f.write(f"{value!r}\n")
    return len(multipliers)


def count_obj_vertices(filepath: str) -> int:
    """Count the `'v '` lines in an exported OBJ."""
    count = 0
    with open(filepath, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                count += 1
    return count


def _bone_role_name(name: str) -> str:
    lowered = name.lower()
    for prefix in ("def-", "org-", "mch-", "mixamorig:", "mixamorig_"):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
    return lowered.replace(" ", "")


def joint_bend_degrees(bone_name: str) -> float:
    """Bend range (degrees) of the joint at a bone's head, from its role name."""
    name = _bone_role_name(bone_name)
    for keys, degrees in JOINT_BEND_ROLES:
        if any(key in name for key in keys):
            return degrees
    return GENERIC_BEND_DEGREES


def _is_helper_bone(bone: Any) -> bool:
    """Twist/helper bones ride inside their driver and are skipped."""
    if bone.get("helper_bone") is not None:
        return True
    return "twist" in bone.name.lower()


def skeleton_bones(obj: Any) -> list[tuple[Any, Any, int, float]]:
    """Return `[(local_head, local_tail, parent_index, bend_degrees)]`
    for the deforming bones of `obj`'s armature, parents first, in `obj`'s
    local frame. Returns `[]` if `obj` has no armature.
    """
    arm = obj.find_armature()
    if arm is None:
        return []
    to_local = obj.matrix_world.inverted() @ arm.matrix_world
    kept = [b for b in arm.data.bones if b.use_deform and not _is_helper_bone(b)]
    names = {b.name for b in kept}

    def kept_parent(bone: Any) -> Any:
        p = bone.parent
        while p is not None and p.name not in names:
            p = p.parent
        return p

    order = []
    placed: set[str] = set()
    pending = list(kept)
    while pending:
        rest = []
        for bone in pending:
            parent = kept_parent(bone)
            if parent is None or parent.name in placed:
                order.append(bone)
                placed.add(bone.name)
            else:
                rest.append(bone)
        if len(rest) == len(pending):  # cycle guard
            order.extend(rest)
            break
        pending = rest
    index = {b.name: i for i, b in enumerate(order)}
    bones = []
    for bone in order:
        parent = kept_parent(bone)
        bones.append(
            (
                to_local @ bone.head_local,
                to_local @ bone.tail_local,
                index[parent.name] if parent is not None else -1,
                joint_bend_degrees(bone.name),
            )
        )
    return bones


def write_skeleton_file(
    obj: Any, bones: list[tuple[Any, Any, int, float]], filepath: str
) -> int:
    """Write bones as a `--skeleton` file (`hx hy hz tx ty tz parent bend_degrees`)."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(
            f"# RetopoForge skeleton from the armature of '{obj.name}' "
            f"({len(bones)} bones)\n"
        )
        for head, tail, parent, bend in bones:
            f.write(
                f"{head.x!r} {head.y!r} {head.z!r} "
                f"{tail.x!r} {tail.y!r} {tail.z!r} {parent} {bend!r}\n"
            )
    return len(bones)


def constraint_args_for_target(
    params: Any,
    obj: Any,
    input_path: str,
    tmpdir: str,
    tag: str,
    scene: Any = None,
) -> tuple[list[str], list[str]]:
    """Build `[--guides, --features, --skeleton, --density]` args for one
    remesh target, writing temp constraint files into `tmpdir`.

    Returns `(args, notes)` where `notes` are informational skip reasons.
    Raises `RuntimeError` for invalid configurations (empty density group
    name, or density vertex count mismatch from topology modifiers).
    """
    args: list[str] = []
    notes: list[str] = []
    if params.guides_enabled:
        active_scene = scene
        if active_scene is None and bpy is not None:
            active_scene = getattr(bpy.context, "scene", None)
        polylines = collect_guide_polylines(active_scene, obj, params)
        if not polylines:
            notes.append(
                f"{obj.name}: guides on but no edge selection or guide strokes, remeshing unconstrained"
            )
        else:
            guides_path = os.path.join(tmpdir, f"guides_{tag}.txt")
            write_guide_polylines(obj, polylines, guides_path)
            args += ["--guides", guides_path]
    if params.features_enabled:
        chains = trace_sharp_chains(obj.data)
        if not chains:
            notes.append(
                f"{obj.name}: sharp features on but no marked edges, "
                f"remeshing unconstrained"
            )
        else:
            features_path = os.path.join(tmpdir, f"features_{tag}.txt")
            write_feature_chains(obj, chains, features_path)
            args += ["--features", features_path]
    if params.skeleton_enabled:
        bones = skeleton_bones(obj)
        if not bones:
            notes.append(
                f"{obj.name}: skeleton density on but no armature, remeshing without it"
            )
        else:
            skeleton_path = os.path.join(tmpdir, f"skeleton_{tag}.txt")
            write_skeleton_file(obj, bones, skeleton_path)
            args += ["--skeleton", skeleton_path]
    if params.density_enabled:
        group_name = (params.density_vertex_group or "").strip()
        if not group_name:
            raise RuntimeError(
                "Density mask on but no vertex group set "
                "(pick one in the Density panel)"
            )
        if obj.vertex_groups.get(group_name) is None:
            notes.append(
                f"{obj.name}: vertex group '{group_name}' not found, "
                f"remeshing unconstrained"
            )
            return args, notes
        multipliers = density_multipliers(
            obj, group_name, float(params.density_min), float(params.density_max)
        )
        exported = count_obj_vertices(input_path)
        if exported != len(multipliers):
            raise RuntimeError(
                f"Density mask has {len(multipliers)} entries but the "
                f"exported mesh has {exported} vertices "
                f"(topology-changing modifier? turn off Apply Modifiers "
                f"or apply it first)"
            )
        density_path = os.path.join(tmpdir, f"density_{tag}.txt")
        write_density_multipliers(obj, group_name, multipliers, density_path)
        args += ["--density", density_path]
    return args, notes
