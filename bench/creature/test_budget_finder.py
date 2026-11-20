#!/usr/bin/env python3
"""Unit tests for the triangle budget finder (`retopo-7`, stdlib only)."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import blind  # noqa: E402
import budget_finder as bf  # noqa: E402


def solid_png(path, w, h, color):
    rgb = bytearray(bytes(color) * (w * h))
    for x in range(min(w, h)):
        o = (x * w + x) * 3
        rgb[o] = (color[0] + x * 5) & 0xFF
        rgb[o + 1] = (color[1] + x * 11) & 0xFF
        rgb[o + 2] = (color[2] + x * 17) & 0xFF
    blind.write_png(path, w, h, rgb)


class BudgetFinderSelectionTest(unittest.TestCase):
    def test_selects_smallest_budget_within_decimate_noise(self):
        ref_vs_dense = {"mean_sil_diff_pct": 0.40, "mean_shade_rmse": 1.20}
        ref_vs_nbr = {"mean_sil_diff_pct": 0.15, "mean_shade_rmse": 0.35}
        ceiling = bf.compute_noise_budget(ref_vs_dense, ref_vs_nbr)
        self.assertAlmostEqual(ceiling["max_sil_diff_pct"], 0.55)
        self.assertAlmostEqual(ceiling["max_shade_rmse"], 1.55)

        probes = [
            {"target_tris": 2000, "actual_tris": 2010, "metrics": {"mean_sil_diff_pct": 1.40, "mean_shade_rmse": 3.10}},
            {"target_tris": 4500, "actual_tris": 4490, "metrics": {"mean_sil_diff_pct": 0.60, "mean_shade_rmse": 1.50}},
            {"target_tris": 6000, "actual_tris": 6004, "metrics": {"mean_sil_diff_pct": 0.52, "mean_shade_rmse": 1.48}},
            {"target_tris": 10000, "actual_tris": 9998, "metrics": {"mean_sil_diff_pct": 0.44, "mean_shade_rmse": 1.28}},
            {"target_tris": 18000, "actual_tris": 18000, "metrics": ref_vs_dense},
        ]
        sel = bf.select_smallest_budget(probes, ceiling, ref_tris=18000)
        self.assertTrue(sel["within_noise"])
        self.assertEqual(sel["found_target_tris"], 6000)
        self.assertAlmostEqual(sel["reduction_pct"], 66.64, places=1)

    def test_blind_pack_and_score_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            for i in range(1, 4):
                sd = os.path.join(tmp, f"creature_{i}")
                os.makedirs(sd)
                f_found = os.path.join(sd, "frame_found.png")
                f_18k = os.path.join(sd, "frame_18k.png")
                f_pl = os.path.join(sd, "frame_planted.png")
                solid_png(f_found, 64, 72, (100, 120, 140))
                solid_png(f_18k, 64, 72, (100, 120, 140))
                solid_png(f_pl, 64, 72, (220, 30, 30))
                rows.append({
                    "anon": f"creature_{i}",
                    "selection": {"found_actual_tris": 6000},
                    "frame_found": f_found,
                    "frame_18k": f_18k,
                    "frame_planted": f_pl,
                })

            pack = bf.build_blind_pack(tmp, rows, seed=42)
            # Answer key must sit beside blind/, never inside it.
            self.assertNotIn("blind.key.json", os.listdir(pack["blind_dir"]))
            self.assertTrue(os.path.isfile(pack["key_path"]))

            with open(pack["key_path"]) as f:
                key = json.load(f)

            # Passing answers: control cant_tell, planted caught, all claims cant_tell.
            ans_pass = {}
            for p in key["pairs"]:
                ans_pass[p["id"]] = p["expected_answer"]
            ans_file = os.path.join(tmp, "ans_pass.json")
            with open(ans_file, "w") as f:
                json.dump(ans_pass, f)
            v = bf.score_blind_pack(pack["blind_dir"], ans_file)
            self.assertTrue(v["pass"])
            self.assertTrue(v["control_ok"])
            self.assertTrue(v["planted_ok"])
            self.assertEqual(v["claims_cant_tell"], 3)

            # Failing control invalidates run.
            ans_bad_ctrl = dict(ans_pass)
            ctrl_id = next(p["id"] for p in key["pairs"] if p["kind"] == "cant_tell")
            ans_bad_ctrl[ctrl_id] = "left"
            with open(ans_file, "w") as f:
                json.dump(ans_bad_ctrl, f)
            v2 = bf.score_blind_pack(pack["blind_dir"], ans_file)
            self.assertFalse(v2["pass"])
            self.assertFalse(v2["control_ok"])

            # Judge picking 18k over found_budget fails the cant_tell gate.
            ans_disting = dict(ans_pass)
            claim_p = next(p for p in key["pairs"] if p["kind"] == "claim")
            ans_disting[claim_p["id"]] = "left" if claim_p["left_label"] == "ref_18k" else "right"
            with open(ans_file, "w") as f:
                json.dump(ans_disting, f)
            v3 = bf.score_blind_pack(pack["blind_dir"], ans_file)
            self.assertFalse(v3["pass"])
            self.assertEqual(v3["claims_cant_tell"], 2)


if __name__ == "__main__":
    unittest.main()
