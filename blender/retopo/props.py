# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene parameters, add-on preferences, and per-object settings recall."""

from __future__ import annotations

import contextlib
import json
from typing import Any, Iterator

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from .cli import find_retopo_binary

# Must equal the module name Blender loaded us under: "retopo" on the
# legacy/sys.path test path, "bl_ext.<repo>.retopo" as an installed
# extension.
ADDON_ID: str = __package__

# Scene params snapshotted into the recall blob, grouped by Blender type.
_RECALL_INT_KEYS: tuple[str, ...] = ("target_quads",)
_RECALL_FLOAT_KEYS: tuple[str, ...] = (
    "sharp_edge",
    "smooth_normal",
    "edge_scaling",
    "adaptivity",
    "anisotropy",
    "density_min",
    "density_max",
)
_RECALL_BOOL_KEYS: tuple[str, ...] = (
    "apply_modifiers",
    "keep_original",
    "symmetry_enabled",
    "guides_enabled",
    "features_enabled",
    "density_enabled",
    "skeleton_enabled",
)
_RECALL_STR_KEYS: tuple[str, ...] = ("lod_targets", "density_vertex_group")
_RECALL_MODEL_TYPES: tuple[str, ...] = ("ORGANIC", "HARDSURFACE")
_RECALL_SYMMETRY_PLANES: tuple[str, ...] = ("AUTO", "X", "Y", "Z")


def _params_snapshot(params: Any) -> dict[str, Any]:
    snap: dict[str, Any] = {k: int(getattr(params, k)) for k in _RECALL_INT_KEYS}
    snap.update({k: float(getattr(params, k)) for k in _RECALL_FLOAT_KEYS})
    snap.update({k: bool(getattr(params, k)) for k in _RECALL_BOOL_KEYS})
    snap.update({k: str(getattr(params, k)) for k in _RECALL_STR_KEYS})
    snap["model_type"] = str(params.model_type)
    snap["symmetry_plane"] = str(params.symmetry_plane)
    return snap


def _read_recall_blob(context: Any) -> dict[str, Any]:
    try:
        blob = json.loads(context.scene.retopo_recall or "{}")
    except (ValueError, TypeError):
        return {}
    return blob if isinstance(blob, dict) else {}


def save_recall_entry(context: Any, obj_name: str, params: Any) -> None:
    """Remember the params just used for `obj_name`.

    A corrupt blob is discarded rather than merged with: the fresh
    snapshot wins outright.
    """
    blob = _read_recall_blob(context)
    blob[obj_name] = _params_snapshot(params)
    context.scene.retopo_recall = json.dumps(blob, sort_keys=True)


def load_recall_entry(context: Any, obj_name: str) -> bool:
    """Copy the saved params for `obj_name` into the scene params.

    Returns `True` when an entry existed (even if some keys were invalid
    and skipped); unknown keys and mistyped values never raise.
    """
    return _load_recall_into(context.scene, obj_name)


def _load_recall_into(scene: Any, obj_name: str) -> bool:
    try:
        blob = json.loads(scene.retopo_recall or "{}")
    except (ValueError, TypeError):
        return False
    entry = blob.get(obj_name) if isinstance(blob, dict) else None
    if not isinstance(entry, dict):
        return False
    params = scene.retopo_params
    for key in _RECALL_INT_KEYS:
        value = entry.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            try:
                setattr(params, key, value)
            except (ValueError, TypeError):
                pass
    for key in _RECALL_FLOAT_KEYS:
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                setattr(params, key, float(value))
            except (ValueError, TypeError):
                pass
    for key in _RECALL_BOOL_KEYS:
        value = entry.get(key)
        if isinstance(value, bool):
            try:
                setattr(params, key, value)
            except (ValueError, TypeError):
                pass
    for key in _RECALL_STR_KEYS:
        value = entry.get(key)
        if isinstance(value, str):
            try:
                setattr(params, key, value)
            except (ValueError, TypeError):
                pass
    if entry.get("model_type") in _RECALL_MODEL_TYPES:
        try:
            params.model_type = entry["model_type"]
        except (ValueError, TypeError):
            pass
    if entry.get("symmetry_plane") in _RECALL_SYMMETRY_PLANES:
        try:
            params.symmetry_plane = entry["symmetry_plane"]
        except (ValueError, TypeError):
            pass
    return True


