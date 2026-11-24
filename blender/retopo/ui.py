# SPDX-License-Identifier: GPL-3.0-or-later
"""3D Viewport sidebar panels for RetopoForge."""

from __future__ import annotations

from typing import Any

import bpy

from .bake import compute_auto_bake_rays, object_world_diagonal
from .cli import find_retopo_binary
from .constraints import guide_preview_world_polylines, raw_annotation_strokes
from .props import ADDON_ID

_GUIDE_PREVIEW_COLOR: tuple[float, float, float, float] = (0.15, 0.95, 1.0, 0.95)
_preview_draw_handle: Any = None
_preview_cache_key: Any = None
_preview_segments: list[tuple[float, float, float]] = []


def invalidate_guide_preview_cache() -> None:
    global _preview_cache_key, _preview_segments
    _preview_cache_key = None
    _preview_segments = []


def _preview_line_segments(context: Any) -> list[tuple[float, float, float]]:
    global _preview_cache_key, _preview_segments
    scene = getattr(context, "scene", None)
    if scene is None:
        return []
    params = getattr(scene, "retopo_params", None)
    if params is None or not getattr(params, "guides_preview", False):
        return []
    obj = context.view_layer.objects.active
    if obj is None or obj.type != "MESH" or obj.data is None:
        return []
    strokes = raw_annotation_strokes(scene)
    edges = obj.data.edges
    edge_buf = bytearray(len(edges))
    if len(edge_buf):
        edges.foreach_get("select", edge_buf)
    edge_key = bytes(edge_buf) if len(edge_buf) <= 200000 else edge_buf.count(1)
    if not strokes and not any(edge_buf):
        return []
    key = (
        scene.as_pointer(),
        obj.as_pointer(),
        len(obj.data.vertices),
        len(obj.data.polygons),
        int(params.target_quads),
        bool(params.guides_mirror),
        bool(params.symmetry_enabled),
        str(params.symmetry_plane),
        bool(params.apply_modifiers),
        edge_key,
        tuple(tuple(pt for pt in st) for st in strokes),
        tuple(tuple(float(v) for v in row) for row in obj.matrix_world),
    )
    if key != _preview_cache_key:
        polys = guide_preview_world_polylines(scene, obj, params)
        segs: list[tuple[float, float, float]] = []
        for poly in polys:
            for a, b in zip(poly, poly[1:]):
                segs.append((float(a[0]), float(a[1]), float(a[2])))
                segs.append((float(b[0]), float(b[1]), float(b[2])))
        _preview_cache_key = key
        _preview_segments = segs
    return _preview_segments


def _draw_guide_preview_callback() -> None:
    import gpu
    from gpu_extras.batch import batch_for_shader

    segments = _preview_line_segments(bpy.context)
    if not segments:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINES", {"pos": segments})
    prev_depth = gpu.state.depth_test_get()
    prev_blend = gpu.state.blend_get()
    prev_width = gpu.state.line_width_get()
    try:
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(2.5)
        shader.bind()
        shader.uniform_float("color", _GUIDE_PREVIEW_COLOR)
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(prev_width)
        gpu.state.blend_set(prev_blend)
        gpu.state.depth_test_set(prev_depth)


def register_guide_preview() -> None:
    """Register the 3D Viewport GPU guide-polyline preview handler in GUI mode."""
    global _preview_draw_handle
    invalidate_guide_preview_cache()
    if bpy.app.background or _preview_draw_handle is not None:
        return
    _preview_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_guide_preview_callback, (), "WINDOW", "POST_VIEW"
    )


def unregister_guide_preview() -> None:
    """Remove the 3D Viewport GPU guide-polyline preview handler."""
    global _preview_draw_handle
    invalidate_guide_preview_cache()
    if _preview_draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_preview_draw_handle, "WINDOW")
        _preview_draw_handle = None


