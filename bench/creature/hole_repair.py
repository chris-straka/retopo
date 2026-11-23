"""4-stage hole-repair bridge for `bench/creature/cut.py` (ported from `motion/tools/tripo_to_glb.py`).

Stages on a Blender mesh object (`bpy` / `bmesh`):
  1. `untangle(obj)`           weld coincident verts (`1e-6 * size`) and remove faces incident
                              to 3+-face non-manifold edges so tangles become plain open rims
  2. `mend_holes(obj, limit)` fill open boundary components `<= limit * size` (`holes_fill`),
                              triangulate new n-gons, copy adjacent UVs/materials, fix normals
  3. `cap_rims(obj, limit)`   decompose remaining rims into edge-disjoint simple cycles (safe on
                              pinched figure-8 boundaries), fan-cap with centroid `mid`, dissolve
                              degenerate slivers (`1e-5 * size`), and re-cap up to 3 passes

Stage 4 on the exported `.obj` file (pure Python, no `bpy` needed):
  4. `close_rims_in_obj(path, limit)`
                              quantize-weld seam vertices (`1e-5 * size`), drop degenerate faces,
                              decompose any remaining open rims into simple cycles, and cap them
                              with outward winding and zero non-manifold chords
"""
import math
import os


def peel_simple_cycles(dir_edges):
    """Decompose directed boundary edges `(u, v, payload)` into edge-disjoint simple cycles.

    Every returned cycle is a list of `(u, v, payload)` edges with no repeated
    vertex inside a single cycle, preventing 4-face non-manifold spokes when two
    holes meet at a pinched figure-8 vertex. Returns `(cycles, open_leftovers)`.
    """
    adj = {}
    for idx, (u, _, _) in enumerate(dir_edges):
        adj.setdefault(u, []).append(idx)

    used = [False] * len(dir_edges)
    cycles = []
    leftovers = []

    for start_idx, (start_u, _, _) in enumerate(dir_edges):
        if used[start_idx]:
            continue
        v_stack = [start_u]
        e_stack = []
        pos = {start_u: 0}
        cursor = {start_u: 0}

        while v_stack:
            cur = v_stack[-1]
            out_list = adj.get(cur, ())
            ci = cursor.get(cur, 0)
            while ci < len(out_list) and used[out_list[ci]]:
                ci += 1
            cursor[cur] = ci
            if ci >= len(out_list):
                v_stack.pop()
                pos.pop(cur, None)
                if e_stack:
                    leftovers.append(e_stack.pop())
                continue

            ei = out_list[ci]
            cursor[cur] = ci + 1
            used[ei] = True
            _, nxt, _ = dir_edges[ei]

            if nxt in pos:
                k = pos[nxt]
                cyc_indices = e_stack[k:] + [ei]
                cycles.append([dir_edges[j] for j in cyc_indices])
                while len(v_stack) > k + 1:
                    removed_v = v_stack.pop()
                    pos.pop(removed_v, None)
                del e_stack[k:]
            else:
                pos[nxt] = len(v_stack)
                v_stack.append(nxt)
                e_stack.append(ei)
                cursor.setdefault(nxt, 0)

    return cycles, leftovers


def _read_obj(path):
    verts = []
    faces = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("v "):
                parts = s.split()
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif s.startswith("f "):
                raw = []
                for tok in s.split()[1:]:
                    idx = int(tok.split("/")[0])
                    raw.append(idx - 1 if idx > 0 else len(verts) + idx)
                if len(raw) == 3:
                    faces.append(tuple(raw))
                elif len(raw) > 3:
                    for i in range(1, len(raw) - 1):
                        faces.append((raw[0], raw[i], raw[i + 1]))
    return verts, faces


def _write_obj(path, verts, faces):
    used = {}
    compact_verts = []
    compact_faces = []
    for a, b, c in faces:
        tri = []
        for idx in (a, b, c):
            nid = used.get(idx)
            if nid is None:
                nid = len(compact_verts)
                used[idx] = nid
                compact_verts.append(verts[idx])
            tri.append(nid)
        if len({tri[0], tri[1], tri[2]}) == 3:
            compact_faces.append(tuple(tri))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for x, y, z in compact_verts:
            f.write(f"v {x:.8f} {y:.8f} {z:.8f}\n")
        for a, b, c in compact_faces:
            f.write(f"f {a + 1} {b + 1} {c + 1}\n")