# Recall fires when the user makes a different mesh object active (the
# next depsgraph update notices), never on Remesh itself: values edited
# in the panel after picking an object are what the next remesh uses.
_recall_last_active: dict[tuple[int, str], str | None] = {}
_recall_pause_depth: int = 0


def _active_mesh_name(view_layer: Any) -> str | None:
    active = view_layer.objects.active
    if active is None or active.type != "MESH":
        return None
    return active.name


def _recall_key(scene: Any, view_layer: Any) -> tuple[int, str]:
    return (scene.as_pointer(), view_layer.name)


def _mark_active_seen(context: Any) -> None:
    view_layer = context.view_layer
    _recall_last_active[_recall_key(context.scene, view_layer)] = _active_mesh_name(
        view_layer
    )


def _pause_recall() -> None:
    global _recall_pause_depth
    _recall_pause_depth += 1


def _resume_recall(context: Any) -> None:
    global _recall_pause_depth
    _recall_pause_depth = max(0, _recall_pause_depth - 1)
    _mark_active_seen(context)


@contextlib.contextmanager
def _recall_paused(context: Any) -> Iterator[None]:
    """Pause active-object recall while an operator temporarily switches active."""
    _pause_recall()
    try:
        yield
    finally:
        _resume_recall(context)


@persistent
def _recall_on_active_change(scene: Any, depsgraph: Any) -> None:
    if _recall_pause_depth:
        return
    view_layer = depsgraph.view_layer
    name = _active_mesh_name(view_layer)
    key = _recall_key(scene, view_layer)
    if key in _recall_last_active and _recall_last_active[key] == name:
        return
    _recall_last_active[key] = name
    if name is not None:
        _load_recall_into(scene, name)


@persistent
def _recall_forget_on_load(_: Any) -> None:
    _recall_last_active.clear()


