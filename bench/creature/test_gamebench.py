#!/usr/bin/env python3
"""Unit tests for the creature bend + blind game-camera benchmark (stdlib only).

    python3 bench/creature/test_gamebench.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blind  # noqa: E402
import gamebench as gb  # noqa: E402


def make_row(subject, rt_tears, dc_tears, pl_tears, rt_d95, dc_d95, pl_d95,
             rt_score=55.0, dc_score=75.0, pl_score=20.0):
    def cut(tears, d95, score, open_edges=0):
        return {
            "bend": {"tears": tears, "tears_poses": tears, "score": score, "pass": tears < 5.0},
            "dist_p95": d95,
            "dist_mean": d95 * 0.4,
            "dist_max": d95 * 2.5,
            "open_edges": open_edges,
        }

    return {
        "subject": subject,
        "ok": True,
        "cuts": {
            "retopo": cut(rt_tears, rt_d95, rt_score, open_edges=12),
            "decimate": cut(dc_tears, dc_d95, dc_score, open_edges=0),
            "planted": cut(pl_tears, pl_d95, pl_score, open_edges=0),
        },
    }


def make_five(retopo_wins):
    rows = []
    for i in range(5):
        if i < retopo_wins:
            rows.append(make_row(f"secret_corpus_{i}", 6.0, 9.0, 28.0, 0.20, 0.20, 1.40))
        else:
            rows.append(make_row(f"secret_corpus_{i}", 11.0, 8.0, 28.0, 0.20, 0.20, 1.40))
    return rows


def solid_png(path, w, h, color):
    rgb = bytearray(bytes(color) * (w * h))
    # Vary a few pixels deterministically so tiles are non-trivial.
    for x in range(min(w, h)):
        o = (x * w + x) * 3
        rgb[o] = (color[0] + x * 7) & 0xFF
        rgb[o + 1] = (color[1] + x * 13) & 0xFF
        rgb[o + 2] = (color[2] + x * 19) & 0xFF
    blind.write_png(path, w, h, rgb)


class VerdictAndReportTest(unittest.TestCase):
    def test_beats_requires_majority_bend_and_surface_within_10pct(self):
        v = gb.verdict(make_five(3))
        self.assertTrue(v["beats_decimate"])
        self.assertEqual(v["bend_wins"]["retopo"], 3)
        self.assertTrue(v["controls"]["valid"])

    def test_two_of_five_is_not_majority(self):
        self.assertFalse(gb.verdict(make_five(2))["beats_decimate"])

    def test_surface_over_10pct_blocks_win(self):
        rows = make_five(5)
        for r in rows:
            r["cuts"]["retopo"]["dist_p95"] = 0.30
        v = gb.verdict(rows)
        self.assertFalse(v["beats_decimate"])
        self.assertAlmostEqual(v["median_surface_ratio_retopo_over_decimate"], 1.5)

    def test_planted_must_lose_both_surface_and_bend_on_every_creature(self):
        rows = make_five(3)
        rows[0]["cuts"]["planted"]["bend"]["tears"] = 5.0  # cleaner than decimate's 9.0 -> invalid!
        v = gb.verdict(rows)
        self.assertFalse(v["controls"]["valid"])
        self.assertFalse(v["controls"]["planted_bend_worse_on_all"])

        rows2 = make_five(3)
        rows2[2]["cuts"]["planted"]["dist_p95"] = 0.10  # tighter than decimate's 0.20 -> invalid!
        v2 = gb.verdict(rows2)
        self.assertFalse(v2["controls"]["valid"])
        self.assertFalse(v2["controls"]["planted_surface_worse_on_all"])

    def test_report_never_leaks_corpus_subject_names(self):
        rows = make_five(1)
        v = gb.verdict(rows)

        class Args:
            tris = 10000
            planted_tris = 1500

        md = gb.report(rows, v, Args)
        self.assertNotIn("secret_corpus", md)
        self.assertIn("creature_1", md)
        self.assertIn("creature_5", md)


class ParseBendTest(unittest.TestCase):
    def test_prefers_rom_poses_by_default_and_clips_when_requested(self):
        payload = json.dumps({
            "score": 63.7,
            "pass": False,
            "headline": [
                {"name": "game clips", "tears_pct": 15.83, "frames": 69},
                {"name": "range of motion", "tears_pct": 10.85, "frames": 32},
            ],
        })
        b_rom = gb.parse_bend(payload, prefer_clips=False)
        self.assertEqual(b_rom["tears"], 10.85)
        self.assertEqual(b_rom["tears_poses"], 10.85)
        self.assertEqual(b_rom["tears_clips"], 15.83)

        b_clips = gb.parse_bend(payload, prefer_clips=True)
        self.assertEqual(b_clips["tears"], 15.83)


class BlindPackAndScoreTest(unittest.TestCase):
    def test_external_key_zero_control_rmse_and_no_leaks_in_blind_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = make_five(1)
            for r in rows:
                sd = os.path.join(tmp, r["subject"])
                os.makedirs(sd, exist_ok=True)
                solid_png(os.path.join(sd, "frame_retopo.png"), 64, 72, (160, 90, 80))
                solid_png(os.path.join(sd, "frame_decimate.png"), 64, 72, (90, 160, 110))
                solid_png(os.path.join(sd, "frame_planted.png"), 64, 72, (220, 30, 30))
            with open(os.path.join(tmp, "results.json"), "w") as f:
                json.dump({"rows": rows, "verdict": gb.verdict(rows)}, f)

            rc = blind.build(tmp, seed=20261115)
            self.assertEqual(rc, 0)

            blind_dir = os.path.join(tmp, "blind")
            ext_key = os.path.join(tmp, "blind.key.json")
            self.assertTrue(os.path.exists(ext_key))
            self.assertTrue(os.path.exists(os.path.join(tmp, "summary_sheet.png")))

            # Key must NEVER sit inside the judged folder, and manifest must leak nothing.
            inside = sorted(os.listdir(blind_dir))
            self.assertNotIn("key.json", inside)
            self.assertNotIn("blind.key.json", inside)
            with open(os.path.join(blind_dir, "manifest.json")) as f:
                manifest_txt = f.read()
            for forbidden in ("retopo", "decimate", "planted", "secret_corpus"):
                self.assertNotIn(forbidden, manifest_txt)

            with open(ext_key) as f:
                key_doc = json.load(f)
            self.assertEqual(len(key_doc["pairs"]), 7)
            # Key itself must use anonymized creature_N labels, not corpus filenames.
            self.assertNotIn("secret_corpus", json.dumps(key_doc))

            # Verify can't-tell control sheet literally has 0.0 pixel RMSE below header.
            ctrl = next(p for p in key_doc["pairs"] if p["kind"] == "cant_tell")
            sw, sh, srgb = blind.read_png(os.path.join(blind_dir, ctrl["file"]))
            self.assertEqual(blind.rmse_below_header(srgb, sw, sh, blind.HEADER_H), 0.0)

            # 1) A judge who guesses on the control has their entire run discarded (exit 2).
            bad_ans = {}
            for p in key_doc["pairs"]:
                bad_ans[p["id"]] = "left"
            bad_path = os.path.join(tmp, "bad_answers.json")
            with open(bad_path, "w") as f:
                json.dump(bad_ans, f)
            self.assertEqual(blind.score(blind_dir, bad_path), 2)
            with open(os.path.join(tmp, "blind.verdict.json")) as f:
                bad_verdict = json.load(f)
            self.assertFalse(bad_verdict["pass"])
            self.assertTrue(bad_verdict["discarded"])

            # 2) Sound judge passes gates and records claim agreement cleanly.
            good_ans = []
            for p in key_doc["pairs"]:
                if p["kind"] == "cant_tell":
                    pick = "cant_tell"
                elif p["kind"] == "planted":
                    pick = "left" if p["left_tool"] == "decimate" else "right"
                else:
                    win = p["bend_winner"]
                    pick = "left" if p["left_tool"] == win else "right"
                good_ans.append({"id": p["id"], "answer": pick})
            good_path = os.path.join(tmp, "good_answers.json")
            with open(good_path, "w") as f:
                json.dump(good_ans, f)
            self.assertEqual(blind.score(blind_dir, good_path), 0)
            # Scoring must also keep verdict outside blind_dir.
            self.assertEqual(sorted(os.listdir(blind_dir)), inside)
            with open(os.path.join(tmp, "blind.verdict.json")) as f:
                good_verdict = json.load(f)
            self.assertTrue(good_verdict["pass"])
            self.assertEqual(good_verdict["claims"]["agree_with_bend"], 5)


if __name__ == "__main__":
    unittest.main()
