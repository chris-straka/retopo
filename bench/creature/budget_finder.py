#!/usr/bin/env python3
"""Per-creature triangle budget finder (`retopo-7`, stdlib only).

Finds the smallest triangle count whose 8-frame posed game-camera silhouette and
shading error against the dense source (`retopo-3`, `posed_image_check.py`) sits
within Blender decimate's own noise at the 18k reference budget, then builds an
unmarked blind game-camera verification pack comparing the found budget against
the 18k cut (with mandatory can't-tell control and planted 1,500-tri swap).

Modes:
    # Find budgets & generate blind pack:
    python3 bench/creature/budget_finder.py find WORK --out OUT \\
        [--heldout MANIFEST] [--ref-tris 18000] \\
        [--candidates 2000,3000,4500,6000,8000,10000,12000,15000,18000] \\
        [--planted-tris 1500] [--seed 20261120]

    # Score blind answers against the external key:
    python3 bench/creature/budget_finder.py score OUT/blind ANSWERS.json
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import blind  # noqa: E402
import gamebench as gb  # noqa: E402

DEFAULT_CANDIDATES = (2000, 3000, 4500, 6000, 8000, 10000, 12000, 15000, 18000)
DEFAULT_REF_TRIS = 18000
DEFAULT_PLANTED_TRIS = 1500
MIN_SIL_NOISE_PCT = 0.10
MIN_SHADE_NOISE_RMSE = 0.25

BUDGET_QUESTION = (
    "Same creature, same stride frame, HLH follow camera. Can you tell a real "
    "visual difference in silhouette or shading between LEFT and RIGHT? "
    "Answer left (left is cleaner), right (right is cleaner), or cant_tell."
)


def parse_candidates(raw: str, ref_tris: int) -> list:
    vals = sorted({int(x.strip()) for x in raw.split(",") if x.strip()})
    if not vals or any(v <= 0 for v in vals):
        raise ValueError(f"invalid --candidates list: {raw!r}")
    if ref_tris not in vals:
        vals.append(ref_tris)
        vals.sort()
    return vals


def compute_noise_budget(ref_vs_dense: dict, ref_vs_neighbor: dict) -> dict:
    """Compute allowed silhouette/shading error ceilings from decimate's own noise.

    `ref_vs_dense` is `posed_image_check(dense, cut@ref_tris)`.
    `ref_vs_neighbor` is `posed_image_check(cut@ref_tris, cut@neighbor_tris)`
    measuring decimate's intrinsic perturbation noise near `ref_tris`.
    """
    sil_noise = max(float(ref_vs_neighbor.get("mean_sil_diff_pct", 0.0)), MIN_SIL_NOISE_PCT)
    shade_noise = max(float(ref_vs_neighbor.get("mean_shade_rmse", 0.0)), MIN_SHADE_NOISE_RMSE)
    return {
        "ref_sil_diff_pct": round(float(ref_vs_dense["mean_sil_diff_pct"]), 4),
        "ref_shade_rmse": round(float(ref_vs_dense["mean_shade_rmse"]), 4),
        "noise_sil_pct": round(sil_noise, 4),
        "noise_shade_rmse": round(shade_noise, 4),
        "max_sil_diff_pct": round(float(ref_vs_dense["mean_sil_diff_pct"]) + sil_noise, 4),
        "max_shade_rmse": round(float(ref_vs_dense["mean_shade_rmse"]) + shade_noise, 4),
    }


def within_noise(cand_metrics: dict, ceiling: dict) -> bool:
    """True when candidate's 8-frame posed silhouette & shading errors sit within ceiling."""
    sil = float(cand_metrics["mean_sil_diff_pct"])
    shade = float(cand_metrics["mean_shade_rmse"])
    return sil <= ceiling["max_sil_diff_pct"] + 1e-9 and shade <= ceiling["max_shade_rmse"] + 1e-9


