#!/usr/bin/env python3
"""Unit tests for the creature bend + blind game-camera benchmark (stdlib only).

    python3 bench/creature/test_gamebench.py
"""
import hashlib
import json
import os
import subprocess
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


class NeutralBenchAndControlsTest(unittest.TestCase):
    def _make_pair_rows(self, tool_a, tool_b, a_wins, n=5, control_drift=0.0):
        def cut(tears, d95, score, open_edges=0):
            return {
                "bend": {"tears": tears, "tears_poses": tears, "score": score, "pass": tears < 5.0},
                "dist_p95": d95,
                "dist_mean": d95 * 0.4,
                "dist_max": d95 * 2.5,
                "open_edges": open_edges,
            }

        rows = []
        for i in range(n):
            b_tears = 8.0 + i * 0.3
            b_d95 = 0.20
            if tool_a == tool_b:
                a_tears, a_d95 = b_tears, b_d95
                slot_a, slot_b = f"{tool_a}#a", f"{tool_b}#b"
            else:
                a_tears = b_tears - 2.0 if i < a_wins else b_tears + 2.0
                a_d95 = 0.21
                slot_a, slot_b = tool_a, tool_b
            cuts = {
                slot_a: cut(a_tears, a_d95, 72.0),
                slot_b: cut(b_tears, b_d95, 68.0),
                "planted": cut(b_tears + 18.0, 1.40, 22.0),
                "control": cut(b_tears + control_drift, b_d95 + control_drift, 68.0),
            }
            rows.append({"subject": f"private_creature_{i}", "ok": True, "cuts": cuts})
        return rows

    def test_self_comparison_decimate_vs_decimate_ties_every_creature_and_has_no_winner(self):
        rows = self._make_pair_rows("decimate", "decimate", a_wins=0, n=5)
        v = gb.verdict(rows, tools=("decimate", "decimate"))
        self.assertTrue(v["controls"]["valid"])
        self.assertIsNone(v["winner"])
        self.assertFalse(v["challenger_beats_baseline"])
        self.assertFalse(v["beats_decimate"])
        self.assertEqual(v["bend_wins"]["ties"], 5)
        self.assertEqual(v["surface_wins"]["ties"], 5)
        self.assertAlmostEqual(v["median_surface_ratio"], 1.0)

    def test_neutral_arbitrary_tools_quadriflow_vs_decimate(self):
        rows = self._make_pair_rows("quadriflow", "decimate", a_wins=4, n=5)
        v = gb.verdict(rows, tools=("quadriflow", "decimate"))
        self.assertTrue(v["controls"]["valid"])
        self.assertEqual(v["tools"], {"challenger": "quadriflow", "baseline": "decimate"})
        self.assertEqual(v["winner"], "quadriflow")
        self.assertTrue(v["challenger_beats_baseline"])
        self.assertEqual(v["bend_wins"]["quadriflow"], 4)
        self.assertEqual(v["bend_wins"]["decimate"], 1)

        class Args:
            tris = 10000
            planted_tris = 1500
            tools = "quadriflow,decimate"

        md = gb.report(rows, v, Args)
        self.assertIn("quadriflow", md)
        self.assertIn("decimate", md)
        self.assertNotIn("private_creature", md)
        self.assertNotIn("weightforge", md.lower())

    def test_self_control_drift_invalidates_run_and_blocks_winner(self):
        rows = self._make_pair_rows("quadriflow", "decimate", a_wins=5, n=5, control_drift=0.5)
        v = gb.verdict(rows, tools=("quadriflow", "decimate"))
        self.assertFalse(v["controls"]["cant_tell_zero_delta"])
        self.assertFalse(v["controls"]["valid"])
        self.assertIsNone(v["winner"])
        self.assertFalse(v["challenger_beats_baseline"])

    def test_heldout_manifest_verification_and_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            hashes = []
            tsv_lines = []
            for i in range(3):
                name = f"creature_{i}"
                sd = os.path.join(work, name)
                os.makedirs(sd)
                data = f"v 0 0 {i}\nv 1 0 {i}\nv 0 1 {i}\nf 1 2 3\n".encode()
                dense = os.path.join(sd, "dense.obj")
                with open(dense, "wb") as f:
                    f.write(data)
                hashes.append(hashlib.sha256(data).hexdigest())
                tsv_lines.append(f"{name}\ttripo\t/nonexistent/{name}.glb\n")
            with open(os.path.join(work, "subjects.tsv"), "w") as f:
                f.writelines(tsv_lines)

            # Valid JSON manifest (hashes only, zero names) passes.
            man_ok = os.path.join(tmp, "heldout.json")
            with open(man_ok, "w") as f:
                json.dump({"sha256": hashes}, f)
            loaded = gb.load_heldout_manifest(man_ok)
            self.assertEqual(len(loaded), 3)
            matched = gb.verify_heldout_workspace(work, man_ok)
            self.assertEqual(len(matched), 3)

            # Too few entries (< 3) refused.
            man_short = os.path.join(tmp, "short.json")
            with open(man_short, "w") as f:
                json.dump({"sha256": hashes[:2]}, f)
            with self.assertRaises(ValueError):
                gb.load_heldout_manifest(man_short)

            # Mismatched sha256 refused.
            bad_hashes = hashes[:2] + ["0" * 64]
            man_bad = os.path.join(tmp, "bad.json")
            with open(man_bad, "w") as f:
                json.dump({"sha256": bad_hashes}, f)
            with self.assertRaises(ValueError):
                gb.verify_heldout_workspace(work, man_bad)

            # Missing manifest refused.
            with self.assertRaises((FileNotFoundError, ValueError)):
                gb.load_heldout_manifest(os.path.join(tmp, "missing.json"))

    def test_cli_missing_env_or_heldout_exits_2(self):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gamebench.py")
        clean_env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        with tempfile.TemporaryDirectory() as tmp:
            # Missing --blender / --weights exits 2 with clear stderr.
            r1 = subprocess.run(
                [sys.executable, script, tmp, "--out", os.path.join(tmp, "o"), "--tools", "decimate,decimate"],
                env=clean_env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(r1.returncode, 2)
            self.assertIn("blender", r1.stderr.lower())

            # With dummy blender/weights and --tools decimate,decimate, --retopo is NOT required;
            # --require-heldout with missing manifest exits 2 naming heldout.
            r2 = subprocess.run(
                [
                    sys.executable,
                    script,
                    tmp,
                    "--out",
                    os.path.join(tmp, "o"),
                    "--tools",
                    "decimate,decimate",
                    "--blender",
                    "/bin/true",
                    "--weights",
                    "/bin/true",
                    "--require-heldout",
                    "--heldout",
                    os.path.join(tmp, "no_such_manifest.json"),
                ],
                env=clean_env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(r2.returncode, 2)
            self.assertIn("heldout", r2.stderr.lower())

    def test_blind_pack_works_neutrally_for_arbitrary_and_self_tools(self):
        for tool_a, tool_b in [("quadriflow", "decimate"), ("decimate", "decimate")]:
            with tempfile.TemporaryDirectory() as tmp:
                rows = self._make_pair_rows(tool_a, tool_b, a_wins=3, n=3)
                slot_a = f"{tool_a}#a" if tool_a == tool_b else tool_a
                slot_b = f"{tool_b}#b" if tool_a == tool_b else tool_b
                for r in rows:
                    sd = os.path.join(tmp, r["subject"])
                    os.makedirs(sd, exist_ok=True)
                    col_a = (90, 160, 110) if tool_a == tool_b else (160, 90, 80)
                    solid_png(os.path.join(sd, f"frame_{slot_a}.png"), 64, 72, col_a)
                    solid_png(os.path.join(sd, f"frame_{slot_b}.png"), 64, 72, (90, 160, 110))
                    solid_png(os.path.join(sd, "frame_planted.png"), 64, 72, (220, 30, 30))
                v = gb.verdict(rows, tools=(tool_a, tool_b))
                with open(os.path.join(tmp, "results.json"), "w") as f:
                    json.dump({"tools": [tool_a, tool_b], "rows": rows, "verdict": v}, f)

                rc = blind.build(tmp, seed=7)
                self.assertEqual(rc, 0)
                blind_dir = os.path.join(tmp, "blind")
                with open(os.path.join(blind_dir, "manifest.json")) as f:
                    mtxt = f.read()
                for forbidden in (tool_a, tool_b, "planted", "private_creature"):
                    self.assertNotIn(forbidden, mtxt)

                with open(os.path.join(tmp, "blind.key.json")) as f:
                    key_doc = json.load(f)
                ans = {}
                for p in key_doc["pairs"]:
                    if p["kind"] == "cant_tell":
                        ans[p["id"]] = "cant_tell"
                    elif p["kind"] == "planted":
                        ans[p["id"]] = "left" if p["left_tool"] != "planted" else "right"
                    else:
                        ans[p["id"]] = "cant_tell" if tool_a == tool_b else "left"
                ans_path = os.path.join(tmp, "ans.json")
                with open(ans_path, "w") as f:
                    json.dump(ans, f)
                self.assertEqual(blind.score(blind_dir, ans_path), 0)


if __name__ == "__main__":
    unittest.main()
