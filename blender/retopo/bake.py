# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake Assist, UV prep, UV projection, and vertex-color transfer operators."""

from __future__ import annotations

import contextlib
import math
import os
import tempfile
from typing import Any, Iterator, Sequence

try:
    import bpy
    from bpy.props import BoolProperty
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    from .props import _recall_paused
except ImportError:  # Outside Blender (pure-Python unit tests).
    bpy = None  # type: ignore[assignment]
    BoolProperty = lambda **_kwargs: None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]
    BVHTree = None  # type: ignore[assignment]
    _recall_paused = None  # type: ignore[assignment]

_OPERATOR_BASE: Any = bpy.types.Operator if bpy is not None else object

# Marks a LOW material the bake created (safe to rewire on every bake);
# a material the user brought is only given image nodes, never rewired.
_BAKE_MATERIAL_TAG = "retopo_bake"
# Data maps store raw values: tagging them Non-Color keeps Blender from
# sRGB-encoding the bake and makes the saved PNGs load back correctly.
_NON_COLOR_MAPS = frozenset({"normal", "roughness", "metallic", "ao"})
# AO is the one Monte Carlo pass here; one sample is pure noise.
_AO_BAKE_SAMPLES = 64
# Default scale-aware bake ray extrusion: 1% of the world bounding-box
# diagonal for cage extrusion (matching project_uv_max_dist / transfer_max_dist),
# and 2.5x that for max_ray_distance so thin adjacent limbs (e.g. fingers near
# legs) do not capture rays from neighbours.
DEFAULT_BAKE_EXTRUSION_SCALE: float = 0.01
DEFAULT_BAKE_RAY_MULTIPLIER: float = 2.5
# Scene settings the bake overrides, restored afterwards (dotted paths
# from the scene).
_BAKE_SCENE_SETTINGS: tuple[str, ...] = (
    "render.engine",
    "cycles.device",
    "cycles.samples",
    "render.bake.use_selected_to_active",
    "render.bake.use_cage",
    "render.bake.cage_object",
    "render.bake.cage_extrusion",
    "render.bake.max_ray_distance",
    "render.bake.margin",
    "render.bake.use_clear",
)
# Blender's default fill color for newly created FLOAT_COLOR CORNER layers.
_DEFAULT_COLOR_FILL: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)


def diagonal_from_bounds(
    bounds_min: Sequence[float], bounds_max: Sequence[float]
) -> float:
    """Return Euclidean diagonal length between min and max 3D bounds."""
    return math.sqrt(
        sum((float(hi) - float(lo)) ** 2 for lo, hi in zip(bounds_min, bounds_max))
    )


def compute_auto_bake_rays(
    world_diagonal: float,
    scale_fraction: float = DEFAULT_BAKE_EXTRUSION_SCALE,
    ray_multiplier: float = DEFAULT_BAKE_RAY_MULTIPLIER,
) -> tuple[float, float]:
    """Compute `(cage_extrusion, max_ray_distance)` proportional to `world_diagonal`."""
    if world_diagonal <= 0.0 or scale_fraction <= 0.0:
        return 0.0, 0.0
    extrusion = float(world_diagonal) * float(scale_fraction)
    max_ray = extrusion * max(0.0, float(ray_multiplier))
    return extrusion, max_ray