def select_smallest_budget(probes: list, ceiling: dict, ref_tris: int) -> dict:
    """Pick the smallest probed triangle budget whose metrics stay within `ceiling`.

    `probes` is a list of dicts with keys `target_tris`, `actual_tris`, `metrics`.
    Returns `(selected_probe, within_noise_flag)`.
    """
    ordered = sorted(probes, key=lambda p: p["target_tris"])
    for p in ordered:
        if within_noise(p["metrics"], ceiling):
            return {
                "found_target_tris": p["target_tris"],
                "found_actual_tris": p["actual_tris"],
                "within_noise": True,
                "reduction_pct": round(100.0 * (1.0 - p["actual_tris"] / max(1, ref_tris)), 2),
                "metrics": p["metrics"],
            }
    fallback = ordered[-1]
    return {
        "found_target_tris": fallback["target_tris"],
        "found_actual_tris": fallback["actual_tris"],
        "within_noise": False,
        "reduction_pct": round(100.0 * (1.0 - fallback["actual_tris"] / max(1, ref_tris)), 2),
        "metrics": fallback["metrics"],
    }


def _run_blender(blender: str, script: str, args: list, log_path: str, timeout: int = 1800) -> int:
    cmd = [blender, "-b", "--factory-startup", "-t", os.environ.get("BLENDER_THREADS", "2"),
           "-P", os.path.join(HERE, script), "--"] + [str(x) for x in args]
    return gb.sh(cmd, log_path, timeout=timeout)


def _cut_and_rig(blender: str, dense: str, ref_rig: str, ref_cut: str,
                 out_obj: str, out_glb: str, tris: int, log_path: str, redo: bool = False) -> int:
    if redo or not os.path.isfile(out_obj):
        rc = _run_blender(blender, "cut.py", ["decimate", dense, out_obj, str(tris)], log_path)
        if rc != 0 or not os.path.isfile(out_obj):
            raise RuntimeError(f"cut.py decimate@{tris} failed (exit {rc})")
    actual_tris, _ = gb.obj_census(out_obj)
    if redo or not os.path.isfile(out_glb):
        rc = _run_blender(blender, "transfer.py", [ref_rig, ref_cut, out_obj, out_glb], log_path)
        if rc != 0 or not os.path.isfile(out_glb):
            raise RuntimeError(f"transfer.py failed for {out_obj} (exit {rc})")
    return actual_tris


def _image_check(blender: str, ref_glb: str, cand_glb: str, dense: str,
                 out_json: str, cache_dir: str, frames: int, log_path: str, redo: bool = False) -> dict:
    if redo or not os.path.isfile(out_json):
        rc = _run_blender(
            blender,
            "posed_image_check.py",
            [ref_glb, cand_glb, dense, out_json, "--frames", str(frames), "--cache", cache_dir],
            log_path,
        )
        if rc != 0 or not os.path.isfile(out_json):
            raise RuntimeError(f"posed_image_check.py failed (exit {rc}) for {cand_glb}")
    with open(out_json, "r", encoding="utf-8") as f:
        return json.load(f)


