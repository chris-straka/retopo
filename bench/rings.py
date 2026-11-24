#!/usr/bin/env python3
"""Joint rings: rigforge landmarks -> --guides ring polylines (item 8).

Reads a rigforge landmark JSON (docs: rigforge docs/auto_placement.md,
Phase 1 `tools/detect_landmarks.py`) plus the subject mesh, cuts the mesh
with a plane at each requested joint, and writes the resulting surface
loops as a --guides polyline file (one `x y z` per line, blank line
between rings, `#` comments). Quad edge flow then follows the joint
loops, which is what bends cleanly (see bench/deform.py).

Ring planes:
  biped joints (elbow/wrist/knee/ankle): horizontal plane at the
    `<joint>_z` landmark. Arm slices also cut the torso, so when a slice
    yields 3+ loops the largest (torso) is dropped; 1-2 loops are kept
    whole (legs, neck-level singletons).
  quadruped legs: per-leg plane through the leg's `mid` point,
    perpendicular to its top->foot axis; the loop nearest `mid` is kept
    (one plane per leg, so other legs/body never leak in).

Fails loud (names the joint) when a landmark is missing or a plane cuts
no loop, instead of emitting a file the CLI would reject as empty.

Frames: landmarks must share the mesh file's coordinate frame. The
rigforge detector reports Blender-world landmarks, and Blender's default
OBJ import rotates file axes — so detect + export coherently: either run
detection in-session on the mesh you export with
`obj_export(forward_axis="Y", up_axis="Z")` (file == world, verified),
or transform CLI landmarks by the import's file->world map. Provenance
note: the bench corpus has no rigforge-compatible subject (beast fails
the detector's quadruped signature loud); the end-to-end proof used
rigforge's synthetic quadruped (real detector landmarks, coherent
export): 4 leg rings land exactly on the detected mids, remesh accepts
them, scores hold (non-quads 10->6, dist_max 1.62->1.41, angdev wash).

Usage:
    bench/rings.py --mesh subject.obj --landmarks landmarks.json \
        --joints elbow,knee --output guides.txt
"""

import argparse
import json
import math
import sys

BIPED_JOINTS = ("elbow", "wrist", "knee", "ankle")


def load_mesh(path):
    """OBJ verts + fan-triangulated faces (0-based).

    Negative (relative) face indices resolve against vertices defined so far.
    """
    verts, faces = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            if line.startswith("v "):
                parts = line.split()
                if len(parts) < 4:
                    raise ValueError(
                        f"malformed vertex on {path}:{lineno}"
                    )
                try:
                    coords = tuple(float(t) for t in parts[1:4])
                except ValueError as exc:
                    raise ValueError(
                        f"non-numeric vertex on {path}:{lineno}"
                    ) from exc
                if not all(math.isfinite(c) for c in coords):
                    raise ValueError(
                        f"non-finite vertex on {path}:{lineno}"
                    )
                verts.append(coords)
            elif line.startswith("f "):
                tokens = line.split()[1:]
                if len(tokens) < 3:
                    raise ValueError(
                        f"face with <3 vertices on {path}:{lineno}"
                    )
                idx = []
                for tok in tokens:
                    try:
                        raw = int(tok.split("/")[0])
                    except ValueError as exc:
                        raise ValueError(
                            f"invalid face index {tok!r} on {path}:{lineno}"
                        ) from exc
                    if raw == 0:
                        raise ValueError(
                            f"OBJ vertex index 0 on {path}:{lineno}"
                        )
                    resolved = raw - 1 if raw > 0 else len(verts) + raw
                    if resolved < 0:
                        raise ValueError(
                            f"relative vertex index {raw} out of range on {path}:{lineno}"
                        )
                    idx.append(resolved)
                for k in range(1, len(idx) - 1):
                    faces.append((idx[0], idx[k], idx[k + 1]))
    if not verts or not faces:
        raise ValueError(f"mesh has no geometry: {path}")
    n_verts = len(verts)
    for a, b, c in faces:
        if a >= n_verts or b >= n_verts or c >= n_verts:
            raise ValueError(
                f"vertex index out of range (1..{n_verts}) in {path}"
            )
    return verts, faces


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a):
    return math.sqrt(dot(a, a))


def cut_mesh(verts, faces, point, normal):
    """Plane/mesh intersection segments (exact plane crossing per tri).

    Returns a list of (p, q) endpoint pairs. Deterministic: when a mesh
    vertex lies within 1e-9 of the plane the cut shifts +1e-7 of the bbox
    diagonal along the normal (once; rechecked, then it proceeds — an
    exactly coplanar triangle row after the shift is measure-zero and
    contributes no segment rather than a wrong one).
    """
    if not verts or not faces:
        raise ValueError("rings: mesh has no geometry")
    lo = [min(v[k] for v in verts) for k in range(3)]
    hi = [max(v[k] for v in verts) for k in range(3)]
    diag = math.sqrt(sum((h - l) ** 2 for l, h in zip(lo, hi)))
    d0 = dot(point, normal)
    if min(abs(dot(v, normal) - d0) for v in verts) < 1e-9 * max(diag, 1e-12):
        d0 += 1e-7 * diag
    segs = []
    for a, b, c in faces:
        p = [verts[a], verts[b], verts[c]]
        s = [dot(v, normal) - d0 for v in p]
        pts = []
        for i in range(3):
            j = (i + 1) % 3
            if (s[i] > 0.0) != (s[j] > 0.0):
                t = s[i] / (s[i] - s[j])
                pts.append(tuple(p[i][k] + t * (p[j][k] - p[i][k])
                                 for k in range(3)))
        if len(pts) == 2:
            segs.append((pts[0], pts[1]))
    return segs