class RETOPOFORGE_PG_params(bpy.types.PropertyGroup):
    """Shared remesh parameters stored on the scene so sidebar edits persist."""

    target_quads: IntProperty(
        name="Target Quads",
        description="Approximate output quad count (engine guidance, expect some undershoot)",
        default=5000,
        min=4,
        max=1000000,
    )
    model_type: EnumProperty(
        name="Model Type",
        description="Model type hint for the remesher",
        items=[
            ("ORGANIC", "Organic", "Smooth organic shapes"),
            ("HARDSURFACE", "Hardsurface", "Hard-surface / CAD-like shapes"),
        ],
        default="ORGANIC",
    )
    sharp_edge: FloatProperty(
        name="Sharp Edge",
        description="Dihedral angle above which edges survive as sharp features",
        default=90.0,
        min=30.0,
        max=180.0,
    )
    smooth_normal: FloatProperty(
        name="Smooth Normal",
        description="Normal angle threshold for smoothing",
        default=0.0,
        min=0.0,
        max=180.0,
    )
    edge_scaling: FloatProperty(
        name="Edge Scaling",
        description="Edge length multiplier (higher = coarser mesh)",
        default=1.0,
        min=1.0,
        max=4.0,
    )
    adaptivity: FloatProperty(
        name="Adaptivity",
        description="Curvature-adaptive density: 0 = uniform quads, 1 = dense on curves, sparse on flats",
        default=1.0,
        min=0.0,
        max=1.0,
    )
    anisotropy: FloatProperty(
        name="Anisotropy",
        description="Curvature-adaptive elongation: 0 = square quads, 1 = stretched along curvature",
        default=1.0,
        min=0.0,
        max=1.0,
    )
    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Remesh the evaluated mesh with modifiers applied",
        default=True,
    )
    keep_original: BoolProperty(
        name="Keep Original",
        description="Spawn a remeshed copy and hide the original instead of replacing its mesh",
        default=False,
    )
    compute_uvs: BoolProperty(
        name="Compute UVs",
        description="Ask the engine for fresh UVs on the remesh (passed as --uvs on)",
        default=False,
    )
    lod_targets: StringProperty(
        name="LOD Targets",
        description="Comma-separated quad counts for Generate LODs (one rung per value)",
        default="10000,5000,2000",
    )
    symmetry_enabled: BoolProperty(
        name="Symmetry",
        description="Mirror-symmetry constraints (passes --symmetry to the CLI; off by default)",
        default=False,
    )
    symmetry_plane: EnumProperty(
        name="Symmetry Plane",
        description="Symmetry plane: auto-detect the dominant plane, or pin an axis",
        items=[
            ("AUTO", "Auto", "Detect the dominant symmetry plane"),
            ("X", "X", "Mirror across the YZ plane (x = 0)"),
            ("Y", "Y", "Mirror across the XZ plane (y = 0)"),
            ("Z", "Z", "Mirror across the XY plane (z = 0)"),
        ],
        default="AUTO",
    )
    bake_size: IntProperty(
        name="Bake Size",
        description="Width/height of baked textures in pixels",
        default=1024,
        min=64,
        max=4096,
    )
    bake_extrusion: FloatProperty(
        name="Bake Extrusion",
        description="Ray extrusion distance for selected-to-active bakes",
        default=0.05,
        min=0.0,
        max=10.0,
    )
    bake_margin: IntProperty(
        name="Bake Margin",
        description="Baked UV island margin in pixels",
        default=16,
        min=0,
        max=64,
    )
    bake_normal: BoolProperty(
        name="Bake Normal",
        description="Also bake a tangent-space normal map alongside diffuse",
        default=True,
    )
    bake_roughness: BoolProperty(
        name="Bake Roughness",
        description="Bake roughness when the HIGH source uses it",
        default=True,
    )
    bake_metallic: BoolProperty(
        name="Bake Metallic",
        description="Bake metallic when the HIGH source uses it",
        default=True,
    )
    bake_ao: BoolProperty(
        name="Bake AO",
        description="Bake ambient occlusion from the HIGH source",
        default=True,
    )
    bake_emission: BoolProperty(
        name="Bake Emission",
        description="Bake emission when the HIGH source emits light",
        default=True,
    )
    bake_uv_mode: EnumProperty(
        name="LOW UVs",
        description="How the LOW bake target gets UVs",
        items=[
            ("SMART", "Smart UV", "Fast automatic projection (current)"),
            (
                "UNWRAP",
                "Unwrap + Pack",
                "Angle-based/conformal unwrap, uniform texel density, packed",
            ),
        ],
        default="SMART",
    )
    bake_unwrap_method: EnumProperty(
        name="Unwrap Method",
        description="Unwrap algorithm for Unwrap + Pack mode",
        items=[
            ("ANGLE_BASED", "Angle Based", "Best for organic shapes"),
            ("CONFORMAL", "Conformal", "Best for hard-surface shapes"),
        ],
        default="ANGLE_BASED",
    )
    bake_pack_margin: FloatProperty(
        name="Pack Margin",
        description="Island margin for Unwrap + Pack mode (UV units)",
        default=0.005,
        min=0.0,
        max=0.1,
    )
    bake_average_scale: BoolProperty(
        name="Uniform Texel Density",
        description="Average island scale in Unwrap + Pack mode so every "
        "island shares one texel density",
        default=True,
    )
    bake_texel_density: FloatProperty(
        name="Texel Density (px/unit)",
        description="Target texel density for Unwrap + Pack mode "
        "(0: pack fit only, density is reported)",
        default=0.0,
        min=0.0,
    )
    project_uv_max_dist: FloatProperty(
        name="Projection Range",
        description="Max HIGH distance for UV projection, as a fraction "
        "of the HIGH bounding-box diagonal (faces beyond "
        "keep their UVs)",
        default=0.01,
        min=0.0,
        max=1.0,
    )
    transfer_max_dist: FloatProperty(
        name="Transfer Range",
        description="Max HIGH distance for color transfer, as a fraction "
        "of the HIGH bounding-box diagonal (faces beyond "
        "keep the fill color)",
        default=0.01,
        min=0.0,
        max=1.0,
    )
    bake_cage: PointerProperty(
        name="Bake Cage",
        description="Cage mesh for selected-to-active bakes (empty: ray extrusion)",
        type=bpy.types.Object,
    )
    guides_enabled: BoolProperty(
        name="Flow Guides",
        description="Constrain quad flow to the edge selection on each target (passed as --guides)",
        default=False,
    )
    features_enabled: BoolProperty(
        name="Sharp Features",
        description="Keep sharp-marked edges crisp on each target (passed as --features)",
        default=False,
    )
    density_enabled: BoolProperty(
        name="Density Mask",
        description="Drive local density from a vertex group (passed as --density)",
        default=False,
    )
    density_vertex_group: StringProperty(
        name="Density Group",
        description="Vertex group whose weights map to density multipliers",
        default="",
    )
    skeleton_enabled: BoolProperty(
        name="Skeleton Density",
        description=(
            "Use the object's armature: creases between sibling "
            "limbs (the groin) get edges wide enough for the skin "
            "weights to blend across (passed as --skeleton)"
        ),
        default=False,
    )
    density_min: FloatProperty(
        name="Density Min",
        description="Multiplier for weight 0 (CLI clamps outside 0.25-4.0)",
        default=0.25,
        min=0.01,
        max=8.0,
    )
    density_max: FloatProperty(
        name="Density Max",
        description="Multiplier for weight 1 (CLI clamps outside 0.25-4.0)",
        default=4.0,
        min=0.01,
        max=8.0,
    )

    def symmetry_value(self) -> str:
        """CLI `--symmetry` value: `'off'` unless enabled, else `'auto'|'x'|'y'|'z'`."""
        if not self.symmetry_enabled:
            return "off"
        return str(self.symmetry_plane).lower()

    def cli_args(self, binary: str, input_path: str, output_path: str) -> list[str]:
        """Build the base `retopo` CLI argument vector."""
        return [
            binary,
            "--input",
            input_path,
            "--output",
            output_path,
            "--target-quads",
            str(self.target_quads),
            "--edge-scaling",
            repr(float(self.edge_scaling)),
            "--sharp-edge",
            repr(float(self.sharp_edge)),
            "--smooth-normal",
            repr(float(self.smooth_normal)),
            "--adaptivity",
            repr(float(self.adaptivity)),
            "--anisotropy",
            repr(float(self.anisotropy)),
            "--model-type",
            "hardsurface" if self.model_type == "HARDSURFACE" else "organic",
            "--symmetry",
            self.symmetry_value(),
            "--uvs",
            "on" if self.compute_uvs else "off",
        ]


class RetopoForgePreferences(bpy.types.AddonPreferences):
    """User preferences for locating the `retopo` CLI executable."""

    bl_idname = ADDON_ID

    retopo_binary: StringProperty(
        name="Retopo CLI",
        description="Path to the retopo `retopo` binary (empty: search PATH, then the build tree)",
        default="",
        subtype="FILE_PATH",
    )

    def draw(self, context: Any) -> None:
        layout = self.layout
        layout.prop(self, "retopo_binary")
        found = find_retopo_binary(self.retopo_binary)
        box = layout.box()
        if found:
            box.label(text="Using: " + found, icon="CHECKMARK")
        else:
            box.label(text="No retopo binary found", icon="ERROR")