class RETOPOFORGE_PT_panel(bpy.types.Panel):
    bl_label = "RetopoForge"
    bl_idname = "RETOPOFORGE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RetopoForge"

    def draw(self, context: Any) -> None:
        layout = self.layout
        prefs = context.preferences.addons[ADDON_ID].preferences
        binary = find_retopo_binary(prefs.retopo_binary)
        status = layout.box()
        if binary:
            status.label(text="retopo: found", icon="CHECKMARK")
        else:
            status.label(text="retopo: missing", icon="ERROR")
            status.label(text="Set the path in Preferences")
        layout.operator(
            "retopo.remesh", text="Remesh Selected", icon="MOD_REMESH"
        )
        params = context.scene.retopo_params
        col = layout.column(align=True)
        col.prop(params, "target_quads")
        col.prop(params, "model_type")
        col.prop(params, "sharp_edge")
        col.prop(params, "compute_uvs")
        col.prop(params, "apply_modifiers")
        col.prop(params, "keep_original")
        col.prop(params, "symmetry_enabled")
        sym_row = col.row()
        sym_row.enabled = params.symmetry_enabled
        sym_row.prop(params, "symmetry_plane")
        report = context.scene.retopo_last_report
        if report:
            box = layout.box()
            for line in report.strip().split("\n"):
                box.label(text=line)
        layout.label(text="Remeshing replaces topology;", icon="INFO")
        layout.label(text="Original UVs do not survive.")
        layout.operator(
            "retopo.reload_scripts", text="Reload Scripts", icon="FILE_REFRESH"
        )


class RETOPOFORGE_PT_advanced(bpy.types.Panel):
    bl_label = "Advanced"
    bl_idname = "RETOPOFORGE_PT_advanced"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RetopoForge"
    bl_parent_id = "RETOPOFORGE_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Any) -> None:
        params = context.scene.retopo_params
        col = self.layout.column(align=True)
        col.prop(params, "smooth_normal")
        col.prop(params, "edge_scaling")
        col.prop(params, "adaptivity")
        col.prop(params, "anisotropy")


class RETOPOFORGE_PT_guides(bpy.types.Panel):
    bl_label = "Flow Guides"
    bl_idname = "RETOPOFORGE_PT_guides"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RetopoForge"
    bl_parent_id = "RETOPOFORGE_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Any) -> None:
        params = context.scene.retopo_params
        layout = self.layout
        layout.prop(params, "guides_enabled")
        row = layout.row(align=True)
        row.operator("retopo.draw_guides", text="Draw Guides", icon="GREASEPENCIL")
        row.operator("retopo.clear_guide_strokes", text="Clear", icon="X")
        col = layout.column(align=True)
        col.prop(params, "guides_mirror")
        col.prop(params, "guides_preview")
        strokes = len(raw_annotation_strokes(context.scene))
        if strokes:
            layout.label(text=f"Surface strokes: {strokes}", icon="CHECKMARK")
        else:
            layout.label(text="Draw on surface or select edges")
        layout.operator(
            "retopo.export_guides",
            text="Export Guide Strokes",
            icon="EXPORT",
        )


class RETOPOFORGE_PT_features(bpy.types.Panel):
    bl_label = "Sharp Features"
    bl_idname = "RETOPOFORGE_PT_features"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RetopoForge"
    bl_parent_id = "RETOPOFORGE_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Any) -> None:
        params = context.scene.retopo_params
        layout = self.layout
        layout.label(text="Marked edges, per target")
        layout.prop(params, "features_enabled")
        layout.operator(
            "retopo.export_features",
            text="Export Sharp Features",
            icon="EDGESEL",
        )