def _obj_open_edge_components(faces):
    counts = {}
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            k = (u, v) if u < v else (v, u)
            counts[k] = counts.get(k, 0) + 1
    open_edges = [e for e, c in counts.items() if c == 1]
    if not open_edges:
        return 0, 0
    adj = {}
    for a, b in open_edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen = set()
    holes = 0
    for start in adj:
        if start in seen:
            continue
        holes += 1
        stack = [start]
        seen.add(start)
        while stack:
            cur = stack.pop()
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
    return holes, len(open_edges)


def close_rims_in_obj(path, limit=1.0):
    """Stage 4 for `.obj`: weld coincident seam verts (`1e-5 * size`), drop
    degenerate faces, and cap residual simple boundary cycles in `path`.
    Respects `limit` (`<= limit * size` when `< 1.0`, all closed rims when `>= 1.0`).
    """
    if limit <= 0:
        verts, faces = _read_obj(path)
        left_holes, left_edges = _obj_open_edge_components(faces)
        return {"closed": 0, "skipped": left_holes, "left": left_holes, "open_edges_left": left_edges, "welded_verts": 0}

    verts, raw_faces = _read_obj(path)
    if not verts or not raw_faces:
        return {"closed": 0, "skipped": 0, "left": 0, "open_edges_left": 0, "welded_verts": 0}

    lo = [min(v[k] for v in verts) for k in range(3)]
    hi = [max(v[k] for v in verts) for k in range(3)]
    size = max(hi[k] - lo[k] for k in range(3)) or 1.0

    welded_verts = 0
    if limit >= 1.0:
        q = size * 1e-5
        key_of = {}
        wid = []
        wverts = []
        for p in verts:
            k = (round(p[0] / q), round(p[1] / q), round(p[2] / q))
            idx = key_of.get(k)
            if idx is None:
                idx = len(wverts)
                key_of[k] = idx
                wverts.append(p)
            wid.append(idx)
        welded_verts = len(verts) - len(wverts)
    else:
        wverts = list(verts)
        wid = list(range(len(verts)))

    faces = []
    dropped_degen = 0
    for a, b, c in raw_faces:
        wa, wb, wc = wid[a], wid[b], wid[c]
        if wa == wb or wb == wc or wa == wc:
            dropped_degen += 1
            continue
        faces.append((wa, wb, wc))

    # Deduplicate faces collapsed onto the same 3 vertices by welding (opposite-wound
    # zero-volume pairs cancel out; identical duplicates keep one copy).
    if welded_verts > 0:
        by_key = {}
        order = []
        for tri in faces:
            k = tuple(sorted(tri))
            if k not in by_key:
                by_key[k] = []
                order.append(k)
            by_key[k].append(tri)
        deduped = []
        for k in order:
            group = by_key[k]
            if len(group) == 1:
                deduped.append(group[0])
            else:
                # Pair and cancel opposite-orientation duplicates; keep at most one same-orientation copy.
                kept = []
                def _orient(t):
                    m = min(range(3), key=lambda idx: t[idx])
                    return (t[m], t[(m + 1) % 3], t[(m + 2) % 3])
                for t in group:
                    ot = _orient(t)
                    rev = (ot[0], ot[2], ot[1])
                    if rev in kept:
                        kept.remove(rev)
                    elif ot not in kept:
                        kept.append(ot)
                deduped.extend(kept)
        dropped_degen += len(faces) - len(deduped)
        faces = deduped

    edge_count = {}
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            k = (u, v) if u < v else (v, u)
            edge_count[k] = edge_count.get(k, 0) + 1

    dir_edges = []
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            k = (u, v) if u < v else (v, u)
            if edge_count[k] == 1:
                # Cap triangles traverse opposite to neighboring face edge (u -> v), i.e. (v -> u).
                dir_edges.append((v, u, None))

    closed = 0
    skipped = 0
    if dir_edges:
        cycles, leftovers = peel_simple_cycles(dir_edges)
        skipped += len(leftovers)
        for cyc in cycles:
            if len(cyc) < 3:
                skipped += 1
                continue
            perim = sum(math.dist(wverts[u], wverts[v]) for u, v, _ in cyc)
            if limit < 1.0 and perim > limit * size:
                skipped += 1
                continue
            ring = [u for u, _, _ in cyc]
            if len(ring) == 3:
                faces.append((ring[0], ring[1], ring[2]))
            else:
                cx = sum(wverts[idx][0] for idx in ring) / len(ring)
                cy = sum(wverts[idx][1] for idx in ring) / len(ring)
                cz = sum(wverts[idx][2] for idx in ring) / len(ring)
                mid = len(wverts)
                wverts.append((cx, cy, cz))
                for i in range(len(ring)):
                    faces.append((ring[i], ring[(i + 1) % len(ring)], mid))
            closed += 1

    if closed or welded_verts or dropped_degen:
        _write_obj(path, wverts, faces)

    left_holes, left_edges = _obj_open_edge_components(faces)
    return {
        "closed": closed,
        "skipped": skipped,
        "left": left_holes,
        "open_edges_left": left_edges,
        "welded_verts": welded_verts,
        "dropped_degen": dropped_degen,
    }