def build_blind_pack(out_dir: str, rows: list, seed: int) -> dict:
    """Build unmarked blind pack in `OUT/blind/` with sibling key `OUT/blind.key.json`."""
    blind_dir = os.path.join(out_dir, "blind")
    if os.path.exists(blind_dir):
        shutil.rmtree(blind_dir)
    os.makedirs(blind_dir, exist_ok=True)

    rng = random.Random(seed)
    items = []

    for r in rows:
        anon = r["anon"]
        f_found = r["frame_found"]
        f_18k = r["frame_18k"]
        items.append({
            "kind": "claim",
            "anon": anon,
            "found_tris": r["selection"]["found_actual_tris"],
            "a_label": "found_budget",
            "b_label": "ref_18k",
            "a_path": f_found,
            "b_path": f_18k,
            "expected": "cant_tell",
        })

    # Can't-tell control: identical 18k render on both sides (assert RMSE == 0.0).
    ctrl_row = rows[rng.randrange(len(rows))]
    items.append({
        "kind": "cant_tell",
        "anon": ctrl_row["anon"],
        "a_label": "ref_18k",
        "b_label": "ref_18k",
        "a_path": ctrl_row["frame_18k"],
        "b_path": ctrl_row["frame_18k"],
        "expected": "cant_tell",
    })

    # Planted bad case: 18k vs planted (1,500 tris + limb swap).
    plant_row = rows[rng.randrange(len(rows))]
    items.append({
        "kind": "planted",
        "anon": plant_row["anon"],
        "a_label": "ref_18k",
        "b_label": "planted",
        "a_path": plant_row["frame_18k"],
        "b_path": plant_row["frame_planted"],
        "expected": "ref_18k",
    })

    rng.shuffle(items)

    manifest_pairs = []
    key_pairs = []
    for idx, it in enumerate(items, 1):
        pid = f"pair_{idx:02d}"
        flip = bool(rng.getrandbits(1))
        if flip:
            left_path, right_path = it["b_path"], it["a_path"]
            left_label, right_label = it["b_label"], it["a_label"]
        else:
            left_path, right_path = it["a_path"], it["b_path"]
            left_label, right_label = it["a_label"], it["b_label"]

        lw, lh, lrgb = blind.read_png(left_path)
        rw, rh, rrgb = blind.read_png(right_path)
        if (lw, lh) != (rw, rh):
            raise RuntimeError(f"mismatched tile sizes {(lw, lh)} vs {(rw, rh)}")
        sheet_w, sheet_h, sheet_rgb = blind.compose_pair_sheet(lrgb, rrgb, lw, lh, idx)
        sheet_path = os.path.join(blind_dir, f"{pid}.png")
        blind.write_png(sheet_path, sheet_w, sheet_h, sheet_rgb)

        if it["kind"] == "cant_tell":
            rmse = blind.rmse_below_header(sheet_rgb, sheet_w, sheet_h)
            if rmse != 0.0:
                raise RuntimeError(f"cant_tell control RMSE is {rmse} != 0.0")

        manifest_pairs.append({
            "id": pid,
            "image": f"{pid}.png",
            "question": BUDGET_QUESTION,
        })
        key_entry = {
            "id": pid,
            "kind": it["kind"],
            "anon": it["anon"],
            "left_label": left_label,
            "right_label": right_label,
        }
        if it["kind"] == "claim":
            key_entry["found_tris"] = it["found_tris"]
            key_entry["expected_answer"] = "cant_tell"
        elif it["kind"] == "cant_tell":
            key_entry["expected_answer"] = "cant_tell"
        else:
            key_entry["expected_answer"] = "left" if left_label == "ref_18k" else "right"
        key_pairs.append(key_entry)

    with open(os.path.join(blind_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"question": BUDGET_QUESTION, "choices": ["left", "right", "cant_tell"], "pairs": manifest_pairs},
            f,
            indent=2,
        )
        f.write("\n")

    key_path = os.path.join(out_dir, "blind.key.json")
    key_doc = {"seed": seed, "pairs": key_pairs}
    with open(key_path, "w", encoding="utf-8") as f:
        json.dump(key_doc, f, indent=2)
        f.write("\n")

    return {"blind_dir": blind_dir, "key_path": key_path, "pair_count": len(key_pairs)}