def object_world_diagonal(obj: Any, mesh: Any | None = None) -> float:
    """World-space bounding-box diagonal of `obj`, rotation-invariant when `bound_box` exists."""
    world = getattr(obj, "matrix_world", None)
    bb = getattr(obj, "bound_box", None)
    if bb is not None and len(bb) >= 8:
        corners: list[tuple[float, float, float]] = []
        for corner in bb[:8]:
            if world is not None and Vector is not None:
                pt = world @ Vector(corner)
                corners.append((float(pt[0]), float(pt[1]), float(pt[2])))
            else:
                corners.append((float(corner[0]), float(corner[1]), float(corner[2])))
        return max(
            diagonal_from_bounds(corners[a], corners[b])
            for a, b in ((0, 6), (1, 7), (2, 4), (3, 5))
        )
    data = mesh if mesh is not None else getattr(obj, "data", None)
    verts = getattr(data, "vertices", None)
    if not verts:
        return 0.0
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for vert in verts:
        co = world @ vert.co if (world is not None and Vector is not None) else vert.co
        xs.append(float(co[0]))
        ys.append(float(co[1]))
        zs.append(float(co[2]))
    return diagonal_from_bounds((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def resolve_bake_rays_from_diagonal(
    params: Any,
    world_diagonal: float,
    cage_name: str | None = None,
) -> tuple[float, float, str]:
    """Resolve `(cage_extrusion, max_ray_distance, mode_label)` from params and world size."""
    auto = bool(getattr(params, "bake_extrusion_auto", True))
    explicit_extrusion = max(0.0, float(getattr(params, "bake_extrusion", 0.05)))
    explicit_ray = max(0.0, float(getattr(params, "bake_ray_distance", 0.0)))
    if cage_name is not None:
        if auto:
            return 0.0, explicit_ray, f"cage '{cage_name}'"
        return explicit_extrusion, explicit_ray, f"cage '{cage_name}' manual"
    if auto and world_diagonal > 0.0:
        scale_frac = float(
            getattr(params, "bake_extrusion_scale", DEFAULT_BAKE_EXTRUSION_SCALE)
        )
        extrusion, auto_ray = compute_auto_bake_rays(world_diagonal, scale_frac)
        max_ray = explicit_ray if explicit_ray > 0.0 else auto_ray
        return (
            extrusion,
            max_ray,
            f"auto ({scale_frac * 100.0:.3g}% of {world_diagonal:.4g})",
        )
    return explicit_extrusion, explicit_ray, "manual"


def resolve_bake_ray_settings(
    params: Any,
    high_eval: Any,
    low: Any,
    cage: Any | None = None,
    high_mesh: Any | None = None,
) -> tuple[float, float, str]:
    """Resolve Cycles `(cage_extrusion, max_ray_distance, mode_label)` for HIGH/LOW objects."""
    high_diag = object_world_diagonal(high_eval, high_mesh)
    low_diag = object_world_diagonal(low, getattr(low, "data", None))
    diag = low_diag if low_diag > 0.0 else high_diag
    cage_name = getattr(cage, "name", None) if cage is not None else None
    return resolve_bake_rays_from_diagonal(params, diag, cage_name=cage_name)


def _uses_nodes(mat: Any) -> bool:
    if bpy.app.version < (5, 0, 0):
        return bool(mat.use_nodes)
    return mat.node_tree is not None


def _ensure_node_tree(mat: Any) -> Any:
    if bpy.app.version < (5, 0, 0):
        if not mat.use_nodes:
            mat.use_nodes = True
    elif mat.node_tree is None and hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    return mat.node_tree


def _ensure_object_mode() -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _snapshot_settings(scene: Any, paths: tuple[str, ...]) -> list[tuple[Any, str, Any]]:
    saved = []
    for path in paths:
        owner_path, _, attr = path.rpartition(".")
        owner = scene
        for part in owner_path.split("."):
            owner = getattr(owner, part)
        saved.append((owner, attr, getattr(owner, attr)))
    return saved


def _restore_settings(saved: list[tuple[Any, str, Any]]) -> None:
    for owner, attr, value in saved:
        try:
            setattr(owner, attr, value)
        except (AttributeError, TypeError, ValueError):
            pass


def _bake_image(name: str, size: int, filepath: str, non_color: bool) -> Any:
    """Create or reset the image datablock a map bakes into."""
    image = bpy.data.images.get(name)
    if image is None:
        image = bpy.data.images.new(name, size, size, alpha=True)
    else:
        # Back to a blank generated buffer at the requested size: the
        # last bake's PNG may since have been moved or deleted, and a
        # FILE image would then fail to load its buffer.
        image.source = "GENERATED"
        image.generated_type = "BLANK"
        image.generated_width = size
        image.generated_height = size
    image.file_format = "PNG"
    image.filepath_raw = filepath
    try:
        image.colorspace_settings.name = "Non-Color" if non_color else "sRGB"
    except TypeError:
        pass
    return image


def _new_bake_material(obj: Any) -> Any:
    mat = bpy.data.materials.new(name=f"{obj.name}_bake")
    _ensure_node_tree(mat)
    mat[_BAKE_MATERIAL_TAG] = True
    return mat


def _prepare_bake_materials(
    low: Any,
) -> tuple[list[Any], list[tuple[Any, bool | None, int | None, dict[int, bool]]]]:
    """Ensure every material slot on `low` has a node-tree material.

    Creates a tagged placeholder material if `low` has no material slots or
    any empty (`None`) slots, and snapshots every touched material's
    pre-bake node active/select state for restoration on exit or error.
    """
    mats = low.data.materials
    placeholder = None
    if len(mats) == 0:
        placeholder = _new_bake_material(low)
        mats.append(placeholder)
    else:
        for idx, slot in enumerate(list(mats)):
            if slot is None:
                if placeholder is None:
                    placeholder = _new_bake_material(low)
                mats[idx] = placeholder

    unique_mats: list[Any] = []
    seen_ptrs: set[int] = set()
    snapshots: list[tuple[Any, bool | None, int | None, dict[int, bool]]] = []
    for mat in mats:
        ptr = mat.as_pointer()
        if ptr in seen_ptrs:
            continue
        seen_ptrs.add(ptr)
        unique_mats.append(mat)
        prev_use_nodes = (
            bool(mat.use_nodes) if bpy.app.version < (5, 0, 0) else None
        )
        tree = _ensure_node_tree(mat)
        prev_active_ptr = (
            tree.nodes.active.as_pointer()
            if tree is not None and tree.nodes.active is not None
            else None
        )
        prev_selected = (
            {n.as_pointer(): bool(n.select) for n in tree.nodes}
            if tree is not None
            else {}
        )
        snapshots.append((mat, prev_use_nodes, prev_active_ptr, prev_selected))
    return unique_mats, snapshots


def _restore_bake_materials(
    snapshots: list[tuple[Any, bool | None, int | None, dict[int, bool]]],
    map_nodes_by_mat: dict[int, dict[str, Any]],
    bake_ok: bool,
) -> None:
    """Wire bake-created materials on success; restore user node state cleanly."""
    for mat, prev_use_nodes, prev_active_ptr, prev_selected in snapshots:
        tree = mat.node_tree
        if tree is None:
            continue
        nodes = tree.nodes
        if bake_ok and mat.get(_BAKE_MATERIAL_TAG):
            mat_nodes = map_nodes_by_mat.get(mat.as_pointer(), {})
            _wire_baked_maps(mat, mat_nodes)
            diffuse = mat_nodes.get("diffuse")
            if diffuse is not None:
                _activate_bake_node(mat, diffuse)
            continue

        restored_active = None
        for node in nodes:
            ptr = node.as_pointer()
            node.select = prev_selected.get(ptr, False)
            if prev_active_ptr is not None and ptr == prev_active_ptr:
                restored_active = node
        nodes.active = restored_active
        if bpy.app.version < (5, 0, 0) and prev_use_nodes is False:
            mat.use_nodes = False


def _bake_node(mat: Any, name: str, image: Any) -> Any:
    """Create or reuse the named Image Texture node holding one baked map."""
    tree = _ensure_node_tree(mat)
    nodes = tree.nodes
    node_name = f"RetopoForge {name}"
    node = nodes.get(node_name)
    if node is None or node.type != "TEX_IMAGE":
        node = nodes.new("ShaderNodeTexImage")
        node.name = node_name
        node.label = node_name
    node.image = image
    return node


def _activate_bake_node(mat: Any, node: Any) -> None:
    """Make `node` the sole selected and active Image Texture node on `mat`."""
    tree = mat.node_tree
    for other in tree.nodes:
        other.select = False
    node.select = True
    tree.nodes.active = node


def _wire_baked_maps(mat: Any, map_nodes: dict[str, Any]) -> None:
    """Hook baked maps into a bake-created material's Principled BSDF."""
    if not mat.get(_BAKE_MATERIAL_TAG):
        return
    tree = mat.node_tree
    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return

    def feed(socket_name: str, output: Any) -> None:
        socket = bsdf.inputs.get(socket_name)
        if socket is None:
            return
        for link in list(socket.links):
            tree.links.remove(link)
        tree.links.new(output, socket)

    order = ("diffuse", "roughness", "metallic", "normal", "emission", "ao")
    for row, name in enumerate(n for n in order if n in map_nodes):
        map_nodes[name].location = (
            bsdf.location.x - 600,
            bsdf.location.y - 280 * row,
        )
    for name, socket_name in (
        ("diffuse", "Base Color"),
        ("roughness", "Roughness"),
        ("metallic", "Metallic"),
        ("emission", "Emission Color"),
    ):
        if name in map_nodes:
            feed(socket_name, map_nodes[name].outputs["Color"])
    if "emission" in map_nodes:
        strength = bsdf.inputs.get("Emission Strength")
        if strength is not None and not strength.is_linked:
            strength.default_value = 1.0
    if "normal" in map_nodes:
        normal_map = tree.nodes.get("RetopoForge normal map")
        if normal_map is None or normal_map.type != "NORMAL_MAP":
            normal_map = tree.nodes.new("ShaderNodeNormalMap")
            normal_map.name = "RetopoForge normal map"
            normal_map.label = normal_map.name
        normal_map.location = (
            bsdf.location.x - 250,
            map_nodes["normal"].location.y,
        )
        for link in list(normal_map.inputs["Color"].links):
            tree.links.remove(link)
        tree.links.new(
            map_nodes["normal"].outputs["Color"],
            normal_map.inputs["Color"],
        )
        feed("Normal", normal_map.outputs["Normal"])


def _ensure_high_material(obj: Any) -> None:
    """Give a material-less HIGH object a default Principled material."""
    if len(obj.data.materials):
        return
    mat = bpy.data.materials.new(name=f"{obj.name}_high")
    _ensure_node_tree(mat)
    obj.data.materials.append(mat)


def _principled_nodes(obj: Any) -> Iterator[tuple[Any, Any]]:
    """Yield `(material, Principled BSDF node)` over `obj`'s node materials."""
    for mat in obj.data.materials:
        if mat is None or not _uses_nodes(mat) or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                yield mat, node
                break


def _socket_used(obj: Any, socket: str, default: float) -> bool:
    """True when any Principled `socket` on `obj` is linked or non-default."""
    for _, bsdf in _principled_nodes(obj):
        inp = bsdf.inputs.get(socket)
        if inp is None:
            continue
        if inp.is_linked:
            return True
        val = inp.default_value
        if isinstance(val, float):
            if abs(val - default) > 1e-9:
                return True
        else:
            try:
                if any(abs(c - d) > 1e-9 for c, d in zip(val, default)):
                    return True
            except TypeError:
                pass
    return False


def _emission_used(obj: Any) -> bool:
    """True when any Principled BSDF on `obj` emits non-black light."""
    for _, bsdf in _principled_nodes(obj):
        color = bsdf.inputs.get("Emission Color")
        strength = bsdf.inputs.get("Emission Strength")
        if color is None or strength is None:
            continue
        sval = strength.default_value
        if strength.is_linked or sval > 1e-9:
            if color.is_linked:
                return True
            if any(c > 1e-9 for c in color.default_value[:3]):
                return True
    return False


def _metallic_bake_source(context: Any, high: Any) -> Any:
    """Duplicate `high` with materials rewired `Metallic -> Emission`.

    Blender has no metallic bake type; an EMIT bake of the duplicate
    transfers the metallic map. Returns `None` when no Principled metallic
    input is active. Caller must clean up via `_delete_bake_source`.
    """
    if not _socket_used(high, "Metallic", 0.0):
        return None
    dup = high.copy()
    dup.data = high.data.copy()
    dup.name = f"{high.name}_metallic_src"
    context.collection.objects.link(dup)
    for i, slot in enumerate(list(dup.data.materials)):
        if slot is None or not _uses_nodes(slot) or slot.node_tree is None:
            continue
        mat = slot.copy()
        dup.data.materials[i] = mat
        bsdf = next(
            (n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"),
            None,
        )
        out = next(
            (n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL"),
            None,
        )
        if bsdf is None or out is None:
            continue
        metallic = bsdf.inputs.get("Metallic")
        if metallic is None:
            continue
        emission = mat.node_tree.nodes.new("ShaderNodeEmission")
        emission.inputs["Strength"].default_value = 1.0
        if metallic.is_linked:
            src = metallic.links[0].from_socket
            mat.node_tree.links.new(src, emission.inputs["Color"])
        else:
            v = float(metallic.default_value)
            emission.inputs["Color"].default_value = (v, v, v, 1.0)
        for link in list(out.inputs["Surface"].links):
            mat.node_tree.links.remove(link)
        mat.node_tree.links.new(
            emission.outputs["Emission"], out.inputs["Surface"]
        )
    return dup


def _delete_bake_source(dup: Any) -> None:
    if dup is None:
        return
    mesh = dup.data
    mats = [s for s in mesh.materials if s is not None]
    bpy.data.objects.remove(dup, do_unlink=True)
    bpy.data.meshes.remove(mesh, do_unlink=True)
    for mat in mats:
        if mat.users == 0:
            bpy.data.materials.remove(mat)


def _select_single_for_uv_edit(context: Any, obj: Any) -> None:
    _ensure_object_mode()
    context.view_layer.update()
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")


def _smart_uv_low(context: Any, low: Any) -> None:
    _select_single_for_uv_edit(context, low)
    bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


def _uv_world_areas(obj: Any) -> tuple[float, float]:
    """Return `(total_uv_area, total_world_area)` over `obj`'s active UV layer."""
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return 0.0, 0.0
    world = obj.matrix_world.to_3x3()
    uv_area = 0.0
    world_area = 0.0
    for poly in mesh.polygons:
        loop_uvs = [uv_layer.uv[li].vector for li in poly.loop_indices]
        for a, b, c in zip(loop_uvs, loop_uvs[1:], loop_uvs[2:]):
            uv_area += abs((b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y)) / 2.0
        verts = [world @ mesh.vertices[vi].co for vi in poly.vertices]
        origin = verts[0]
        for b, c in zip(verts[1:], verts[2:]):
            world_area += ((b - origin).cross(c - origin)).length / 2.0
    return uv_area, world_area


def _scale_uvs_about_center(obj: Any, scale: float) -> None:
    uv_layer = obj.data.uv_layers.active
    if uv_layer is None:
        return
    for item in uv_layer.uv:
        x, y = item.vector
        item.vector = (0.5 + (x - 0.5) * scale, 0.5 + (y - 0.5) * scale)


def _uvs_fit_tile(obj: Any) -> bool:
    uv_layer = obj.data.uv_layers.active
    if uv_layer is None:
        return False
    return all(-1e-6 <= c <= 1.0 + 1e-6 for item in uv_layer.uv for c in item.vector)


def _unwrap_pack_low(context: Any, low: Any, params: Any) -> str:
    """Unwrap + uniform-density + pack pipeline for the LOW bake target."""
    _select_single_for_uv_edit(context, low)
    bpy.ops.uv.unwrap(
        method=params.bake_unwrap_method,
        fill_holes=True,
        correct_aspect=True,
        margin=float(params.bake_pack_margin),
    )
    if params.bake_average_scale:
        bpy.ops.uv.average_islands_scale()
    bpy.ops.uv.pack_islands(margin=float(params.bake_pack_margin))
    bpy.ops.object.mode_set(mode="OBJECT")
    size = int(params.bake_size)
    uv_area, world_area = _uv_world_areas(low)
    if uv_area <= 0.0 or world_area <= 0.0:
        return "uv: unwrap+pack (degenerate UVs, density unmeasured)"
    measured = size * (uv_area / world_area) ** 0.5
    target = float(params.bake_texel_density)
    if target > 0.0:
        _scale_uvs_about_center(low, target / measured)
        if _uvs_fit_tile(low):
            return (
                f"uv: unwrap+pack @ {target:.0f}px/unit "
                f"(target met, pack fit {measured:.0f})"
            )
        _scale_uvs_about_center(low, measured / target)
        return (
            f"uv: unwrap+pack @ {measured:.0f}px/unit "
            f"(target {target:.0f} exceeds the tile fit)"
        )
    return f"uv: unwrap+pack @ {measured:.0f}px/unit"


@contextlib.contextmanager
def _evaluated_high_mesh(context: Any, high: Any) -> Iterator[tuple[Any, Any]]:
    """Yield `(high_eval, eval_mesh)` for `high` with active modifiers applied."""
    if high.data is not None:
        high.data.update_tag()
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    high_eval = high.evaluated_get(depsgraph)
    eval_mesh = high_eval.to_mesh()
    try:
        yield high_eval, eval_mesh
    finally:
        high_eval.to_mesh_clear()


class RETOPOFORGE_OT_bake_textures(_OPERATOR_BASE):
    """Bake PBR maps from the HIGH-poly active object to the selected
    LOW-poly target (UV prep per panel, Cycles CPU, selected-to-active)"""

    bl_idname = "retopo.bake_textures"
    bl_label = "Bake High to Low"
    bl_options = {"REGISTER", "UNDO"}

    # Remesh + Bake All bakes onto a LOW that did not exist a moment ago,
    # so no cage can match it yet: the chained call bakes without one.
    ignore_cage: BoolProperty(
        name="Ignore Cage",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    def execute(self, context: Any) -> set[str]:
        with _recall_paused(context):
            return self._execute(context)

    def _execute(self, context: Any) -> set[str]:
        params = context.scene.retopo_params
        high = context.view_layer.objects.active
        if high is None or high.type != "MESH":
            self.report({"ERROR"}, "Make the HIGH-poly source the active object")
            return {"CANCELLED"}
        lows = [
            o for o in context.selected_objects if o.type == "MESH" and o != high
        ]
        if not lows:
            self.report(
                {"ERROR"},
                "Select the LOW-poly target (active object is the HIGH source)",
            )
            return {"CANCELLED"}
        low = lows[0]
        if len(lows) > 1:
            self.report({"INFO"}, f"Multiple LOW candidates; baking to '{low.name}'")

        with _evaluated_high_mesh(context, high) as (high_eval, high_eval_mesh):
            high_polys = len(high_eval_mesh.polygons)
            high_diag = object_world_diagonal(high_eval, high_eval_mesh)
        if high_polys == 0:
            self.report(
                {"ERROR"},
                f"HIGH '{high.name}' has no polygons to bake from",
            )
            return {"CANCELLED"}
        if len(low.data.polygons) == 0:
            self.report(
                {"ERROR"},
                f"LOW '{low.name}' has no polygons to bake onto",
            )
            return {"CANCELLED"}

        cage = None if self.ignore_cage else params.bake_cage
        if cage is not None:
            if cage.type != "MESH":
                self.report(
                    {"ERROR"}, f"Bake cage '{cage.name}' is not a mesh object"
                )
                return {"CANCELLED"}
            if len(cage.data.polygons) != len(low.data.polygons):
                self.report(
                    {"ERROR"},
                    f"Bake cage '{cage.name}' has "
                    f"{len(cage.data.polygons)} faces but '{low.name}' has "
                    f"{len(low.data.polygons)} (duplicate LOW and inflate it)",
                )
                return {"CANCELLED"}

        low_diag = object_world_diagonal(low, low.data)
        cage_extrusion, max_ray_distance, ray_mode = resolve_bake_rays_from_diagonal(
            params,
            low_diag if low_diag > 0.0 else high_diag,
            cage_name=cage.name if cage is not None else None,
        )

        uv_note = None
        if params.bake_uv_mode == "UNWRAP":
            uv_note = _unwrap_pack_low(context, low, params)
        else:
            _smart_uv_low(context, low)
        _ensure_high_material(high)

        metallic_src = None
        if params.bake_metallic:
            metallic_src = _metallic_bake_source(context, high)
        jobs = [
            ("diffuse", "DIFFUSE", {"COLOR"}, True, True),
            ("normal", "NORMAL", set(), params.bake_normal, True),
            (
                "roughness",
                "ROUGHNESS",
                set(),
                params.bake_roughness,
                _socket_used(high, "Roughness", 0.5),
            ),
            (
                "metallic",
                "EMIT",
                set(),
                params.bake_metallic,
                metallic_src is not None,
            ),
            ("ao", "AO", set(), params.bake_ao, True),
            (
                "emission",
                "EMIT",
                set(),
                params.bake_emission,
                _emission_used(high),
            ),
        ]

        outdir = os.path.dirname(bpy.data.filepath) or tempfile.gettempdir()
        size = int(params.bake_size)
        unique_mats, mat_snapshots = _prepare_bake_materials(low)
        images_by_map: dict[str, Any] = {}
        map_nodes_by_mat: dict[int, dict[str, Any]] = {
            m.as_pointer(): {} for m in unique_mats
        }
        for name, _, _, enabled, used in jobs:
            if not (enabled and used):
                continue
            image = _bake_image(
                f"{low.name}_{name}",
                size,
                os.path.join(outdir, f"{low.name}_{name}.png"),
                name in _NON_COLOR_MAPS,
            )
            images_by_map[name] = image
            for mat in unique_mats:
                map_nodes_by_mat[mat.as_pointer()][name] = _bake_node(
                    mat, name, image
                )

        scene = context.scene
        saved = _snapshot_settings(scene, _BAKE_SCENE_SETTINGS)
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        bake = scene.render.bake
        bake.use_selected_to_active = True
        bake.use_cage = cage is not None
        bake.cage_object = cage
        bake.cage_extrusion = cage_extrusion
        bake.max_ray_distance = max_ray_distance
        bake.margin = int(params.bake_margin)
        bake.use_clear = True

        lines: list[str] = [
            f"{low.name}: rays: {ray_mode} "
            f"(extrusion={cage_extrusion:.4g}, max_ray={max_ray_distance:.4g})"
        ]
        bake_ok = False
        try:
            for name, btype, filt, enabled, used in jobs:
                if not enabled:
                    continue
                if not used:
                    lines.append(f"{low.name}: {name} skipped (HIGH has no {name})")
                    continue
                src = metallic_src if name == "metallic" else high
                for o in context.selected_objects:
                    o.select_set(False)
                src.select_set(True)
                low.select_set(True)
                context.view_layer.objects.active = low
                context.view_layer.update()
                scene.cycles.samples = _AO_BAKE_SAMPLES if btype == "AO" else 1
                for mat in unique_mats:
                    _activate_bake_node(mat, map_nodes_by_mat[mat.as_pointer()][name])
                if filt:
                    bpy.ops.object.bake(type=btype, pass_filter=filt, use_clear=True)
                else:
                    bpy.ops.object.bake(type=btype, use_clear=True)
                image = images_by_map[name]
                image.save()
                lines.append(f"{low.name}: {name} -> {image.filepath_raw}")
            bake_ok = True
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Bake failed: {exc}")
            return {"CANCELLED"}
        finally:
            _restore_bake_materials(mat_snapshots, map_nodes_by_mat, bake_ok)
            _delete_bake_source(metallic_src)
            for o in context.selected_objects:
                o.select_set(False)
            high.select_set(True)
            low.select_set(True)
            context.view_layer.objects.active = high
            _restore_settings(saved)

        if uv_note is not None:
            lines.insert(0, f"{low.name}: {uv_note}")
        line = "\n".join(lines)
        scene.retopo_last_report += line + "\n"
        self.report({"INFO"}, line.replace("\n", " | "))
        return {"FINISHED"}


def _bary_weights_3d(p: Vector, a: Vector, b: Vector, c: Vector) -> tuple[float, float, float]:
    """Barycentric weights of `p` in triangle `abc` (3D, area-based)."""
    ab = b - a
    ac = c - a
    ap = p - a
    d1 = ab.dot(ap)
    d2 = ac.dot(ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return (1.0, 0.0, 0.0)
    bp = p - b
    d3 = ab.dot(bp)
    d4 = ac.dot(bp)
    if d3 >= 0.0 and d4 <= d3:
        return (0.0, 1.0, 0.0)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        return (1.0 - v, v, 0.0)
    cp = p - c
    d5 = ab.dot(cp)
    d6 = ac.dot(cp)
    if d6 >= 0.0 and d5 <= d6:
        return (0.0, 0.0, 1.0)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        return (1.0 - w, 0.0, w)
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return (0.0, 1.0 - w, w)
    denom = 1.0 / (va + vb + vc)
    v = vb * denom
    w = vc * denom
    return (1.0 - v - w, v, w)


def _uv_islands(mesh: Any, uv_layer: Any) -> list[int]:
    """Per-polygon UV island ids for `mesh`."""
    parent = list(range(len(mesh.polygons)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    uvs = uv_layer.uv
    first_owner: dict[tuple[tuple[int, int], tuple[tuple[float, ...], tuple[float, ...]]], tuple[int, Any]] = {}
    for poly in mesh.polygons:
        verts = poly.vertices
        loops = poly.loop_indices
        n = len(verts)
        for k in range(n):
            va, vb = verts[k], verts[(k + 1) % n]
            ua = tuple(uvs[loops[k]].vector)
            ub = tuple(uvs[loops[(k + 1) % n]].vector)
            key, uv_key = (
                ((va, vb), (ua, ub)) if va < vb else ((vb, va), (ub, ua))
            )
            owner = first_owner.get(key)
            if owner is None:
                first_owner[key] = (poly.index, uv_key)
            elif owner[1] == uv_key:
                parent[find(owner[0])] = find(poly.index)
    return [find(i) for i in range(len(parent))]


def _face_correspondence(
    high_eval: Any,
    high_mesh: Any,
    low: Any,
    max_dist_frac: float,
    island_of: list[int] | None = None,
) -> tuple[list[tuple[Any, list[Any]]], int, int]:
    """Nearest-point mapping from LOW corners onto the evaluated HIGH surface."""
    high_world = high_eval.matrix_world
    hverts = [high_world @ v.co for v in high_mesh.vertices]
    hpolys = high_mesh.polygons
    tree = BVHTree.FromPolygons(hverts, [tuple(p.vertices) for p in hpolys])
    if len(high_eval.bound_box) >= 7:
        diag = (
            high_world @ Vector(high_eval.bound_box[6])
            - high_world @ Vector(high_eval.bound_box[0])
        ).length
    else:
        diag = 0.0
    max_dist = float(max_dist_frac) * diag

    island_members: dict[int, list[int]] = {}
    if island_of is not None:
        for index, island in enumerate(island_of):
            island_members.setdefault(island, []).append(index)
    island_trees: dict[int, tuple[BVHTree, list[int]]] = {}

    def nearest_in_island(island: int, point: Vector) -> tuple[Vector, int]:
        entry = island_trees.get(island)
        if entry is None:
            members = island_members[island]
            entry = (
                BVHTree.FromPolygons(
                    hverts, [tuple(hpolys[i].vertices) for i in members]
                ),
                members,
            )
            island_trees[island] = entry
        location, _, local, _ = entry[0].find_nearest(point)
        return location, entry[1][local]

    def corner_on(face_idx: int, point: Vector) -> tuple[tuple[int, ...], tuple[int, ...], tuple[float, float, float]]:
        hpoly = hpolys[face_idx]
        pverts = hpoly.vertices
        ploops = hpoly.loop_indices
        best = None
        best_d2 = float("inf")
        for k in range(1, len(pverts) - 1):
            tri = (0, k, k + 1)
            a, b, c = (hverts[pverts[t]] for t in tri)
            w = _bary_weights_3d(point, a, b, c)
            d2 = (point - (a * w[0] + b * w[1] + c * w[2])).length_squared
            if d2 < best_d2:
                best_d2 = d2
                best = (
                    tuple(ploops[t] for t in tri),
                    tuple(pverts[t] for t in tri),
                    w,
                )
        if best is None:
            return (
                (ploops[0], ploops[0], ploops[0]),
                (pverts[0], pverts[0], pverts[0]),
                (1.0, 0.0, 0.0),
            )
        return best

    low_world = low.matrix_world
    low_mesh = low.data
    items: list[tuple[Any, list[Any]]] = []
    projected = 0
    skipped = 0
    for poly in low_mesh.polygons:
        _, _, anchor, dist = tree.find_nearest(low_world @ poly.center)
        if anchor is None or dist > max_dist:
            skipped += 1
            continue
        corners = []
        for li in poly.loop_indices:
            point = low_world @ low_mesh.vertices[low_mesh.loops[li].vertex_index].co
            location, _, face_idx, _ = tree.find_nearest(point)
            if island_of is not None and island_of[face_idx] != island_of[anchor]:
                location, face_idx = nearest_in_island(island_of[anchor], point)
            corners.append((li,) + corner_on(face_idx, location))
        items.append((poly, corners))
        projected += 1
    return items, projected, skipped


def _get_or_reuse_color_attribute(mesh: Any, name: str) -> Any:
    """Return a `FLOAT_COLOR`/`CORNER` color attribute named `name`, reusing
    an existing compatible layer (reset to fill color) instead of creating
    `.001` duplicates."""
    existing = mesh.color_attributes.get(name)
    if existing is not None:
        if existing.domain == "CORNER" and existing.data_type == "FLOAT_COLOR":
            n_items = len(existing.data)
            if n_items:
                existing.data.foreach_set("color", _DEFAULT_COLOR_FILL * n_items)
            mesh.color_attributes.active_color = existing
            return existing
        mesh.color_attributes.remove(existing)
    layer = mesh.color_attributes.new(name, "FLOAT_COLOR", "CORNER")
    mesh.color_attributes.active_color = layer
    return layer


class RETOPOFORGE_OT_project_uvs(_OPERATOR_BASE):
    """Copy HIGH UVs onto the LOW mesh by nearest-point projection
    (reads HIGH through the evaluated depsgraph so modifiers count;
    each LOW face stays inside one HIGH UV island, so seams survive)"""

    bl_idname = "retopo.project_uvs"
    bl_label = "Project HIGH UVs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: Any) -> set[str]:
        params = context.scene.retopo_params
        high = context.active_object
        lows = [
            o for o in context.selected_objects if o is not high and o.type == "MESH"
        ]
        if high is None or high.type != "MESH" or not lows:
            self.report(
                {"ERROR"}, "Select LOW meshes with the HIGH-poly source active"
            )
            return {"CANCELLED"}
        low = lows[0]
        if len(low.data.polygons) == 0:
            self.report(
                {"ERROR"}, f"LOW '{low.name}' has no polygons to receive UVs"
            )
            return {"CANCELLED"}

        with _evaluated_high_mesh(context, high) as (high_eval, high_mesh):
            if len(high_mesh.polygons) == 0:
                self.report(
                    {"ERROR"},
                    f"HIGH '{high.name}' has no polygons to project from",
                )
                return {"CANCELLED"}
            high_uv = high_mesh.uv_layers.active
            if high_uv is None and high.data.uv_layers.active is not None:
                high_uv = high_mesh.uv_layers.get(high.data.uv_layers.active.name)
            if high_uv is None:
                self.report(
                    {"ERROR"}, f"HIGH '{high.name}' has no UVs to project"
                )
                return {"CANCELLED"}
            low_uv = low.data.uv_layers.active
            if low_uv is None:
                low_uv = low.data.uv_layers.new(name="Projected")
            items, projected, skipped = _face_correspondence(
                high_eval,
                high_mesh,
                low,
                params.project_uv_max_dist,
                island_of=_uv_islands(high_mesh, high_uv),
            )
            huv = high_uv.uv
            for _, corners in items:
                for li, (la, lb, lc), _, (w0, w1, w2) in corners:
                    low_uv.uv[li].vector = (
                        huv[la].vector * w0
                        + huv[lb].vector * w1
                        + huv[lc].vector * w2
                    )
        line = (
            f"{low.name}: projected UVs on {projected}/"
            f"{projected + skipped} faces ({skipped} beyond range)"
        )
        context.scene.retopo_last_report += line + "\n"
        self.report({"INFO"}, line)
        return {"FINISHED"}


class RETOPOFORGE_OT_transfer_colors(_OPERATOR_BASE):
    """Copy the HIGH active vertex-color layer onto LOW by
    nearest-point projection (reuses same-named target layers; faces
    beyond range keep the fill color)"""

    bl_idname = "retopo.transfer_colors"
    bl_label = "Transfer HIGH Colors"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: Any) -> set[str]:
        params = context.scene.retopo_params
        high = context.active_object
        lows = [
            o for o in context.selected_objects if o is not high and o.type == "MESH"
        ]
        if high is None or high.type != "MESH" or not lows:
            self.report(
                {"ERROR"}, "Select LOW meshes with the HIGH-poly source active"
            )
            return {"CANCELLED"}
        low = lows[0]
        if len(low.data.polygons) == 0:
            self.report(
                {"ERROR"}, f"LOW '{low.name}' has no polygons to receive colors"
            )
            return {"CANCELLED"}

        with _evaluated_high_mesh(context, high) as (high_eval, high_mesh):
            if len(high_mesh.polygons) == 0:
                self.report(
                    {"ERROR"},
                    f"HIGH '{high.name}' has no polygons to transfer colors from",
                )
                return {"CANCELLED"}
            high_col = high_mesh.color_attributes.active_color
            if (
                high_col is None
                and high.data.color_attributes.active_color is not None
            ):
                high_col = high_mesh.color_attributes.get(
                    high.data.color_attributes.active_color.name
                )
            if high_col is None:
                self.report(
                    {"ERROR"}, f"HIGH '{high.name}' has no color attribute"
                )
                return {"CANCELLED"}
            low_col = _get_or_reuse_color_attribute(low.data, high_col.name)
            items, projected, skipped = _face_correspondence(
                high_eval, high_mesh, low, params.transfer_max_dist
            )
            by_point = high_col.domain == "POINT"
            hdata = high_col.data
            for _, corners in items:
                for li, hloops, hvs, (w0, w1, w2) in corners:
                    ia, ib, ic = hvs if by_point else hloops
                    a, b, c = hdata[ia].color, hdata[ib].color, hdata[ic].color
                    low_col.data[li].color = tuple(
                        a[k] * w0 + b[k] * w1 + c[k] * w2 for k in range(4)
                    )
        line = (
            f"{low.name}: transferred colors on {projected}/"
            f"{projected + skipped} faces ({skipped} beyond range)"
        )
        context.scene.retopo_last_report += line + "\n"
        self.report({"INFO"}, line)
        return {"FINISHED"}
