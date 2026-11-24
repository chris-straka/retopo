# SPDX-License-Identifier: GPL-3.0-or-later
"""Constraint extraction and file serialization (`--guides`, `--features`,
`--density`, `--skeleton`) for RetopoForge."""

from __future__ import annotations

import os
from typing import Any

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
    selected = sorted(e.index for e in mesh.edges if e.select)
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


def _write_chains(obj: Any, chains: list[list[int]], filepath: str, kind_label: str) -> int:
    mesh = obj.data
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(
            f"# RetopoForge {kind_label} from '{obj.name}' "
            f"({len(chains)} polylines)\n"
        )
        for index, chain in enumerate(chains):
            if index:
                f.write("\n")
            for vert in chain:
                co = mesh.vertices[vert].co
                f.write(f"{co.x!r} {co.y!r} {co.z!r}\n")
    return len(chains)


def write_guide_chains(obj: Any, chains: list[list[int]], filepath: str) -> int:
    """Write vertex-index chains as a `--guides` file (local coords, one
    `x y z` per line, blank line between polylines). Returns polyline count."""
    return _write_chains(obj, chains, filepath, "flow guides")


def write_feature_chains(obj: Any, chains: list[list[int]], filepath: str) -> int:
    """Write vertex-index chains as a `--features` file (local coords, one
    `x y z` per line, blank line between polylines). Returns polyline count."""
    return _write_chains(obj, chains, filepath, "sharp features")


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
    params: Any, obj: Any, input_path: str, tmpdir: str, tag: str
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
        chains = trace_edge_chains(obj.data)
        if not chains:
            notes.append(
                f"{obj.name}: guides on but no edge selection, remeshing unconstrained"
            )
        else:
            guides_path = os.path.join(tmpdir, f"guides_{tag}.txt")
            write_guide_chains(obj, chains, guides_path)
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
