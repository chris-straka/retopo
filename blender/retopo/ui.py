# SPDX-License-Identifier: GPL-3.0-or-later
"""3D Viewport sidebar panels for RetopoForge."""

from __future__ import annotations

from typing import Any

import bpy

from .cli import find_retopo_binary
from .props import ADDON_ID


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
        layout.label(text="Edge selection, per target")
        layout.prop(params, "guides_enabled")
        layout.operator(
            "retopo.export_guides",
            text="Export Guide Strokes",
            icon="GREASEPENCIL",
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
        bcol.prop(params, "bake_extrusion")
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
