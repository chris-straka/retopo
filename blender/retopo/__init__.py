# SPDX-License-Identifier: GPL-3.0-or-later
# RetopoForge Blender extension: quad remeshing via the retopo CLI.
#
# Flow per object: export selection to a temp OBJ (identity transform, so
# local == world and no transform bugs are possible), run the `retopo` CLI
# as a subprocess, import the result and swap it onto the original object.
#
# The engine stays MIT-licensed: this GPL extension talks to it only as a
# subprocess over OBJ files, never linked, never imported.

from __future__ import annotations

import importlib
import subprocess  # Re-exported so adapter hooks can wrap retopo.subprocess.

try:
    import bpy
    from bpy.props import PointerProperty, StringProperty
except ImportError:  # Outside Blender (pure-Python unit tests).
    bpy = None  # type: ignore[assignment]

from . import cli, constraints
from .cli import (
    find_retopo_binary,
    parse_lod_rung_counts,
    parse_lod_targets,
    parse_summary,
)
from .constraints import (
    GENERIC_BEND_DEGREES,
    JOINT_BEND_ROLES,
    constraint_args_for_target,
    count_obj_vertices,
    density_multipliers,
    joint_bend_degrees,
    skeleton_bones,
    trace_edge_chains,
    trace_sharp_chains,
    write_density_multipliers,
    write_feature_chains,
    write_guide_chains,
    write_skeleton_file,
)

ADDON_ID: str = __package__

if bpy is not None:
    if "_LOADED" in locals():
        for _mod in (cli, constraints, props, bake, operators, ui):
            importlib.reload(_mod)

    from . import bake, operators, props, ui
    from .bake import (
        RETOPOFORGE_OT_bake_textures,
        RETOPOFORGE_OT_project_uvs,
        RETOPOFORGE_OT_transfer_colors,
        _uv_world_areas,
    )
    from .operators import (
        RETOPOFORGE_OT_export_density,
        RETOPOFORGE_OT_export_features,
        RETOPOFORGE_OT_export_guides,
        RETOPOFORGE_OT_generate_lods,
        RETOPOFORGE_OT_reload,
        RETOPOFORGE_OT_remesh,
        RETOPOFORGE_OT_remesh_and_bake,
    )
    from .props import (
        RETOPOFORGE_PG_params,
        RetopoForgePreferences,
        _recall_forget_on_load,
        _recall_last_active,
        _recall_on_active_change,
        load_recall_entry,
        save_recall_entry,
    )
    from .ui import (
        RETOPOFORGE_PT_advanced,
        RETOPOFORGE_PT_bake,
        RETOPOFORGE_PT_density,
        RETOPOFORGE_PT_features,
        RETOPOFORGE_PT_guides,
        RETOPOFORGE_PT_lods,
        RETOPOFORGE_PT_panel,
    )

    _CLASSES = (
        RetopoForgePreferences,
        RETOPOFORGE_PG_params,
        RETOPOFORGE_OT_remesh,
        RETOPOFORGE_OT_generate_lods,
        RETOPOFORGE_OT_bake_textures,
        RETOPOFORGE_OT_remesh_and_bake,
        RETOPOFORGE_OT_project_uvs,
        RETOPOFORGE_OT_transfer_colors,
        RETOPOFORGE_OT_export_guides,
        RETOPOFORGE_OT_export_features,
        RETOPOFORGE_OT_export_density,
        RETOPOFORGE_OT_reload,
        RETOPOFORGE_PT_panel,
        RETOPOFORGE_PT_advanced,
        RETOPOFORGE_PT_guides,
        RETOPOFORGE_PT_features,
        RETOPOFORGE_PT_density,
        RETOPOFORGE_PT_lods,
        RETOPOFORGE_PT_bake,
    )
    _LOADED = True

    def register() -> None:
        """Register RetopoForge preferences, properties, operators, and panels."""
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
        bpy.types.Scene.retopo_params = PointerProperty(
            type=RETOPOFORGE_PG_params
        )
        bpy.types.Scene.retopo_last_report = StringProperty(
            name="Last Report",
            description="Stats from the most recent retopo remesh",
            default="",
        )
        bpy.types.Scene.retopo_recall = StringProperty(
            name="Settings Recall",
            description="Per-object last-used remesh parameters as a JSON blob",
            default="",
        )
        if _recall_on_active_change not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(
                _recall_on_active_change
            )
        if _recall_forget_on_load not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(_recall_forget_on_load)

    def unregister() -> None:
        """Unregister RetopoForge handlers, properties, and classes."""
        for handlers, handler in (
            (bpy.app.handlers.depsgraph_update_post, _recall_on_active_change),
            (bpy.app.handlers.load_post, _recall_forget_on_load),
        ):
            if handler in handlers:
                handlers.remove(handler)
        _recall_last_active.clear()
        del bpy.types.Scene.retopo_recall
        del bpy.types.Scene.retopo_last_report
        del bpy.types.Scene.retopo_params
        for cls in reversed(_CLASSES):
            bpy.utils.unregister_class(cls)

else:

    def register() -> None:
        """No-op outside Blender."""

    def unregister() -> None:
        """No-op outside Blender."""


__all__ = [
    "ADDON_ID",
    "GENERIC_BEND_DEGREES",
    "JOINT_BEND_ROLES",
    "constraint_args_for_target",
    "count_obj_vertices",
    "density_multipliers",
    "find_retopo_binary",
    "joint_bend_degrees",
    "parse_lod_rung_counts",
    "parse_lod_targets",
    "parse_summary",
    "register",
    "skeleton_bones",
    "subprocess",
    "trace_edge_chains",
    "trace_sharp_chains",
    "unregister",
    "write_density_multipliers",
    "write_feature_chains",
    "write_guide_chains",
    "write_skeleton_file",
]
if bpy is not None:
    __all__ += [
        "RETOPOFORGE_OT_bake_textures",
        "RETOPOFORGE_OT_export_density",
        "RETOPOFORGE_OT_export_features",
        "RETOPOFORGE_OT_export_guides",
        "RETOPOFORGE_OT_generate_lods",
        "RETOPOFORGE_OT_project_uvs",
        "RETOPOFORGE_OT_reload",
        "RETOPOFORGE_OT_remesh",
        "RETOPOFORGE_OT_remesh_and_bake",
        "RETOPOFORGE_OT_transfer_colors",
        "RETOPOFORGE_PG_params",
        "RETOPOFORGE_PT_advanced",
        "RETOPOFORGE_PT_bake",
        "RETOPOFORGE_PT_density",
        "RETOPOFORGE_PT_features",
        "RETOPOFORGE_PT_guides",
        "RETOPOFORGE_PT_lods",
        "RETOPOFORGE_PT_panel",
        "RetopoForgePreferences",
        "_uv_world_areas",
        "bake",
        "load_recall_entry",
        "operators",
        "props",
        "save_recall_entry",
        "ui",
    ]


if __name__ == "__main__":
    register()
