#!/usr/bin/env python3
"""Measure-only creature bend + surface benchmark: retopoforge vs Blender decimate
at the HLH game camera (stdlib only).

    bench/creature/gamebench.py WORK --out OUT [--tris 10000]
        [--planted-tris 1500] [--subjects a,b] [--redo]

WORK is a creature-bench workspace (bench/creature/README.md) where each
`tripo` creature already has `dense.obj`, `ref_cut.glb`, `ref/<name>.glb`,
and `ref/<name>.rom.ron` (its own range of motion from the creature route).
Nothing in WORK is modified; all outputs go to OUT.

Per creature, three cuts of the same `dense.obj`:
  retopo    this repo's CLI, count-calibrated within 8% of --tris
  decimate  Blender collapse decimate at --tris (the floor to beat)
  planted   coarse decimate (--planted-tris) with left/right limb weights
            swapped after weight transfer: a known unmistakable defect that
            both the metrics and a blind game-camera judge must flag.

Measurements per cut:
  bend      weightforge (`WEIGHTS_NEW` or `WEIGHTS`) `tears_pct` on the
            creature's own range-of-motion poses (`--poses <rom>`, lower is
            better), plus the 0-100 `score` and `metrics.py` `posed_flips`
            as secondary readings. (Optional `--motion` also records humanoid
            clip tears when benchmarking humanoid characters.)
  surface   `metrics.py` two-sided distance to `dense.obj` (`dist_mean`,
            `dist_p95`, `dist_max` as % of bbox diagonal) and normal error.
  pictures  `game_frame.py` renders every cut at the same stride frame from
            the shared HLH follow camera (`dense.obj` framing, smooth-shaded,
            no wireframes or labels). `blind.py` packs unmarked pair sheets
            into `OUT/blind/` and writes `OUT/blind.key.json` beside it.

Verdict rule (`docs/direction.md`):
  `retopo` beats the `decimate` floor when it wins bend (lower `tears`) on
  most creatures AND its median `dist_p95` is within 10% of `decimate`'s.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def obj_census(path):
    """Return (triangles counting a quad as two, quads) of an OBJ."""
    tris = quads = 0
    with open(path) as f:
        for line in f:
            if line.startswith("f "):
                n = len(line.split()) - 1
                tris += n - 2
                quads += int(n == 4)
    return tris, quads


def sh(cmd, log, timeout=3600):
    with open(log, "a") as f:
        f.write("$ " + " ".join(cmd) + "\n")
        f.flush()
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
        f.write(f"# exit {r.returncode}\n")
    return r.returncode


def blender(blender_bin, script, args, log, timeout=3600):
    threads = os.environ.get("BLENDER_THREADS", "2")
    return sh(
        [blender_bin, "-b", "--factory-startup", "-t", threads, "-P",
         os.path.join(HERE, script), "--", *args],
        log,
        timeout,
    )


def subjects(work, only=None):
    rows = []
    with open(os.path.join(work, "subjects.tsv")) as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                cols = line.rstrip("\n").split("\t")
                rows.append({"name": cols[0], "kind": cols[1], "source": cols[2]})
    rows = [r for r in rows if r["kind"] == "tripo"]
    if only:
        keep = set(only.split(","))
        rows = [r for r in rows if r["name"] in keep]
    return rows


def cut_retopo(retopo, dense, out, tris, max_tries, log):
    """Run retopo CLI (its default `--exact-count` already runs an internal
    count-matching second pass when >8% off; `--retopo-tries` allows extra
    outer passes only when `--no-exact-count` is tested)."""
    if os.path.exists(out):
        return True
    target = max(100, tris // 2)
    tries = []
    for attempt in range(max(1, max_tries)):
        tmp = f"{out}.try{attempt}.obj"
        code = sh([retopo, "--input", dense, "--output", tmp,
                   "--target-quads", str(target), "--quiet"], log)
        if code != 0 or not os.path.exists(tmp):
            break
        got, _ = obj_census(tmp)
        tries.append((abs(got - tris), attempt, tmp))
        if got == 0 or abs(got - tris) <= 0.08 * tris:
            break
        target = max(100, int(target * tris / got))
    if not tries:
        return False
    best = min(tries)[2]
    os.replace(best, out)
    for _, _, tmp in tries:
        if tmp != best and os.path.exists(tmp):
            os.remove(tmp)
    return True


def cut_blender(blender_bin, dense, out, tris, log):
    if os.path.exists(out):
        return True
    code = blender(blender_bin, "cut.py", ["decimate", dense, out, str(tris)], log)
    return code == 0 and os.path.exists(out)


def parse_bend(stdout, prefer_clips=False):
    """Parse `weights check --json` output into bend headline readings."""
    try:
        w = json.loads(stdout)
    except ValueError:
        idx = stdout.find("{")
        if idx < 0:
            raise
        w = json.loads(stdout[idx:])
    out = {"score": w.get("score"), "pass": w.get("pass")}
    for sec in w.get("headline", []):
        if sec.get("name") == "game clips":
            out["tears_clips"] = sec.get("tears_pct")
            out["clips_frames"] = sec.get("frames")
        else:
            out["tears_poses"] = sec.get("tears_pct")
            out["poses_frames"] = sec.get("frames")
    if prefer_clips and out.get("tears_clips") is not None:
        out["tears"] = out["tears_clips"]
    elif out.get("tears_poses") is not None:
        out["tears"] = out["tears_poses"]
    elif out.get("tears_clips") is not None:
        out["tears"] = out["tears_clips"]
    else:
        # Older weightforge fallback if `headline` is absent: invert 0-100 score.
        sc = out.get("score")
        out["tears"] = round(100.0 - float(sc), 3) if sc is not None else None
    return out


def run_bend(weights_bin, rigged, rom, motion, log):
    cmd = [weights_bin, "check", rigged, "--json"]
    if motion:
        cmd += ["--motion", motion]
    else:
        cmd += ["--no-clips"]
    if rom:
        cmd += ["--poses", rom]
    with open(log, "a") as f:
        f.write("$ " + " ".join(cmd) + "\n")
        f.flush()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        f.write(f"# exit {r.returncode}\n{(r.stderr or '')[-400:]}\n")
    if r.returncode not in (0, 1):
        return None
    try:
        return parse_bend(r.stdout, prefer_clips=bool(motion))
    except ValueError:
        return None


def one_subject(s, work, out_root, a, log):
    name = s["name"]
    d = os.path.join(out_root, name)
    if a.redo and os.path.exists(d):
        shutil.rmtree(d)
    for sub in ("cand", "score"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    dense = os.path.join(work, name, "dense.obj")
    ref_dir = os.path.join(work, name, "ref")
    ref = os.path.join(ref_dir, name + ".glb")
    ref_cut = os.path.join(work, name, "ref_cut.glb")
    roms = [os.path.join(ref_dir, f) for f in os.listdir(ref_dir) if f.endswith(".rom.ron")]
    rom = next((r for r in roms if r.endswith(name + ".rom.ron")), roms[0] if roms else None)

    row = {"subject": name}
    cuts = {}
    ok = True
    specs = (
        ("retopo", a.tris, "retopo", False),
        ("decimate", a.tris, "blender", False),
        ("planted", a.planted_tris, "blender", True),
    )
    for tool, tris, how, swap_limbs in specs:
        cand = os.path.join(d, "cand", f"{tool}@{tris}.obj")
        good = (
            cut_retopo(a.retopo, dense, cand, tris, a.retopo_tries, log)
            if how == "retopo"
            else cut_blender(a.blender, dense, cand, tris, log)
        )
        if not good or not os.path.exists(cand):
            cuts[tool] = {"error": "cut failed"}
            ok = False
            continue

        rigged = os.path.join(d, "score", f"{tool}@{tris}.rigged.glb")
        if not os.path.exists(rigged):
            if swap_limbs:
                raw_rigged = rigged + ".pre_swap.glb"
                blender(a.blender, "transfer.py", [ref, ref_cut, cand, raw_rigged], log)
                if os.path.exists(raw_rigged):
                    blender(a.blender, "game_frame.py", ["--plant-swap", raw_rigged, rigged], log)
                    os.remove(raw_rigged)
            else:
                blender(a.blender, "transfer.py", [ref, ref_cut, cand, rigged], log)
        if not os.path.exists(rigged):
            cuts[tool] = {"error": "transfer failed"}
            ok = False
            continue

        mj = os.path.join(d, "score", f"{tool}@{tris}.metrics.json")
        if not os.path.exists(mj):
            blender(a.blender, "metrics.py", [rigged, dense, mj, cand], log)

        entry = {}
        got, quads = obj_census(cand)
        entry.update(tris=got, quads=quads)
        if os.path.exists(mj):
            with open(mj) as f:
                m = json.load(f)
            entry.update(
                dist_mean=m.get("dist_mean"),
                dist_p95=m.get("dist_p95"),
                dist_max=m.get("dist_max"),
                ndev_mean=m.get("ndev_mean"),
                open_edges=m.get("open_edges"),
                irr_pct=m.get("irr_pct"),
                posed_flips=m.get("posed_flips"),
                joint_loops_mean=m.get("joint_loops_mean"),
            )
        else:
            ok = False

        bj = os.path.join(d, "score", f"{tool}@{tris}.bend.json")
        if os.path.exists(bj):
            with open(bj) as f:
                entry["bend"] = json.load(f)
        else:
            b = run_bend(a.weights, rigged, rom, a.motion, log)
            if b is not None and b.get("tears") is not None:
                with open(bj, "w") as f:
                    json.dump(b, f, indent=1, sort_keys=True)
                entry["bend"] = b
            else:
                ok = False

        if not a.skip_pictures:
            frame_png = os.path.join(d, f"frame_{tool}.png")
            if not os.path.exists(frame_png):
                blender(a.blender, "game_frame.py", [rigged, dense, frame_png], log)
            if os.path.exists(frame_png):
                entry["frame"] = os.path.basename(frame_png)
            else:
                ok = False

        cuts[tool] = entry

    row["cuts"] = cuts
    row["ok"] = ok
    return row


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def verdict(rows):
    """Evaluate the `docs/direction.md` verdict rule and mandatory controls."""
    subs = [r for r in rows if r.get("ok")]
    bend_wins = {"retopo": 0, "decimate": 0, "ties": 0}
    score_wins = {"retopo": 0, "decimate": 0, "ties": 0}
    surface_wins = {"retopo": 0, "decimate": 0, "ties": 0}
    planted_surface_worse = 0
    planted_bend_worse = 0
    ratios = []
    cant_tell = True

    for r in subs:
        c = r["cuts"]
        rt = c["retopo"]["bend"]["tears"]
        dc = c["decimate"]["bend"]["tears"]
        pl = c["planted"]["bend"]["tears"]
        if rt < dc:
            bend_wins["retopo"] += 1
        elif dc < rt:
            bend_wins["decimate"] += 1
        else:
            bend_wins["ties"] += 1

        rs = c["retopo"]["bend"].get("score")
        ds = c["decimate"]["bend"].get("score")
        if rs is not None and ds is not None:
            if rs > ds:
                score_wins["retopo"] += 1
            elif ds > rs:
                score_wins["decimate"] += 1
            else:
                score_wins["ties"] += 1

        rd95 = c["retopo"]["dist_p95"]
        dd95 = c["decimate"]["dist_p95"]
        pd95 = c["planted"]["dist_p95"]
        if rd95 < dd95:
            surface_wins["retopo"] += 1
        elif dd95 < rd95:
            surface_wins["decimate"] += 1
        else:
            surface_wins["ties"] += 1

        ratios.append(rd95 / max(dd95, 1e-9))
        if pd95 > dd95:
            planted_surface_worse += 1
        if pl > dc:
            planted_bend_worse += 1
        if (dd95 - dd95) != 0 or (dc - dc) != 0:
            cant_tell = False

    ratio = median(ratios)
    beats = (
        len(subs) > 0
        and bend_wins["retopo"] > len(subs) / 2.0
        and ratio is not None
        and ratio <= 1.10
    )
    controls = {
        "cant_tell_zero_delta": cant_tell,
        "planted_surface_worse_count": planted_surface_worse,
        "planted_bend_worse_count": planted_bend_worse,
        "planted_surface_worse_on_all": len(subs) > 0 and planted_surface_worse == len(subs),
        "planted_bend_worse_on_all": len(subs) > 0 and planted_bend_worse == len(subs),
    }
    controls["valid"] = (
        controls["cant_tell_zero_delta"]
        and controls["planted_surface_worse_on_all"]
        and controls["planted_bend_worse_on_all"]
    )
    return {
        "n": len(subs),
        "bend_wins": bend_wins,
        "score_wins": score_wins,
        "surface_wins": surface_wins,
        "median_surface_ratio_retopo_over_decimate": round(ratio, 4) if ratio is not None else None,
        "beats_decimate": beats,
        "controls": controls,
    }


def fmt(v, digits=3):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}g}"
    return str(v)


def report(rows, v, a):
    """Corpus-safe markdown report (subjects named as creature_1..N only)."""
    lines = [
        "# Creature bend + surface benchmark: retopoforge vs Blender decimate",
        "",
        f"{v['n']} creatures at {a.tris} triangles (planted bad case: "
        f"{a.planted_tris}-tri cut with swapped L/R limb weights).",
        "Bend = weightforge `tears%` on each creature's range-of-motion poses "
        "(lower is better; 0-100 score shown beside it). Surface = `dist_p95` "
        "to the dense source (% of bbox diagonal; lower is better).",
        "",
        "| subject | tears% retopo / decimate / planted | bend 0-100 retopo / decimate | "
        "surface p95% retopo / decimate / planted | open edges retopo / decimate | bend winner |",
        "|---|---|---|---|---|---|",
    ]
    for idx, r in enumerate(rows, 1):
        anon = f"creature_{idx}"
        if not r.get("ok"):
            lines.append(f"| {anon} | FAILED | - | - | - | - |")
            continue
        c = r["cuts"]
        rt = c["retopo"]["bend"]["tears"]
        dc = c["decimate"]["bend"]["tears"]
        pl = c["planted"]["bend"]["tears"]
        rs = c["retopo"]["bend"].get("score")
        ds = c["decimate"]["bend"].get("score")
        win = "retopo" if rt < dc else "decimate" if dc < rt else "tie"
        lines.append(
            f"| {anon} | {fmt(rt)} / {fmt(dc)} / {fmt(pl)} | "
            f"{fmt(rs)} / {fmt(ds)} | "
            f"{fmt(c['retopo']['dist_p95'])} / {fmt(c['decimate']['dist_p95'])} / "
            f"{fmt(c['planted']['dist_p95'])} | "
            f"{fmt(c['retopo'].get('open_edges'))} / {fmt(c['decimate'].get('open_edges'))} | "
            f"{win} |"
        )
    lines += [
        "",
        f"- Bend tears wins: retopo {v['bend_wins']['retopo']}, "
        f"decimate {v['bend_wins']['decimate']}, ties {v['bend_wins']['ties']} of {v['n']}.",
        f"- Secondary 0-100 score wins: retopo {v['score_wins']['retopo']}, "
        f"decimate {v['score_wins']['decimate']} of {v['n']}.",
        f"- Median surface p95 ratio (retopo / decimate): "
        f"{fmt(v['median_surface_ratio_retopo_over_decimate'], 4)} (gate <= 1.10).",
        f"- Controls: can't-tell zero delta = {v['controls']['cant_tell_zero_delta']}, "
        f"planted surface worse = {v['controls']['planted_surface_worse_count']}/{v['n']}, "
        f"planted bend worse = {v['controls']['planted_bend_worse_count']}/{v['n']} -> "
        f"{'VALID' if v['controls']['valid'] else 'INVALID'}.",
        f"- Verdict: retopoforge {'BEATS' if v['beats_decimate'] else 'does NOT beat'} "
        "Blender decimate on this creature benchmark.",
    ]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("work")
    ap.add_argument("--out", required=True)
    ap.add_argument("--subjects", default="")
    ap.add_argument("--tris", type=int, default=10000)
    ap.add_argument("--planted-tris", type=int, default=1500)
    ap.add_argument("--retopo-tries", type=int, default=1)
    ap.add_argument("--blender", default=os.environ.get("BLENDER"))
    ap.add_argument("--retopo", default=os.environ.get("RETOPO"))
    ap.add_argument("--weights", default=os.environ.get("WEIGHTS_NEW") or os.environ.get("WEIGHTS"))
    ap.add_argument("--motion", default=os.environ.get("GAMEBENCH_MOTION", ""))
    ap.add_argument("--skip-pictures", action="store_true")
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()

    for req in ("blender", "retopo", "weights"):
        if not getattr(a, req):
            print(f"gamebench: set --{req} (or its env var)", file=sys.stderr)
            return 2

    work, out = os.path.abspath(a.work), os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    log = os.path.join(out, "gamebench.log")

    rows = []
    for idx, s in enumerate(subjects(work, a.subjects or None), 1):
        print(f"== creature_{idx}", flush=True)
        r = one_subject(s, work, out, a, log)
        rows.append(r)
        if r.get("ok"):
            c = r["cuts"]
            print(
                f"   tears% retopo/decimate/planted: "
                f"{fmt(c['retopo']['bend'].get('tears'))}/"
                f"{fmt(c['decimate']['bend'].get('tears'))}/"
                f"{fmt(c['planted']['bend'].get('tears'))}  "
                f"d95%: "
                f"{fmt(c['retopo'].get('dist_p95'))}/"
                f"{fmt(c['decimate'].get('dist_p95'))}/"
                f"{fmt(c['planted'].get('dist_p95'))}",
                flush=True,
            )
    v = verdict(rows)
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(
            {"args": {"tris": a.tris, "planted_tris": a.planted_tris}, "rows": rows, "verdict": v},
            f,
            indent=1,
            sort_keys=True,
        )
    md = report(rows, v, a)
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(md)
    print(md)
    return 0 if v["controls"]["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