def score_blind_pack(blind_dir: str, answers_path: str, key_path: str = "", out_path: str = "") -> dict:
    """Score blind answers for budget_finder (`retopo-7` gate)."""
    blind_dir = os.path.normpath(os.path.abspath(blind_dir))
    if not key_path:
        key_path = blind_dir + ".key.json"
    if not os.path.isfile(key_path):
        raise FileNotFoundError(f"answer key not found: {key_path}")
    with open(key_path, "r", encoding="utf-8") as f:
        key_doc = json.load(f)
    with open(answers_path, "r", encoding="utf-8") as f:
        ans_doc = json.load(f)
    answers = ans_doc["answers"] if isinstance(ans_doc, dict) and "answers" in ans_doc else ans_doc

    control_ok = False
    planted_ok = False
    claims = []
    reasons = []

    for p in key_doc["pairs"]:
        pid = p["id"]
        got = answers.get(pid)
        if got not in ("left", "right", "cant_tell"):
            reasons.append(f"{pid}: missing or invalid answer {got!r}")
            continue
        if p["kind"] == "cant_tell":
            control_ok = (got == "cant_tell")
            if not control_ok:
                reasons.append(f"{pid}: cant_tell control failed (got {got!r})")
        elif p["kind"] == "planted":
            planted_ok = (got == p["expected_answer"])
            if not planted_ok:
                reasons.append(f"{pid}: planted bad case missed (got {got!r}, expected {p['expected_answer']!r})")
        else:
            is_ct = (got == "cant_tell")
            picked = (
                "cant_tell"
                if is_ct
                else (p["left_label"] if got == "left" else p["right_label"])
            )
            claims.append({
                "id": pid,
                "anon": p["anon"],
                "found_tris": p.get("found_tris"),
                "answer": got,
                "picked": picked,
                "cant_tell": is_ct,
            })

    claims_total = len(claims)
    claims_cant_tell = sum(1 for c in claims if c["cant_tell"])
    all_claims_cant_tell = claims_total > 0 and claims_cant_tell == claims_total
    if not all_claims_cant_tell:
        reasons.append(f"claims cant_tell: {claims_cant_tell}/{claims_total} (gate requires all cant_tell)")

    valid = control_ok and planted_ok
    passed = valid and all_claims_cant_tell

    verdict = {
        "pass": passed,
        "valid": valid,
        "control_ok": control_ok,
        "planted_ok": planted_ok,
        "claims_total": claims_total,
        "claims_cant_tell": claims_cant_tell,
        "claims": claims,
        "reasons": reasons,
    }
    if not out_path:
        out_path = os.path.join(os.path.dirname(blind_dir), "budget_verdict.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2)
        f.write("\n")
    return verdict


def cmd_find(a) -> int:
    blender = a.blender or os.environ.get("BLENDER") or shutil.which("blender")
    if not blender or not os.path.exists(blender):
        print("budget_finder: set --blender or BLENDER", file=sys.stderr)
        return 2

    candidates = parse_candidates(a.candidates, a.ref_tris)
    work = os.path.abspath(a.work)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    log = os.path.join(out, "budget_finder.log")

    if a.heldout:
        subj_rows = gb.verify_heldout_workspace(work, a.heldout)
    else:
        subj_rows = gb.subjects(work, a.subjects or None)
    if a.subjects:
        keep = set(a.subjects.split(","))
        subj_rows = [r for r in subj_rows if r["name"] in keep]
    if not subj_rows:
        print("budget_finder: no matching subjects", file=sys.stderr)
        return 2

    neighbor_tris = max(1000, int(round(a.ref_tris * 0.95)))
    rows = []

    for idx, s in enumerate(subj_rows, 1):
        name = s["name"]
        anon = f"creature_{idx}"
        sd = os.path.join(work, name)
        od = os.path.join(out, anon)
        os.makedirs(od, exist_ok=True)
        cache_dir = os.path.join(od, "cache")
        os.makedirs(cache_dir, exist_ok=True)

        dense = os.path.join(sd, "dense.obj")
        ref_cut = os.path.join(sd, "ref_cut.glb")
        ref_rig = os.path.join(sd, "ref", f"{name}.glb")
        for req in (dense, ref_cut, ref_rig):
            if not os.path.isfile(req):
                raise RuntimeError(f"{anon}: missing prepared file {req}")

        print(f"== {anon} ==", flush=True)

        # 1. Rig dense.obj once with the reference weights.
        dense_glb = os.path.join(od, "dense_rigged.glb")
        if a.redo or not os.path.isfile(dense_glb):
            rc = _run_blender(blender, "transfer.py", [ref_rig, ref_cut, dense, dense_glb], log)
            if rc != 0 or not os.path.isfile(dense_glb):
                raise RuntimeError(f"{anon}: transfer.py on dense.obj failed (exit {rc})")

        # 2. Cut & rig 18k reference and 95% neighbor to establish decimate's own noise floor.
        ref_obj = os.path.join(od, f"cut_{a.ref_tris}.obj")
        ref_glb = os.path.join(od, f"cut_{a.ref_tris}.glb")
        ref_actual = _cut_and_rig(blender, dense, ref_rig, ref_cut, ref_obj, ref_glb, a.ref_tris, log, a.redo)

        nbr_obj = os.path.join(od, f"cut_{neighbor_tris}.obj")
        nbr_glb = os.path.join(od, f"cut_{neighbor_tris}.glb")
        _cut_and_rig(blender, dense, ref_rig, ref_cut, nbr_obj, nbr_glb, neighbor_tris, log, a.redo)

        ref_vs_dense = _image_check(
            blender, dense_glb, ref_glb, dense,
            os.path.join(od, f"img_{a.ref_tris}.json"), cache_dir, a.frames, log, a.redo,
        )
        ref_vs_nbr = _image_check(
            blender, ref_glb, nbr_glb, dense,
            os.path.join(od, f"img_noise_{neighbor_tris}.json"), cache_dir, a.frames, log, a.redo,
        )
        ceiling = compute_noise_budget(ref_vs_dense, ref_vs_nbr)
        print(
            f"   ref@{ref_actual}: sil={ceiling['ref_sil_diff_pct']:.3f}% shade={ceiling['ref_shade_rmse']:.3f} "
            f"| noise ceiling: sil<={ceiling['max_sil_diff_pct']:.3f}% shade<={ceiling['max_shade_rmse']:.3f}",
            flush=True,
        )

        # 3. Probe candidate triangle budgets against dense_rigged.
        probes = []
        for t in candidates:
            if t == a.ref_tris:
                probes.append({"target_tris": t, "actual_tris": ref_actual, "metrics": ref_vs_dense})
                continue
            c_obj = os.path.join(od, f"cut_{t}.obj")
            c_glb = os.path.join(od, f"cut_{t}.glb")
            actual = _cut_and_rig(blender, dense, ref_rig, ref_cut, c_obj, c_glb, t, log, a.redo)
            m = _image_check(
                blender, dense_glb, c_glb, dense,
                os.path.join(od, f"img_{t}.json"), cache_dir, a.frames, log, a.redo,
            )
            ok = within_noise(m, ceiling)
            print(
                f"   probe@{t} (actual {actual}): sil={m['mean_sil_diff_pct']:.3f}% "
                f"shade={m['mean_shade_rmse']:.3f} -> {'OK' if ok else 'over'}",
                flush=True,
            )
            probes.append({"target_tris": t, "actual_tris": actual, "metrics": m})

        sel = select_smallest_budget(probes, ceiling, ref_actual)
        found_t = sel["found_target_tris"]
        print(
            f"   FOUND budget={sel['found_actual_tris']} tris (target {found_t}, "
            f"-{sel['reduction_pct']:.1f}% vs {ref_actual})",
            flush=True,
        )

        # 4. Planted bad cut (1,500 tris + limb swap) and game-camera stills for blind pair.
        pl_obj = os.path.join(od, f"cut_planted_{a.planted_tris}.obj")
        pl_glb = os.path.join(od, f"cut_planted_{a.planted_tris}.glb")
        _cut_and_rig(blender, dense, ref_rig, ref_cut, pl_obj, pl_glb, a.planted_tris, log, a.redo)

        found_glb = os.path.join(od, f"cut_{found_t}.glb")
        frame_found = os.path.join(od, "frame_found.png")
        frame_18k = os.path.join(od, "frame_18k.png")
        frame_planted = os.path.join(od, "frame_planted.png")
        if a.redo or not os.path.isfile(frame_found):
            rc = _run_blender(blender, "game_frame.py", [found_glb, dense, frame_found], log)
            if rc != 0:
                raise RuntimeError(f"{anon}: game_frame.py failed on found budget")
        if a.redo or not os.path.isfile(frame_18k):
            rc = _run_blender(blender, "game_frame.py", [ref_glb, dense, frame_18k], log)
            if rc != 0:
                raise RuntimeError(f"{anon}: game_frame.py failed on 18k ref")
        if a.redo or not os.path.isfile(frame_planted):
            rc = _run_blender(blender, "game_frame.py", [pl_glb, dense, frame_planted, "--plant-swap"], log)
            if rc != 0:
                raise RuntimeError(f"{anon}: game_frame.py failed on planted cut")

        rows.append({
            "anon": anon,
            "ref_tris": ref_actual,
            "ceiling": ceiling,
            "probes": probes,
            "selection": sel,
            "frame_found": frame_found,
            "frame_18k": frame_18k,
            "frame_planted": frame_planted,
        })

    pack_info = build_blind_pack(out, rows, seed=a.seed)

    # Save anonymized results (zero corpus names).
    pub_rows = [
        {
            "anon": r["anon"],
            "ref_tris": r["ref_tris"],
            "ceiling": r["ceiling"],
            "selection": r["selection"],
            "probes": [
                {
                    "target_tris": p["target_tris"],
                    "actual_tris": p["actual_tris"],
                    "mean_sil_diff_pct": p["metrics"]["mean_sil_diff_pct"],
                    "mean_shade_rmse": p["metrics"]["mean_shade_rmse"],
                }
                for p in r["probes"]
            ],
        }
        for r in rows
    ]
    summary = {
        "ref_tris": a.ref_tris,
        "planted_tris": a.planted_tris,
        "frames": a.frames,
        "seed": a.seed,
        "creatures": pub_rows,
        "blind_pairs": pack_info["pair_count"],
    }
    res_path = os.path.join(out, "budget_results.json")
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")

    md_lines = [
        "# Triangle Budget Finder (`retopo-7`)",
        "",
        f"Reference cut: `{a.ref_tris}` tris, 8-frame posed game-camera check (`retopo-3`).",
        "",
        "| Creature | Ref tris | Found tris | Reduction | Sil diff % (ceiling) | Shade RMSE (ceiling) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in pub_rows:
        c = r["ceiling"]
        sel = r["selection"]
        m = sel["metrics"]
        md_lines.append(
            f"| {r['anon']} | {r['ref_tris']} | {sel['found_actual_tris']} | -{sel['reduction_pct']:.1f}% | "
            f"{m['mean_sil_diff_pct']:.3f}% (<= {c['max_sil_diff_pct']:.3f}%) | "
            f"{m['mean_shade_rmse']:.3f} (<= {c['max_shade_rmse']:.3f}) |"
        )
    md_path = os.path.join(out, "budget_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")
    print("\n".join(md_lines))

    fleet_art = os.environ.get("FLEET_ARTIFACTS")
    if fleet_art and os.path.isdir(fleet_art):
        shutil.copyfile(res_path, os.path.join(fleet_art, "budget_results.json"))
        shutil.copyfile(md_path, os.path.join(fleet_art, "budget_report.md"))
        shutil.copyfile(pack_info["key_path"], os.path.join(fleet_art, "blind.key.json"))
        dst_blind = os.path.join(fleet_art, "blind")
        if os.path.exists(dst_blind):
            shutil.rmtree(dst_blind)
        shutil.copytree(pack_info["blind_dir"], dst_blind)

    return 0


def cmd_score(a) -> int:
    v = score_blind_pack(a.blind_dir, a.answers, key_path=a.key, out_path=a.out)
    status = "PASS" if v["pass"] else "FAIL"
    print(
        f"{status} retopo-7 budget blind check: control_ok={v['control_ok']} "
        f"planted_ok={v['planted_ok']} cant_tell={v['claims_cant_tell']}/{v['claims_total']}"
    )
    for r in v["reasons"]:
        print(f"  - {r}", file=sys.stderr)
    return 0 if v["pass"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find", help="Find minimal budgets and build blind verification pack")
    p_find.add_argument("work")
    p_find.add_argument("--out", required=True)
    p_find.add_argument("--heldout", default=os.environ.get("GAMEBENCH_HELDOUT", ""))
    p_find.add_argument("--subjects", default="")
    p_find.add_argument("--ref-tris", type=int, default=DEFAULT_REF_TRIS)
    p_find.add_argument("--planted-tris", type=int, default=DEFAULT_PLANTED_TRIS)
    p_find.add_argument("--candidates", default=",".join(str(x) for x in DEFAULT_CANDIDATES))
    p_find.add_argument("--frames", type=int, default=8)
    p_find.add_argument("--seed", type=int, default=20261120)
    p_find.add_argument("--blender", default="")
    p_find.add_argument("--redo", action="store_true")

    p_score = sub.add_parser("score", help="Score blind answers for budget finder")
    p_score.add_argument("blind_dir")
    p_score.add_argument("answers")
    p_score.add_argument("--key", default="")
    p_score.add_argument("--out", default="")

    a = ap.parse_args()
    if a.cmd == "find":
        return cmd_find(a)
    return cmd_score(a)


if __name__ == "__main__":
    sys.exit(main())
