# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python unit tests for RetopoForge add-on helpers (no Blender required)."""

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
from retopo import cli, constraints  # noqa: E402


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
            "subprocess",
        ):
            self.assertTrue(hasattr(retopo, name), f"retopo.{name} missing")


if __name__ == "__main__":
    unittest.main()