class RETOPOFORGE_PT_density(bpy.types.Panel):
    bl_label = "Density Mask"
    bl_idname = "RETOPOFORGE_PT_density"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RetopoForge"
    bl_parent_id = "RETOPOFORGE_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Any) -> None:
        params = context.scene.retopo_params
        layout = self.layout
        layout.label(text="Vertex group weights")
        layout.prop(params, "density_enabled")
        dcol = layout.column(align=True)
        dcol.enabled = params.density_enabled
        active = context.view_layer.objects.active
        if active is not None and active.type == "MESH":
            dcol.prop_search(
                params,
                "density_vertex_group",
                active,
                "vertex_groups",
                text="Group",
            )
        else:
            dcol.prop(params, "density_vertex_group")
        drow = dcol.row(align=True)
        drow.prop(params, "density_min")
        drow.prop(params, "density_max")
        layout.operator(
            "retopo.export_density",
            text="Export Density Mask",
            icon="GROUP_VERTEX",
        )
        layout.separator()
        layout.label(text="Armature")
        layout.prop(params, "skeleton_enabled")


class RETOPOFORGE_PT_lods(bpy.types.Panel):
    bl_label = "LODs"
    bl_idname = "RETOPOFORGE_PT_lods"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RetopoForge"
    bl_parent_id = "RETOPOFORGE_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Any) -> None:
        params = context.scene.retopo_params
        layout = self.layout
        layout.prop(params, "lod_targets")
        layout.operator(
            "retopo.generate_lods", text="Generate LODs", icon="MOD_DECIM"
        )


class RETOPOFORGE_PT_bake(bpy.types.Panel):
    bl_label = "Bake Assist"
    bl_idname = "RETOPOFORGE_PT_bake"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RetopoForge"
    bl_parent_id = "RETOPOFORGE_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Any) -> None:
        params = context.scene.retopo_params
        layout = self.layout
        layout.label(text="Active = HIGH, Selected = LOW")
        bcol = layout.column(align=True)
        bcol.prop(params, "bake_size")
        bcol.prop(params, "bake_extrusion_auto")
        if params.bake_extrusion_auto:
            bcol.prop(params, "bake_extrusion_scale")
            active = context.view_layer.objects.active
            target = next(
                (
                    o
                    for o in context.selected_objects
                    if o.type == "MESH" and o is not active
                ),
                active,
            )
            if (
                params.bake_cage is None
                and target is not None
                and target.type == "MESH"
            ):
                diag = object_world_diagonal(target, target.data)
                if diag > 0.0:
                    extr, auto_ray = compute_auto_bake_rays(
                        diag, float(params.bake_extrusion_scale)
                    )
                    eff_ray = (
                        float(params.bake_ray_distance)
                        if float(params.bake_ray_distance) > 0.0
                        else auto_ray
                    )
                    bcol.label(
                        text=f"Auto: extr={extr:.4g}, ray={eff_ray:.4g}",
                        icon="INFO",
                    )
            bcol.prop(params, "bake_ray_distance")
        else:
            bcol.prop(params, "bake_extrusion")
            bcol.prop(params, "bake_ray_distance")
        bcol.prop(params, "bake_margin")
        bcol.prop(params, "bake_cage")
        bcol.prop(params, "bake_normal")
        bcol.prop(params, "bake_roughness")
        bcol.prop(params, "bake_metallic")
        bcol.prop(params, "bake_ao")
        bcol.prop(params, "bake_emission")
        bcol.separator()
        bcol.prop(params, "bake_uv_mode")
        bcol.prop(params, "bake_unwrap_method")
        bcol.prop(params, "bake_pack_margin")
        bcol.prop(params, "bake_average_scale")
        bcol.prop(params, "bake_texel_density")
        layout.operator(
            "retopo.bake_textures", text="Bake High to Low", icon="RENDER_RESULT"
        )
        layout.operator(
            "retopo.remesh_and_bake", text="Remesh + Bake All", icon="PLAY"
        )
        bcol.prop(params, "project_uv_max_dist")
        layout.operator(
            "retopo.project_uvs", text="Project HIGH UVs", icon="UV"
        )
        bcol.prop(params, "transfer_max_dist")
        layout.operator(
            "retopo.transfer_colors",
            text="Transfer HIGH Colors",
            icon="GROUP_VCOL",
        )
