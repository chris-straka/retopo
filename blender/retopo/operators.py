# SPDX-License-Identifier: GPL-3.0-or-later
"""Remesh, LOD generation, constraint export, and utility operators."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

import bpy
from bpy.props import BoolProperty, StringProperty
from mathutils import Matrix

from .cli import find_retopo_binary, parse_lod_rung_counts, parse_lod_targets, parse_summary
from .constraints import (
    constraint_args_for_target,
    density_multipliers,
    trace_edge_chains,
    trace_sharp_chains,
    write_density_multipliers,
    write_feature_chains,
    write_guide_chains,
)
from .props import (
    ADDON_ID,
    _active_mesh_name,
    _pause_recall,
    _recall_paused,
    _resume_recall,
    save_recall_entry,
)


def _package_attr(name: str, default: Any) -> Any:
    """Resolve `name` on the package module first so adapter hooks work."""
    pkg = sys.modules.get(__package__)
    return getattr(pkg, name, default) if pkg is not None else default


def _select_results(context: Any, results: dict[str, Any], active_name: str | None) -> None:
    """Leave remesh results selected with the active source's result active."""
    if not results:
        return
    for o in context.selected_objects:
        o.select_set(False)
    for obj in results.values():
        obj.select_set(True)
    context.view_layer.objects.active = (
        results.get(active_name) if active_name else None
    ) or next(iter(results.values()))


def _mesh_objects(context: Any) -> list[Any]:
    return [o for o in context.selected_objects if o.type == "MESH"]


def _ensure_object_mode() -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _operator_has_property(op: Any, name: str) -> bool:
    return name in op.get_rna_type().properties.keys()


def _export_selection(filepath: str, apply_modifiers: bool) -> None:
    kwargs: dict[str, Any] = dict(
        filepath=filepath,
        export_selected_objects=True,
        apply_modifiers=apply_modifiers,
        # Identity axis mapping: local == world under identity transform.
        forward_axis="Y",
        up_axis="Z",
        export_uv=False,
        export_normals=False,
        export_materials=False,
        export_triangulated_mesh=False,
    )
    if _operator_has_property(bpy.ops.wm.obj_export, "apply_transform"):
        kwargs["apply_transform"] = False
    bpy.ops.wm.obj_export(**kwargs)


def _import_result(filepath: str) -> list[Any]:
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=filepath, forward_axis="Y", up_axis="Z")
    return [o for o in bpy.data.objects if o not in before and o.type == "MESH"]


def _validate_nonempty_mesh(
    context: Any, obj: Any, apply_modifiers: bool, action_label: str
) -> None:
    """Raise `RuntimeError` if `obj` has zero polygons to process."""
    if apply_modifiers:
        depsgraph = context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        eval_mesh = obj_eval.to_mesh()
        try:
            poly_count = len(eval_mesh.polygons)
        finally:
            obj_eval.to_mesh_clear()
    else:
        poly_count = len(obj.data.polygons)
    if poly_count == 0:
        raise RuntimeError(
            f"Object '{obj.name}' has no polygons to {action_label}"
        )


def _export_obj_identity(
    context: Any, obj: Any, filepath: str, apply_modifiers: bool
) -> None:
    """Export `obj` to `filepath` as OBJ under an identity world transform."""
    _ensure_object_mode()
    context.view_layer.update()
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    saved_matrix = obj.matrix_world.copy()
    obj.matrix_world = Matrix()
    context.view_layer.update()
    try:
        _export_selection(filepath, apply_modifiers)
    finally:
        obj.matrix_world = saved_matrix


