#!/usr/bin/env python3
"""Unit tests for bench/score.py, bench/noise.py, and bench/run.py (stdlib only).

Run:  python3 bench/test_harness.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import noise  # noqa: E402
import run  # noqa: E402
import score  # noqa: E402


CUBE_OBJ = (
    "v 0 0 0\n"
    "v 1 0 0\n"
    "v 1 1 0\n"
    "v 0 1 0\n"
    "v 0 0 1\n"
    "v 1 0 1\n"
    "v 1 1 1\n"
    "v 0 1 1\n"
    "f 1 4 3 2\n"
    "f 5 6 7 8\n"
    "f 1 2 6 5\n"
    "f 2 3 7 6\n"
    "f 3 4 8 7\n"
    "f 4 1 5 8\n"
)


class ScoreTests(unittest.TestCase):
    def test_load_obj_resolves_negative_indices_and_rejects_bad_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            neg = os.path.join(tmp, "neg.obj")
            with open(neg, "w", encoding="utf-8") as f:
                f.write(
                    "v 0 0 0\nv 1 0 0\nv 0 1 0\n"
                    "f -3/1/1 -2/2/2 -1/3/3\n"
                    "v 0 0 1\nv 1 0 1\nv 0 1 1\n"
                    "f -3 -2 -1\n"
                )
            verts, faces = score.load_obj(neg)
            self.assertEqual(len(verts), 6)
            self.assertEqual(faces, [[0, 1, 2], [3, 4, 5]])

            for body in (
                "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 1 2\n",
                "v 0 0 0\nv 1 0 0\nv 0 1 0\nf -4 -2 -1\n",
                "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9\n",
                "v 0 0\nf 1 1 1\n",
                "v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
                "v 0 0 0\nv 1 0 0\nf 1 2\n",
            ):
                bad = os.path.join(tmp, "bad.obj")
                with open(bad, "w", encoding="utf-8") as f:
                    f.write(body)
                with self.assertRaises(ValueError):
                    score.load_obj(bad)

    def test_score_identical_cube_and_error_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "cube.obj")
            empty = os.path.join(tmp, "empty.obj")
            zero = os.path.join(tmp, "zero.obj")
            with open(src, "w", encoding="utf-8") as f:
                f.write(CUBE_OBJ)
            with open(empty, "w", encoding="utf-8") as f:
                f.write("# empty\n")
            with open(zero, "w", encoding="utf-8") as f:
                f.write("v 0 0 0\nv 0 0 0\nv 0 0 0\nf 1 2 3\n")

            m = score.score(src, src, target=6)
            self.assertEqual(m["quads"], 6)
            self.assertEqual(m["non_quads"], 0)
            self.assertEqual(m["verts"], 8)
            self.assertAlmostEqual(m["yield"], 1.0)
            self.assertAlmostEqual(m["dist_mean"], 0.0)

            self.assertTrue(score.score(src, empty)["empty"])
            with self.assertRaises(ValueError):
                score.score(empty, src)
            with self.assertRaises(ValueError):
                score.score(zero, src)

            r_ok = subprocess.run(
                [sys.executable, os.path.join(HERE, "score.py"),
                 "--source", src, "--output", src, "--target", "6", "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(r_ok.returncode, 0)
            self.assertEqual(json.loads(r_ok.stdout)["quads"], 6)

            r_empty = subprocess.run(
                [sys.executable, os.path.join(HERE, "score.py"),
                 "--source", src, "--output", empty],
                capture_output=True, text=True,
            )
            self.assertEqual(r_empty.returncode, 1)

            for args in (
                ["--source", os.path.join(tmp, "missing.obj"), "--output", src],
                ["--source", src, "--output", os.path.join(tmp, "missing.obj")],
                ["--source", src, "--output", src, "--target", "0"],
                ["--source", zero, "--output", src],
            ):
                r = subprocess.run(
                    [sys.executable, os.path.join(HERE, "score.py")] + args,
                    capture_output=True, text=True,
                )
                self.assertEqual(r.returncode, 2, args)
                self.assertNotIn("Traceback", r.stderr)


class NoiseTests(unittest.TestCase):
    def test_noise_cli_error_exits_and_stub_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = os.path.join(tmp, "cube.obj")
            with open(model, "w", encoding="utf-8") as f:
                f.write(CUBE_OBJ)

            ok_bin = os.path.join(tmp, "ok_bin.py")
            with open(ok_bin, "w", encoding="utf-8") as f:
                f.write(
                    f"#!{sys.executable}\n"
                    "import shutil, sys\n"
                    "args = sys.argv[1:]\n"
                    "shutil.copyfile(args[args.index('--input') + 1], "
                    "args[args.index('--output') + 1])\n"
                )
            os.chmod(ok_bin, 0o755)

            fail_bin = os.path.join(tmp, "fail_bin.py")
            with open(fail_bin, "w", encoding="utf-8") as f:
                f.write(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")
            os.chmod(fail_bin, 0o755)

            out_json = os.path.join(tmp, "noise.json")
            r_ok = subprocess.run(
                [sys.executable, os.path.join(HERE, "noise.py"),
                 "--binary", ok_bin, "--models", model, "--targets", "6",
                 "--seeds", "2", "--json", out_json],
                capture_output=True, text=True,
            )
            self.assertEqual(r_ok.returncode, 0, r_ok.stdout + r_ok.stderr)
            with open(out_json, encoding="utf-8") as f:
                rep = json.load(f)
            self.assertEqual(rep["cases"]["cube.obj@6"]["summary"]["metrics"]["quads"]["median"], 6)

            fail_json = os.path.join(tmp, "fail.json")
            r_fail = subprocess.run(
                [sys.executable, os.path.join(HERE, "noise.py"),
                 "--binary", fail_bin, "--models", model, "--targets", "6",
                 "--seeds", "2", "--json", fail_json],
                capture_output=True, text=True,
            )
            self.assertEqual(r_fail.returncode, 1)
            with open(fail_json, encoding="utf-8") as f:
                frep = json.load(f)
            self.assertTrue(all(row["failed"] for row in frep["cases"]["cube.obj@6"]["rows"]))

            for bad_args in (
                ["--binary", os.path.join(tmp, "missing_bin"), "--models", model],
                ["--binary", ok_bin, "--models", os.path.join(tmp, "missing.obj")],
                ["--binary", ok_bin, "--models", model, "--seeds", "0"],
                ["--binary", ok_bin, "--models", model, "--targets", "0"],
                ["--binary", ok_bin, "--models", model, "--targets", "bad"],
            ):
                r = subprocess.run(
                    [sys.executable, os.path.join(HERE, "noise.py")] + bad_args,
                    capture_output=True, text=True,
                )
                self.assertEqual(r.returncode, 2, bad_args)
                self.assertNotIn("Traceback", r.stderr)

    def test_write_jittered_preserves_welded_vertices(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "jit.obj")
            verts = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
            faces = [[0, 1, 2]]
            noise.write_jittered(verts, faces, p, seed=3, diag=2.0)
            jv, jf = score.load_obj(p)
            self.assertEqual(jv[0], jv[1])
            self.assertEqual(jf, faces)


class RunTests(unittest.TestCase):
    def test_validate_obj_catches_errors_and_counts_quads(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.obj")
            bad = os.path.join(tmp, "bad.obj")
            with open(good, "w", encoding="utf-8") as f:
                f.write(CUBE_OBJ)
            with open(bad, "w", encoding="utf-8") as f:
                f.write(
                    "v 0 0 0\nv 1 0 0\nv 0 1 0\n"
                    "f 1 2\nf 1 2 9\nf 1 1 2\nf x y z\n"
                )
            gm = run.validate_obj(good)
            self.assertEqual((gm["verts"], gm["quads"], gm["non_quads"], gm["error_count"]), (8, 6, 0, 0))
            bm = run.validate_obj(bad)
            self.assertEqual(bm["error_count"], 4)

    def test_run_check_fails_when_a_required_model_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dummy_bin = os.path.join(tmp, "retopo")
            with open(dummy_bin, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(dummy_bin, 0o755)
            models_dir = os.path.join(tmp, "models")
            os.makedirs(models_dir)
            with open(os.path.join(models_dir, run.MODELS[0]), "w", encoding="utf-8") as f:
                f.write(CUBE_OBJ)
            ok_base = os.path.join(tmp, "base.json")
            with open(ok_base, "w", encoding="utf-8") as f:
                json.dump({"results": []}, f)
            orig_models_dir = run.MODELS_DIR
            try:
                run.MODELS_DIR = models_dir
                rc = run.main(["--binary", dummy_bin, "--check", ok_base, "--no-deform"])
                self.assertEqual(rc, 2)
            finally:
                run.MODELS_DIR = orig_models_dir

    def test_run_cli_validates_args_and_baselines_up_front(self):
        with tempfile.TemporaryDirectory() as tmp:
            dummy_bin = os.path.join(tmp, "retopo")
            with open(dummy_bin, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(dummy_bin, 0o755)
            bad_json = os.path.join(tmp, "bad.json")
            with open(bad_json, "w", encoding="utf-8") as f:
                f.write("{not json")
            ok_base = os.path.join(tmp, "base.json")
            with open(ok_base, "w", encoding="utf-8") as f:
                json.dump({"results": []}, f)

            r_help = subprocess.run(
                [sys.executable, os.path.join(HERE, "run.py"), "--help"],
                capture_output=True, text=True,
            )
            self.assertEqual(r_help.returncode, 0)

            for args in (
                ["--binary"],
                ["--check"],
                ["--deform-baseline"],
                ["--binary", os.path.join(tmp, "missing")],
                ["--binary", dummy_bin, "--check", os.path.join(tmp, "missing.json")],
                ["--binary", dummy_bin, "--check", bad_json],
                ["--binary", dummy_bin, "--check", ok_base,
                 "--deform-baseline", os.path.join(tmp, "missing_def.json")],
            ):
                r = subprocess.run(
                    [sys.executable, os.path.join(HERE, "run.py")] + args,
                    capture_output=True, text=True,
                )
                self.assertEqual(r.returncode, 2, f"{args}: {r.stdout}\n{r.stderr}")
                self.assertNotIn("Traceback", r.stderr)

    def test_committed_platform_baselines_self_check_cleanly(self):
        for base_name, def_name in (
            ("baseline.json", "deform_baseline.json"),
            ("baseline-linux.json", "deform_baseline-linux.json"),
        ):
            with open(os.path.join(HERE, base_name), encoding="utf-8") as f:
                base = json.load(f)
            with open(os.path.join(HERE, def_name), encoding="utf-8") as f:
                dbase = json.load(f)
            self.assertEqual(len(base["results"]), 12, base_name)
            failures = run.check_regressions(base["results"], base, dbase)
            self.assertEqual(failures, [], f"{base_name}: {failures}")

        # Verify regression gating catches quad drops and deform flip jumps.
        with open(os.path.join(HERE, "baseline-linux.json"), encoding="utf-8") as f:
            linux_base = json.load(f)
        with open(os.path.join(HERE, "deform_baseline-linux.json"), encoding="utf-8") as f:
            linux_def = json.load(f)
        mutated = json.loads(json.dumps(linux_base["results"]))
        mutated[0]["quads"] = int(mutated[0]["quads"] * 0.8)
        mutated[-1]["deform"]["elbow90"]["flips"] = 1
        failures = run.check_regressions(mutated, linux_base, linux_def)
        self.assertEqual(len(failures), 2, failures)


if __name__ == "__main__":
    unittest.main()