def chain_loops(segs, cell):
    """Chain segments into closed loops; drops degenerate scraps.

    Endpoints hash on a `cell` grid (shared-edge crossings agree bitwise
    only when computed in the same edge order, so exact hashing would
    split loops). Each returned loop is closed (first point repeated)
    with 4+ points; loops sort by centroid for determinism.
    """
    key = lambda p: (round(p[0] / cell), round(p[1] / cell), round(p[2] / cell))
    adj = {}
    seen = set()
    for sid, (a, b) in enumerate(segs):
        ka, kb = key(a), key(b)
        if ka == kb:
            continue
        # Shared edges are crossed once per adjacent triangle (opposite
        # endpoint order: last-ulp twins, not bitwise twins); keep the
        # first so parallel edges can't send walks on spur detours.
        ck = (ka, kb) if ka <= kb else (kb, ka)
        if ck in seen:
            continue
        seen.add(ck)
        adj.setdefault(ka, []).append((kb, sid, a, b))
        adj.setdefault(kb, []).append((ka, sid, b, a))
    used = set()
    loops = []
    for start in sorted(adj):
        for (_, sid0, near0, _) in adj[start]:
            if sid0 in used:
                continue
            loop = [near0]
            cur_node, cur_sid = start, sid0
            while True:
                used.add(cur_sid)
                for (nn, s2, _, far_pt) in adj[cur_node]:
                    if s2 == cur_sid:
                        nxt_node = nn
                        break
                if nxt_node == start:
                    loop.append(loop[0])
                    break
                loop.append(far_pt)
                onward = [(nn, s2) for (nn, s2, _, _) in adj[nxt_node]
                          if s2 not in used]
                if not onward:
                    loop = []
                    break
                cur_node, cur_sid = nxt_node, onward[0][1]
            if len(loop) >= 4:
                loops.append(loop)
    loops.sort(key=lambda l: (sum(p[0] for p in l) / len(l),
                              sum(p[1] for p in l) / len(l),
                              sum(p[2] for p in l) / len(l)))
    return loops


def loop_area_xy(loop):
    return abs(sum(a[0] * b[1] - b[0] * a[1]
                   for a, b in zip(loop, loop[1:])) / 2.0)


def rings_for_joint(verts, faces, name, point, normal, drop_largest):
    segs = cut_mesh(verts, faces, point, normal)
    diag = math.sqrt(sum((max(v[k] for v in verts) - min(v[k] for v in verts)) ** 2
                         for k in range(3)))
    loops = chain_loops(segs, 1e-7 * max(diag, 1e-12))
    if not loops:
        raise SystemExit(f"rings: joint '{name}' cuts no loop "
                         f"(point={point} normal={normal})")
    if drop_largest and len(loops) >= 3:
        loops = sorted(loops, key=loop_area_xy)[:-1]
        loops.sort(key=lambda l: (sum(p[0] for p in l) / len(l),
                                  sum(p[1] for p in l) / len(l),
                                  sum(p[2] for p in l) / len(l)))
    return loops


def nearest_loop(loops, point):
    def dist2(loop):
        c = [sum(p[k] for p in loop) / len(loop) for k in range(3)]
        return sum((c[k] - point[k]) ** 2 for k in range(3))
    return min(loops, key=dist2)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--landmarks", required=True)
    ap.add_argument("--joints", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv[1:])

    joints = [j for j in args.joints.split(",") if j]
    if not joints:
        print("rings: --joints must name at least one joint", file=sys.stderr)
        return 2
    try:
        with open(args.landmarks, encoding="utf-8") as lm_f:
            landmarks = json.load(lm_f)
        verts, faces = load_mesh(args.mesh)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"rings: {exc}", file=sys.stderr)
        return 2

    kind = landmarks.get("kind", "biped")
    rings = []  # (label, loop)
    if kind == "biped":
        for joint in joints:
            key = f"{joint}_z"
            if key not in landmarks:
                raise SystemExit(f"rings: landmark '{key}' missing "
                                 f"(biped joints: {','.join(BIPED_JOINTS)})")
            z = landmarks[key]
            loops = rings_for_joint(verts, faces, joint, (0.0, 0.0, z),
                                    (0.0, 0.0, 1.0), drop_largest=True)
            for i, loop in enumerate(loops):
                rings.append((f"{joint}[{i}] z={z}", loop))
    elif kind == "quadruped":
        legs = landmarks.get("legs", {})
        want = joints if joints != ["all"] else sorted(legs)
        for name in want:
            if name not in legs:
                raise SystemExit(f"rings: leg '{name}' missing "
                                 f"(legs: {','.join(sorted(legs))})")
            leg = legs[name]
            top, mid, foot = (tuple(leg[k]) for k in ("top", "mid", "foot"))
            axis = sub(foot, top)
            n = norm(axis)
            if n == 0.0:
                raise SystemExit(f"rings: leg '{name}' has zero top->foot")
            axis = (axis[0] / n, axis[1] / n, axis[2] / n)
            loops = rings_for_joint(verts, faces, name, mid, axis,
                                    drop_largest=False)
            rings.append((f"{name} mid={mid}", nearest_loop(loops, mid)))
    else:
        raise SystemExit(f"rings: unknown kind '{kind}'")
    try:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(f"# joint rings from {args.landmarks} ({kind})\n")
            for label, loop in rings:
                f.write(f"# {label} ({len(loop) - 1} points)\n")
                for p in loop:
                    f.write("%.17g %.17g %.17g\n" % p)
                f.write("\n")
    except OSError as exc:
        print(f"rings: cannot write {args.output}: {exc}", file=sys.stderr)
        return 2
    print(f"rings: {len(rings)} ring(s) -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