class RETOPOFORGE_OT_remesh(bpy.types.Operator):
    """Remesh the selected mesh objects with the retopo engine"""

    bl_idname = "retopo.remesh"
    bl_label = "Remesh with RetopoForge"
    bl_options = {"REGISTER", "UNDO"}

    # Chained callers (Remesh + Bake All) need HIGH intact whatever the
    # panel or recalled settings say; never saved into the recall blob.
    force_keep_original: BoolProperty(
        name="Force Keep Original",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    def _keep_original(self) -> bool:
        return bool(self._params.keep_original or self.force_keep_original)

    def _export_job(self, context: Any, obj: Any, index: int) -> str:
        _validate_nonempty_mesh(
            context, obj, self._params.apply_modifiers, "remesh"
        )
        input_path = os.path.join(self._tmpdir, f"in_{index}.obj")
        _export_obj_identity(
            context, obj, input_path, self._params.apply_modifiers
        )
        return input_path

    def _finish_job(
        self, context: Any, job: dict[str, Any], output_path: str, stdout_text: str
    ) -> str:
        imported = _import_result(output_path)
        if not imported:
            raise RuntimeError("CLI output imported no mesh objects")
        new_mesh = imported[0].data
        new_mesh.name = job["obj"].name + "_remeshed"
        if self._keep_original():
            result_obj = job["obj"].copy()
            result_obj.data = new_mesh
            result_obj.name = job["obj"].name + "_retopo"
            for coll in job["obj"].users_collection:
                coll.objects.link(result_obj)
            result_obj.matrix_world = job["matrix"]
            job["obj"].hide_viewport = True
        else:
            result_obj = job["obj"]
            old_mesh = result_obj.data
            result_obj.data = new_mesh
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
            result_obj.matrix_world = job["matrix"]
        if self._params.apply_modifiers:
            result_obj.modifiers.clear()
        for temp in imported:
            bpy.data.objects.remove(temp, do_unlink=True)
        self._results[job["obj"].name] = result_obj
        summary = parse_summary(stdout_text)
        if summary is not None:
            quads, non_quads, verts, seconds = summary
            line = (
                f"{job['obj'].name}: {quads} quads, {non_quads} non-quads, "
                f"{verts} verts in {seconds:.1f}s"
            )
        else:
            line = f"{job['obj'].name}: done (no summary parsed)"
        context.scene.retopo_last_report += line + "\n"
        save_recall_entry(context, job["obj"].name, self._params)
        return line

    def _cleanup(self, context: Any) -> None:
        wm = context.window_manager
        wm.progress_end()
        if getattr(self, "_timer", None) is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        for area in context.screen.areas if context.screen else []:
            try:
                area.header_text_set(None)
            except RuntimeError:
                pass
        log_handle = getattr(self, "_log_handle", None)
        if log_handle is not None:
            try:
                log_handle.close()
            except OSError:
                pass
            self._log_handle = None
        tmpdir = getattr(self, "_tmpdir", "")
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)
        if getattr(self, "_recall_paused", False):
            self._recall_paused = False
            _select_results(context, self._results, self._active_name)
            _resume_recall(context)

    # -- synchronous path (headless use, tests) -------------------------

    def execute(self, context: Any) -> set[str]:
        self._params = context.scene.retopo_params
        prefs = context.preferences.addons[ADDON_ID].preferences
        binary = find_retopo_binary(prefs.retopo_binary)
        if not binary:
            self.report(
                {"ERROR"}, "retopo binary not found (see add-on preferences)"
            )
            return {"CANCELLED"}
        targets = _mesh_objects(context)
        if not targets:
            self.report({"ERROR"}, "Select at least one mesh object")
            return {"CANCELLED"}
        active_name = _active_mesh_name(context.view_layer)
        self._results: dict[str, Any] = {}
        with _recall_paused(context):
            result = self._execute_targets(context, binary, targets)
            _select_results(context, self._results, active_name)
        return result

    def _execute_targets(
        self, context: Any, binary: str, targets: list[Any]
    ) -> set[str]:
        self._tmpdir = tempfile.mkdtemp(prefix="retopo_")
        context.scene.retopo_last_report = ""
        constraint_fn = _package_attr(
            "constraint_args_for_target", constraint_args_for_target
        )
        subproc = _package_attr("subprocess", subprocess)
        try:
            for index, obj in enumerate(targets):
                matrix = obj.matrix_world.copy()
                try:
                    input_path = self._export_job(context, obj, index)
                    output_path = os.path.join(self._tmpdir, f"out_{index}.obj")
                    extra, notes = constraint_fn(
                        self._params, obj, input_path, self._tmpdir, str(index)
                    )
                    for note in notes:
                        self.report({"INFO"}, note)
                    proc = subproc.run(
                        self._params.cli_args(binary, input_path, output_path)
                        + extra,
                        capture_output=True,
                        text=True,
                    )
                    if proc.returncode != 0:
                        tail = (proc.stderr or "").strip().splitlines()
                        detail = tail[-1] if tail else "unknown error"
                        raise RuntimeError(f"retopo failed: {detail}")
                    line = self._finish_job(
                        context,
                        {"obj": obj, "matrix": matrix},
                        output_path,
                        proc.stdout,
                    )
                    self.report({"INFO"}, line)
                except RuntimeError as exc:
                    obj.matrix_world = matrix
                    self.report({"ERROR"}, str(exc))
                    return {"CANCELLED"}
        finally:
            tmpdir, self._tmpdir = self._tmpdir, ""
            if tmpdir and os.path.isdir(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        return {"FINISHED"}

    # -- modal path (interactive use, UI stays alive) --------------------

    def invoke(self, context: Any, event: Any) -> set[str]:
        self._params = context.scene.retopo_params
        prefs = context.preferences.addons[ADDON_ID].preferences
        self._binary = find_retopo_binary(prefs.retopo_binary)
        if not self._binary:
            self.report(
                {"ERROR"}, "retopo binary not found (see add-on preferences)"
            )
            return {"CANCELLED"}
        targets = _mesh_objects(context)
        if not targets:
            self.report({"ERROR"}, "Select at least one mesh object")
            return {"CANCELLED"}
        self._results = {}
        self._active_name = _active_mesh_name(context.view_layer)
        _pause_recall()
        self._recall_paused = True
        self._tmpdir = tempfile.mkdtemp(prefix="retopo_")
        self._queue = [
            {"obj": o, "matrix": o.matrix_world.copy()} for o in targets
        ]
        self._total = len(self._queue)
        self._proc = None
        self._current = None
        self._output_path = ""
        self._log_path = ""
        self._log_handle = None
        context.scene.retopo_last_report = ""
        wm = context.window_manager
        wm.progress_begin(0, self._total)
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        if not self._start_next(context):
            self._cleanup(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _start_next(self, context: Any) -> bool:
        """Export the next queued object and spawn its CLI subprocess."""
        self._current = self._queue.pop(0)
        obj = self._current["obj"]
        index = self._total - len(self._queue) - 1
        constraint_fn = _package_attr(
            "constraint_args_for_target", constraint_args_for_target
        )
        subproc = _package_attr("subprocess", subprocess)
        try:
            input_path = self._export_job(context, obj, index)
            extra, notes = constraint_fn(
                self._params, obj, input_path, self._tmpdir, str(index)
            )
            for note in notes:
                self.report({"INFO"}, note)
        except RuntimeError as exc:
            obj.matrix_world = self._current["matrix"]
            self.report({"ERROR"}, str(exc))
            return False
        self._output_path = os.path.join(self._tmpdir, f"out_{index}.obj")
        # Write progress to a log file rather than PIPE to avoid buffer deadlocks.
        self._log_path = os.path.join(self._tmpdir, f"run_{index}.log")
        self._log_handle = open(self._log_path, "w", encoding="utf-8")
        self._proc = subproc.Popen(
            self._params.cli_args(self._binary, input_path, self._output_path)
            + extra,
            stdout=self._log_handle,
            stderr=subproc.STDOUT,
            text=True,
        )
        context.window_manager.progress_update(index)
        return True

    def modal(self, context: Any, event: Any) -> set[str]:
        if event.type == "ESC":
            return self._cancel_modal(context)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if self._proc is None or self._proc.poll() is None:
            done = self._total - len(self._queue) - 1
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.header_text_set(
                        f"RetopoForge: remeshing {done + 1}/{self._total} "
                        f"({self._current['obj'].name}) — ESC to cancel"
                    )
                    break
            return {"PASS_THROUGH"}
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        with open(self._log_path, encoding="utf-8", errors="replace") as f:
            stdout_text = f.read()
        if self._proc.returncode != 0:
            tail = stdout_text.strip().splitlines()
            detail = tail[-1] if tail else "unknown error"
            self._current["obj"].matrix_world = self._current["matrix"]
            self.report({"ERROR"}, f"retopo failed: {detail}")
            self._cleanup(context)
            return {"CANCELLED"}
        try:
            line = self._finish_job(
                context, self._current, self._output_path, stdout_text
            )
        except RuntimeError as exc:
            self._current["obj"].matrix_world = self._current["matrix"]
            self.report({"ERROR"}, str(exc))
            self._cleanup(context)
            return {"CANCELLED"}
        self.report({"INFO"}, line)
        if self._queue:
            context.window_manager.progress_update(
                self._total - len(self._queue)
            )
            if not self._start_next(context):
                self._cleanup(context)
                return {"CANCELLED"}
            return {"PASS_THROUGH"}
        self._cleanup(context)
        return {"FINISHED"}

    def _cancel_modal(self, context: Any) -> set[str]:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        for job in [self._current] + (self._queue or []):
            if job is not None:
                job["obj"].matrix_world = job["matrix"]
        self.report({"WARNING"}, "RetopoForge remesh cancelled")
        self._cleanup(context)
        return {"CANCELLED"}


class RETOPOFORGE_OT_generate_lods(bpy.types.Operator):
    """Generate LOD rungs for the selection as <name>_lodN siblings."""

    bl_idname = "retopo.generate_lods"
    bl_label = "Generate LODs"
    bl_options = {"REGISTER", "UNDO"}

    def _adopt_rung(self, obj: Any, rung_path: str, rung_index: int) -> Any:
        imported = _import_result(rung_path)
        if not imported:
            raise RuntimeError(
                f"CLI output {rung_path} imported no mesh objects"
            )
        rung = imported[0]
        rung.name = f"{obj.name}_lod{rung_index}"
        rung.data.name = rung.name
        obj_colls = set(obj.users_collection)
        for coll in obj_colls:
            if rung.name not in coll.objects:
                coll.objects.link(rung)
        for coll in list(rung.users_collection):
            if coll not in obj_colls:
                coll.objects.unlink(rung)
        rung.matrix_world = obj.matrix_world
        rung.select_set(False)
        for temp in imported[1:]:
            bpy.data.objects.remove(temp, do_unlink=True)
        return rung

    def execute(self, context: Any) -> set[str]:
        with _recall_paused(context):
            return self._execute(context)

    def _execute(self, context: Any) -> set[str]:
        params = context.scene.retopo_params
        prefs = context.preferences.addons[ADDON_ID].preferences
        binary = find_retopo_binary(prefs.retopo_binary)
        if not binary:
            self.report(
                {"ERROR"}, "retopo binary not found (see add-on preferences)"
            )
            return {"CANCELLED"}
        targets = _mesh_objects(context)
        if not targets:
            self.report({"ERROR"}, "Select at least one mesh object")
            return {"CANCELLED"}
        try:
            rung_targets = parse_lod_targets(params.lod_targets)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        tmpdir = tempfile.mkdtemp(prefix="retopo_lods_")
        context.scene.retopo_last_report = ""
        subproc = _package_attr("subprocess", subprocess)
        try:
            for index, obj in enumerate(targets):
                matrix = obj.matrix_world.copy()
                try:
                    _validate_nonempty_mesh(
                        context, obj, params.apply_modifiers, "generate LODs"
                    )
                    input_path = os.path.join(tmpdir, f"in_{index}.obj")
                    _export_obj_identity(
                        context, obj, input_path, params.apply_modifiers
                    )
                    output_base = os.path.join(tmpdir, f"out_{index}.obj")
                    args = params.cli_args(binary, input_path, output_base)
                    args += ["--lods", ",".join(str(t) for t in rung_targets)]
                    proc = subproc.run(args, capture_output=True, text=True)
                    if proc.returncode != 0:
                        tail = (proc.stderr or "").strip().splitlines()
                        detail = tail[-1] if tail else "unknown error"
                        raise RuntimeError(f"retopo failed: {detail}")
                    stem = os.path.splitext(os.path.basename(output_base))[0]
                    rung_paths = [
                        os.path.join(tmpdir, f"{stem}_lod{r}.obj")
                        for r in range(len(rung_targets))
                    ]
                    missing = [p for p in rung_paths if not os.path.isfile(p)]
                    if missing:
                        raise RuntimeError(
                            "retopo produced no output for "
                            + ", ".join(os.path.basename(p) for p in missing)
                        )
                    names = [
                        self._adopt_rung(obj, p, r).name
                        for r, p in enumerate(rung_paths)
                    ]
                    obj.matrix_world = matrix
                    counts = parse_lod_rung_counts(proc.stdout)
                    bits = []
                    for r, name in enumerate(names):
                        if r in counts:
                            quads, non_quads = counts[r]
                            bits.append(f"{name}={quads}q+{non_quads}nq")
                        else:
                            bits.append(name)
                    line = f"{obj.name}: " + ", ".join(bits)
                    context.scene.retopo_last_report += line + "\n"
                    save_recall_entry(context, obj.name, params)
                    self.report({"INFO"}, line)
                except RuntimeError as exc:
                    obj.matrix_world = matrix
                    self.report({"ERROR"}, str(exc))
                    return {"CANCELLED"}
            for o in context.selected_objects:
                o.select_set(False)
            for obj in targets:
                obj.select_set(True)
            context.view_layer.objects.active = targets[0]
        finally:
            if os.path.isdir(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        return {"FINISHED"}


class RETOPOFORGE_OT_remesh_and_bake(bpy.types.Operator):
    """Remesh the active HIGH object, then Smart-UV + bake all PBR maps
    to the result in one action"""

    bl_idname = "retopo.remesh_and_bake"
    bl_label = "Remesh + Bake All"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: Any) -> set[str]:
        with _recall_paused(context):
            return self._execute(context)

    def _execute(self, context: Any) -> set[str]:
        high = context.active_object
        if high is None or high.type != "MESH":
            self.report({"ERROR"}, "Make the HIGH-poly mesh the active object")
            return {"CANCELLED"}
        before = set(bpy.data.objects)
        for o in context.selected_objects:
            o.select_set(False)
        high.select_set(True)
        context.view_layer.objects.active = high
        result = bpy.ops.retopo.remesh(force_keep_original=True)
        if "FINISHED" not in result:
            self.report({"ERROR"}, "Remesh leg failed, bake skipped")
            return {"CANCELLED"}
        fresh = [
            o for o in bpy.data.objects if o not in before and o.type == "MESH"
        ]
        if not fresh:
            self.report({"ERROR"}, "Remesh produced no new mesh object")
            return {"CANCELLED"}
        low = fresh[0]
        high.hide_viewport = False
        for o in context.selected_objects:
            o.select_set(False)
        low.select_set(True)
        high.select_set(True)
        context.view_layer.objects.active = high
        result = bpy.ops.retopo.bake_textures(ignore_cage=True)
        if "FINISHED" not in result:
            self.report({"ERROR"}, "Bake leg failed")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Remeshed + baked '{high.name}'")
        return {"FINISHED"}


def _export_chain_file(
    operator: bpy.types.Operator,
    context: Any,
    tracer: Callable[[Any], list[list[int]]],
    writer: Callable[[Any, list[list[int]], str], int],
    empty_msg: str,
    kind_label: str,
) -> set[str]:
    obj = context.view_layer.objects.active
    if obj is None or obj.type != "MESH":
        operator.report({"ERROR"}, "Make a mesh object the active object")
        return {"CANCELLED"}
    chains = tracer(obj.data)
    if not chains:
        operator.report({"ERROR"}, empty_msg.format(name=obj.name))
        return {"CANCELLED"}
    if not operator.filepath:
        operator.report({"ERROR"}, "No output file given")
        return {"CANCELLED"}
    path = bpy.path.abspath(operator.filepath)
    count = writer(obj, chains, path)
    points = sum(len(c) for c in chains)
    operator.report(
        {"INFO"}, f"Exported {count} {kind_label} ({points} points) to {path}"
    )
    return {"FINISHED"}


class RETOPOFORGE_OT_export_guides(bpy.types.Operator):
    """Export the active object's edge selection as a --guides file"""

    bl_idname = "retopo.export_guides"
    bl_label = "Export Guide Strokes"
    bl_options = {"REGISTER"}

    filepath: StringProperty(
        name="Guides File",
        description="Where to write the --guides polyline file",
        default="",
        subtype="FILE_PATH",
    )

    def invoke(self, context: Any, event: Any) -> set[str]:
        if not self.filepath:
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return self.execute(context)

    def execute(self, context: Any) -> set[str]:
        return _export_chain_file(
            self,
            context,
            trace_edge_chains,
            write_guide_chains,
            "No edge selection on '{name}' (select flow-stroke edges in Edit Mode first)",
            "guide polylines",
        )


class RETOPOFORGE_OT_export_features(bpy.types.Operator):
    """Export the active object's sharp-marked edges as a --features file"""

    bl_idname = "retopo.export_features"
    bl_label = "Export Sharp Features"
    bl_options = {"REGISTER"}

    filepath: StringProperty(
        name="Features File",
        description="Where to write the --features polyline file",
        default="",
        subtype="FILE_PATH",
    )

    def invoke(self, context: Any, event: Any) -> set[str]:
        if not self.filepath:
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return self.execute(context)

    def execute(self, context: Any) -> set[str]:
        return _export_chain_file(
            self,
            context,
            trace_sharp_chains,
            write_feature_chains,
            "No sharp-marked edges on '{name}' (mark crisp edges with Edge > Mark Sharp first)",
            "sharp-feature polylines",
        )


class RETOPOFORGE_OT_export_density(bpy.types.Operator):
    """Export the active object's density vertex group as a --density file"""

    bl_idname = "retopo.export_density"
    bl_label = "Export Density Mask"
    bl_options = {"REGISTER"}

    filepath: StringProperty(
        name="Density File",
        description="Where to write the --density multiplier file",
        default="",
        subtype="FILE_PATH",
    )

    def invoke(self, context: Any, event: Any) -> set[str]:
        if not self.filepath:
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return self.execute(context)

    def execute(self, context: Any) -> set[str]:
        obj = context.view_layer.objects.active
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Make a mesh object the active object")
            return {"CANCELLED"}
        if len(obj.data.vertices) == 0:
            self.report({"ERROR"}, f"Object '{obj.name}' has no vertices")
            return {"CANCELLED"}
        params = context.scene.retopo_params
        group_name = (params.density_vertex_group or "").strip()
        if not group_name:
            self.report(
                {"ERROR"}, "No vertex group set (pick one in the Density panel)"
            )
            return {"CANCELLED"}
        if not self.filepath:
            self.report({"ERROR"}, "No output file given")
            return {"CANCELLED"}
        try:
            multipliers = density_multipliers(
                obj,
                group_name,
                float(params.density_min),
                float(params.density_max),
            )
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        path = bpy.path.abspath(self.filepath)
        count = write_density_multipliers(obj, group_name, multipliers, path)
        self.report(
            {"INFO"},
            f"Exported {count} density multipliers (group '{group_name}') to {path}",
        )
        return {"FINISHED"}


class RETOPOFORGE_OT_reload(bpy.types.Operator):
    """Reload all scripts (picks up extension updates), then confirm"""

    bl_idname = "retopo.reload_scripts"
    bl_label = "Reload Scripts"

    def execute(self, context: Any) -> set[str]:
        bpy.ops.script.reload()
        self.report({"INFO"}, "Scripts reloaded")
        return {"FINISHED"}