# ------------------------------------------------------------------ Blender stages 1-3

def boundary_loops(bm):
    """Connected components of open boundary edges (`len(link_faces) == 1`)."""
    open_edges = [e for e in bm.edges if len(e.link_faces) == 1]
    at = {}
    for e in open_edges:
        for v in e.verts:
            at.setdefault(v.index, []).append(e)
    seen, loops = set(), []
    for e in open_edges:
        if e.index in seen:
            continue
        seen.add(e.index)
        stack, loop = [e], []
        while stack:
            a = stack.pop()
            loop.append(a)
            for v in a.verts:
                for b in at[v.index]:
                    if b.index not in seen:
                        seen.add(b.index)
                        stack.append(b)
        loops.append(loop)
    return loops


def untangle(obj):
    """Stage 1: weld micro-doubles (`1e-5 * size`, matching Stage 3/4 seam tolerance)
    and remove faces incident to 3+-face edges."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    size = max(obj.dimensions) or 1.0
    merged = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5 * size)
    merged -= len(bm.verts)

    total_bad = 0
    total_faces = 0
    for _ in range(4):
        bad = [e for e in bm.edges if len(e.link_faces) > 2]
        if not bad:
            break
        total_bad += len(bad)
        faces = list({f for e in bad for f in e.link_faces})
        total_faces += len(faces)
        bmesh.ops.delete(bm, geom=faces, context="FACES_ONLY")
        loose_e = [e for e in bm.edges if not e.link_faces]
        if loose_e:
            bmesh.ops.delete(bm, geom=loose_e, context="EDGES")
        loose_v = [v for v in bm.verts if not v.link_edges]
        if loose_v:
            bmesh.ops.delete(bm, geom=loose_v, context="VERTS")

    still_tangled = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {
        "merged_verts": merged,
        "tangled_edges": total_bad,
        "faces_taken_out": total_faces,
        "still_tangled": still_tangled,
    }


def mend_holes(obj, limit):
    """Stage 2: fill open boundary components `<= limit * size` and keep normals consistent."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    size = max(obj.dimensions) or 1.0
    loops = boundary_loops(bm)
    found = len(loops)
    open_edges = sum(len(lp) for lp in loops)
    small = [lp for lp in loops if sum(e.calc_length() for e in lp) <= limit * size]
    if small and limit > 0:
        uv = bm.loops.layers.uv.active
        old = set(bm.faces)
        bmesh.ops.holes_fill(bm, edges=[e for lp in small for e in lp], sides=0)
        new = [f for f in bm.faces if f not in old]
        if new:
            for f in new:
                f.smooth = True
                if uv:
                    for corner in f.loops:
                        for other in corner.vert.link_loops:
                            if other.face in old:
                                corner[uv].uv = other[uv].uv
                                break
                near = next((e.link_faces[0] for e in f.edges if e.link_faces and e.link_faces[0] in old), None)
                if near:
                    f.material_index = near.material_index
            ngons = [f for f in new if len(f.verts) > 3]
            if ngons:
                tri_res = bmesh.ops.triangulate(bm, faces=ngons)
                for f in tri_res.get("faces", []):
                    f.smooth = True
            bm.faces.ensure_lookup_table()
            recalc = [f for f in bm.faces if f not in old]
            if recalc:
                bmesh.ops.recalc_face_normals(bm, faces=recalc)
    left = boundary_loops(bm)
    filled = max(0, found - len(left))
    out = {
        "found": found,
        "open_edges": open_edges,
        "filled": filled,
        "left": len(left),
        "largest_left": round(max((sum(e.calc_length() for e in lp) for lp in left), default=0.0) / size, 3),
        "edges_with_three_faces": sum(1 for e in bm.edges if len(e.link_faces) > 2),
    }
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return out


