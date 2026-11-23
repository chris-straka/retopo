#!/usr/bin/env python3
"""Gate-first RED -> GREEN tests for hole_repair.py and cut.py edge cases."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hole_repair as hr  # noqa: E402


def find_blender():
    env = os.environ.get("BLENDER")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    for cand in ("/Applications/Blender.app/Contents/MacOS/Blender", os.path.expanduser("~/.local/bin/blender")):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return shutil.which("blender")


class HoleRepairEdgeCasesTest(unittest.TestCase):
    def test_malformed_short_vertex_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "short_v.obj")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("v 1.0 2.0\nf 1 1 1\n")
            with self.assertRaises(ValueError):
                hr._read_obj(p)

    def test_nan_vertex_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "nan_v.obj")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
            with self.assertRaises(ValueError):
                hr._read_obj(p)

    def test_zero_or_oob_face_index_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (
                ("zero_idx.obj", "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 1 2\n"),
                ("oob_idx.obj", "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9\n"),
            ):
                p = os.path.join(tmp, name)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(body)
                with self.assertRaises(ValueError, msg=name):
                    hr._read_obj(p)


class CutEdgeCasesTest(unittest.TestCase):
    def _run_cut(self, args):
        bl = find_blender()
        if not bl:
            self.skipTest("Blender not installed on this machine")
        cut_py = os.path.join(HERE, "cut.py")
        cmd = [bl, "-b", "--factory-startup", "--python-exit-code", "1", "--python", cut_py, "--"] + args
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def test_empty_obj_fails_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_obj = os.path.join(tmp, "empty.obj")
            out_obj = os.path.join(tmp, "out.obj")
            with open(empty_obj, "w", encoding="utf-8") as fh:
                fh.write("# empty\n")
            p = self._run_cut(["decimate", empty_obj, out_obj, "100"])
            self.assertNotEqual(p.returncode, 0)
            combined = p.stdout + "\n" + p.stderr
            self.assertNotIn("IndexError", combined, combined)
            self.assertIn("cut:", combined, combined)

    def test_zero_dimension_quadriflow_fails_without_zero_division(self):
        with tempfile.TemporaryDirectory() as tmp:
            zero_obj = os.path.join(tmp, "zero.obj")
            out_obj = os.path.join(tmp, "out.obj")
            with open(zero_obj, "w", encoding="utf-8") as fh:
                fh.write("v 0 0 0\nv 0 0 0\nv 0 0 0\nf 1 2 3\n")
            p = self._run_cut(["quadriflow", zero_obj, out_obj, "100"])
            self.assertNotEqual(p.returncode, 0)
            combined = p.stdout + "\n" + p.stderr
            self.assertNotIn("ZeroDivisionError", combined, combined)
            self.assertIn("cut:", combined, combined)

    def test_invalid_tris_arg_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._run_cut(["decimate", os.path.join(tmp, "in.obj"), os.path.join(tmp, "out.obj"), "-5"])
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("cut:", p.stdout + "\n" + p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
