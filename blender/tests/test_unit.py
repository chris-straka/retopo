# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python unit tests for RetopoForge add-on helpers (no Blender required)."""

import math
import os
import sys
import tempfile
import types
import unittest

BLENDER_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if BLENDER_DIR not in sys.path:
    sys.path.insert(0, BLENDER_DIR)

import retopo  # noqa: E402
from retopo import bake, cli, constraints  # noqa: E402


class CliHelpersTest(unittest.TestCase):
    def test_parse_summary_valid_and_missing(self):
        report = (
            "=== retopo Report ===\n"
            "Quads: 4980\n"
            "Non-quads: 12\n"
            "Vertices: 5001\n"
            "Time: 1.25 seconds\n"
        )
        self.assertEqual(cli.parse_summary(report), (4980, 12, 5001, 1.25))
        self.assertIsNone(cli.parse_summary(""))
        self.assertIsNone(cli.parse_summary(None))

    def test_parse_lod_targets_validation(self):
        self.assertEqual(cli.parse_lod_targets("10000, 5000,2000"), [10000, 5000, 2000])
        for bad in ("", "0", "-5", "100,", ",100", "100,,50", "100,abc"):
            with self.assertRaises(ValueError):
                cli.parse_lod_targets(bad)

    def test_parse_lod_rung_counts(self):
        stdout = (
            "LOD 0: target-quads=10000 output=/tmp/x_lod0.obj quads=9051 non-quads=3 vertices=9100 time=1.2 seconds\n"
            "LOD 1: target-quads=5000 output=/tmp/x_lod1.obj quads=4900 non-quads=0 vertices=4910 time=0.8 seconds\n"
        )
        self.assertEqual(
            cli.parse_lod_rung_counts(stdout),
            {0: (9051, 3), 1: (4900, 0)},
        )

    def test_find_retopo_binary_explicit_missing_does_not_fallback(self):
        self.assertEqual(
            cli.find_retopo_binary("/definitely/missing/retopo_bin_xyz"), ""
        )

    def test_find_retopo_binary_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = os.path.join(tmp, "retopo")
            with open(fake_bin, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(fake_bin, 0o755)
            prev = os.environ.get("RETOPO_BIN")
            os.environ["RETOPO_BIN"] = fake_bin
            try:
                self.assertEqual(cli.find_retopo_binary(""), fake_bin)
            finally:
                if prev is None:
                    os.environ.pop("RETOPO_BIN", None)
                else:
                    os.environ["RETOPO_BIN"] = prev


class ConstraintHelpersTest(unittest.TestCase):
    def test_joint_bend_degrees_roles(self):
        self.assertEqual(constraints.joint_bend_degrees("DEF-thigh.L"), 90.0)
        self.assertEqual(constraints.joint_bend_degrees("mixamorig:LeftForeArm"), 140.0)
        self.assertEqual(constraints.joint_bend_degrees("custom_bone"), constraints.GENERIC_BEND_DEGREES)

    def test_trace_edge_and_sharp_chains_deterministic(self):
        edges = [
            types.SimpleNamespace(index=0, vertices=(0, 1), select=True, use_edge_sharp=False),
            types.SimpleNamespace(index=1, vertices=(1, 2), select=True, use_edge_sharp=True),
            types.SimpleNamespace(index=2, vertices=(2, 3), select=True, use_edge_sharp=True),
        ]
        mesh = types.SimpleNamespace(edges=edges)
        self.assertEqual(constraints.trace_edge_chains(mesh), [[0, 1, 2, 3]])
        self.assertEqual(constraints.trace_sharp_chains(mesh), [[1, 2, 3]])

    def test_reexports_available_on_package(self):
        for name in (
            "find_retopo_binary",
            "parse_summary",
            "parse_lod_targets",
            "joint_bend_degrees",
            "GENERIC_BEND_DEGREES",
            "constraint_args_for_target",
            "collect_guide_polylines",
            "write_guide_polylines",
            "subprocess",
        ):
            self.assertTrue(hasattr(retopo, name), f"retopo.{name} missing")


class BakeScaleHelpersTest(unittest.TestCase):
    def _params(self, **overrides):
        base = dict(
            bake_extrusion_auto=True,
            bake_extrusion_scale=bake.DEFAULT_BAKE_EXTRUSION_SCALE,
            bake_extrusion=0.05,
            bake_ray_distance=0.0,
        )
        base.update(overrides)
        return types.SimpleNamespace(**base)

    def test_auto_rays_scale_linearly_with_world_diagonal(self):
        small = bake.compute_auto_bake_rays(0.02)
        unit = bake.compute_auto_bake_rays(2.0)
        large = bake.compute_auto_bake_rays(200.0)
        self.assertAlmostEqual(unit[0] / small[0], 100.0, places=6)
        self.assertAlmostEqual(large[0] / unit[0], 100.0, places=6)
        self.assertAlmostEqual(
            unit[1], unit[0] * bake.DEFAULT_BAKE_RAY_MULTIPLIER, places=9
        )
        self.assertEqual(bake.compute_auto_bake_rays(0.0), (0.0, 0.0))

    def test_resolve_bake_ray_settings_auto_and_manual_overrides(self):
        def box_obj(size):
            half = size * 0.5
            bb = [
                (-half, -half, -half),
                (-half, -half, half),
                (-half, half, half),
                (-half, half, -half),
                (half, -half, -half),
                (half, -half, half),
                (half, half, half),
                (half, half, -half),
            ]
            return types.SimpleNamespace(bound_box=bb, matrix_world=None, data=None)

        high = box_obj(100.0)
        low = box_obj(98.0)
        extr, ray, label = bake.resolve_bake_ray_settings(
            self._params(), high, low
        )
        expected_diag = math.sqrt(3.0) * 98.0
        self.assertAlmostEqual(
            extr, expected_diag * bake.DEFAULT_BAKE_EXTRUSION_SCALE, places=5
        )
        self.assertAlmostEqual(
            ray, extr * bake.DEFAULT_BAKE_RAY_MULTIPLIER, places=5
        )
        self.assertIn("auto", label)

        # Explicit ray distance overrides auto ray cap while keeping auto extrusion.
        extr2, ray2, _ = bake.resolve_bake_ray_settings(
            self._params(bake_ray_distance=0.75), high, low
        )
        self.assertAlmostEqual(extr2, extr, places=6)
        self.assertAlmostEqual(ray2, 0.75, places=6)

        # Manual override preserves exact units (including 0.0 extrusion).
        extr3, ray3, label3 = bake.resolve_bake_ray_settings(
            self._params(
                bake_extrusion_auto=False,
                bake_extrusion=0.0,
                bake_ray_distance=0.25,
            ),
            high,
            low,
        )
        self.assertEqual((extr3, ray3, label3), (0.0, 0.25, "manual"))

        # Custom cage disables auto extrusion offset unless manually overridden.
        cage = types.SimpleNamespace(name="MyCage")
        self.assertEqual(
            bake.resolve_bake_ray_settings(self._params(), high, low, cage=cage),
            (0.0, 0.0, "cage 'MyCage'"),
        )
        # Degenerate zero-size mesh falls back to manual settings.
        empty = types.SimpleNamespace(bound_box=[], matrix_world=None, data=None)
        self.assertEqual(
            bake.resolve_bake_ray_settings(self._params(), empty, empty),
            (0.05, 0.0, "manual"),
        )


class SurfaceGuideHelpersTest(unittest.TestCase):
    def test_target_edge_length_scales_with_area_and_quad_count(self):
        h1 = constraints.target_edge_length(100.0, 100)
        h4 = constraints.target_edge_length(100.0, 400)
        self.assertAlmostEqual(h1, 1.0, places=9)
        self.assertAlmostEqual(h4, 0.5, places=9)
        self.assertEqual(constraints.target_edge_length(0.0, 100), 0.0)

    def test_resample_and_simplify_polyline(self):
        pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        res = constraints.resample_polyline(pts, 0.5)
        self.assertEqual(len(res), 5)
        self.assertEqual(res[0], (0.0, 0.0, 0.0))
        self.assertEqual(res[-1], (2.0, 0.0, 0.0))
        # Collinear intermediate points collapse down to endpoints.
        simp = constraints.simplify_polyline(res, 0.01)
        self.assertEqual(simp, [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0)])

        # Closed loop keeps shape instead of collapsing to 2 identical endpoints.
        ring = [
            (math.cos(2 * math.pi * k / 16), math.sin(2 * math.pi * k / 16), 0.0)
            for k in range(16)
        ]
        ring.append(ring[0])
        simp_ring = constraints.simplify_polyline(ring, 0.05)
        self.assertGreaterEqual(len(simp_ring), 6)
        self.assertEqual(simp_ring[0], simp_ring[-1])

    def test_convert_stroke_projects_splits_gaps_and_drops_stubs(self):
        # Analytic sphere radius 1.0; stroke sweeps on surface, jumps far away
        # in the middle, returns for a good arc, plus a short stub at the end.
        def sphere_nearest(p):
            r = math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)
            if r <= 1e-12:
                return (1.0, 0.0, 0.0), 1.0
            hit = (p[0] / r, p[1] / r, p[2] / r)
            return hit, abs(r - 1.0)

        edge_len = 0.2
        stroke = [
            (1.0, 0.0, 0.0),
            (0.9, 0.45, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 5.0, 0.0),  # far off-surface spike -> dropped (> 3 * 0.2)
            (-0.7, 0.7, 0.0),
            (-1.0, 0.0, 0.0),
        ]
        pieces = constraints.convert_stroke_points(stroke, edge_len, sphere_nearest)
        self.assertEqual(len(pieces), 2)
        for poly in pieces:
            self.assertGreaterEqual(len(poly), 2)
            for p in poly:
                r = math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)
                self.assertAlmostEqual(r, 1.0, places=5)

        # Very short stub (< 0.5 * edge_len) is dropped.
        stub = [(1.0, 0.0, 0.0), (1.0, 0.05, 0.0)]
        self.assertEqual(
            constraints.convert_stroke_points(stub, edge_len, sphere_nearest), []
        )

    def test_symmetry_detection_and_mirror_polyline(self):
        # Symmetric in Y only (elongated in X, shifted).
        verts = []
        for x in (-1.0, 0.0, 2.0):
            for y in (-2.0, 2.0):
                for z in (0.0, 1.0):
                    verts.append((x, y, z))
        axis, offset, score = constraints.detect_symmetry_plane(verts)
        self.assertEqual(axis, 1)
        self.assertAlmostEqual(offset, 0.0, places=6)
        self.assertAlmostEqual(score, 1.0, places=6)

        # Asymmetric cloud falls back to X around bbox center.
        asym = [(0.0, 0.0, 0.0), (4.0, 1.0, 0.0), (1.0, 3.0, 2.0)]
        axis_a, offset_a, _ = constraints.detect_symmetry_plane(asym)
        self.assertEqual(axis_a, 0)
        self.assertAlmostEqual(offset_a, 2.0, places=6)

        mir = constraints.mirror_polyline(
            [(0.5, 1.0, 0.0), (0.5, 1.0, 1.0)], 0, 0.0, plane_tolerance=0.1
        )
        self.assertEqual(mir, [(-0.5, 1.0, 0.0), (-0.5, 1.0, 1.0)])
        on_plane = constraints.mirror_polyline(
            [(0.02, 1.0, 0.0), (-0.01, 1.0, 1.0)], 0, 0.0, plane_tolerance=0.1
        )
        self.assertIsNone(on_plane)

    def test_write_guide_polylines_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "guides.txt")
            n = constraints.write_guide_polylines(
                "Hero",
                [[(0.0, 1.0, 2.0), (0.5, 1.0, 2.0)], [(1.0, 0.0, 0.0), (1.0, 1.0, 0.0)]],
                out,
            )
            self.assertEqual(n, 2)
            with open(out, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("Hero", text)
            self.assertIn("\n\n", text)
            self.assertIn("0.0 1.0 2.0", text)


if __name__ == "__main__":
    unittest.main()