def _cap_open_cycles(bm, size, limit):
    """Cap simple boundary cycles up to `limit * size` using per-cycle centroid fans."""
    from mathutils import Vector

    uv = bm.loops.layers.uv.active
    dir_edges = []
    for e in bm.edges:
        if len(e.link_faces) != 1:
            continue
        f = e.link_faces[0]
        corner = next(c for c in f.loops if c.edge == e)
        a_, b_ = corner.vert, corner.link_loop_next.vert
        dir_edges.append((b_, a_, (e, f)))

    if not dir_edges or limit <= 0:
        return 0, 0

    cycles, _ = peel_simple_cycles(dir_edges)
    capped = 0
    for cyc in cycles:
        perim = sum(e.calc_length() for _, _, (e, _) in cyc)
        if perim > limit * size:
            continue
        verts = [u for u, _, _ in cyc]
        if len(verts) == 3:
            e0, f0 = cyc[0][2]
            try:
                nf = bm.faces.new((verts[0], verts[1], verts[2]))
                nf.material_index = f0.material_index
                nf.smooth = True
                if uv:
                    at = {c.vert: c[uv].uv.copy() for _, _, (_, fn) in cyc for c in fn.loops}
                    for c in nf.loops:
                        if c.vert in at:
                            c[uv].uv = at[c.vert]
                capped += 1
            except ValueError:
                pass
            continue

        mid = bm.verts.new(sum((v.co for v in verts), Vector()) / len(verts))
        added_any = False
        for u, v, (e, f) in cyc:
            if len(e.link_faces) != 1:
                continue
            try:
                nf = bm.faces.new((u, v, mid))
            except ValueError:
                continue
            nf.material_index = f.material_index
            nf.smooth = True
            if uv:
                at = {c.vert: c[uv].uv.copy() for c in f.loops}
                for c in nf.loops:
                    if c.vert in at:
                        c[uv].uv = at[c.vert]
                    else:
                        c[uv].uv = (at[u] + at[v]) * 0.5
            added_any = True
        if not added_any:
            bm.verts.remove(mid)
        else:
            capped += 1
    return len(cycles), capped


def cap_rims(obj, limit):
    """Stage 3: cap rims left by `holes_fill`, dissolve degenerate slivers, and re-cap."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    size = max(obj.dimensions) or 1.0
    rims_initial, capped = _cap_open_cycles(bm, size, limit)

    dissolved = 0
    if limit > 0:
        for _ in range(3):
            before = len(bm.faces)
            bmesh.ops.dissolve_degenerate(bm, dist=1e-5 * size, edges=bm.edges[:])
            dissolved += before - len(bm.faces)
            bm.verts.index_update()
            bm.edges.index_update()
            again_count, again_capped = _cap_open_cycles(bm, size, limit)
            capped += again_capped
            if again_count == 0 or again_capped == 0:
                break

    bm.verts.index_update()
    bm.edges.index_update()
    left = boundary_loops(bm)
    degenerate = sum(1 for f in bm.faces if f.calc_area() < (1e-5 * size) ** 2)
    tangled = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {
        "rims": rims_initial,
        "capped": capped,
        "left": len(left),
        "dissolved": dissolved,
        "degenerate_left": degenerate,
        "still_tangled": tangled,
    }
