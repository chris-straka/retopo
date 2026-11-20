#!/usr/bin/env python3
"""Measure-only creature bend + surface benchmark at the HLH game camera
(tool-neutral, portable, stdlib only).

    bench/creature/gamebench.py WORK --out OUT [--tools A,B] [--tris 10000]
        [--planted-tris 1500] [--heldout MANIFEST] [--require-heldout]
        [--subjects a,b] [--gate] [--redo]

WORK is a creature-bench workspace (bench/creature/README.md) where each
`tripo` creature already has `dense.obj`, `ref_cut.glb`, `ref/<name>.glb`,
and `ref/<name>.rom.ron` (its own range of motion from the creature route).
Nothing in WORK is modified; all outputs go to OUT.

Per creature, three cuts of the same `dense.obj` plus a self-control:
  challenger  first tool in --tools (default: retopo; or any cut.py mode)
  baseline    second tool in --tools (default: decimate; may equal challenger
              for a self-comparison control)
  planted     coarse decimate (--planted-tris) with left/right limb weights
              swapped after weight transfer: a known unmistakable defect that
              both the metrics and a blind game-camera judge must flag.

Measurements per cut:
  bend      meshweights (`WEIGHTS` or `WEIGHTS_NEW`) `tears_pct` on the
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
  Challenger beats baseline when controls are valid, it wins bend (lower
  `tears`) on strictly more than half the creatures, AND its median `dist_p95`
  is within 10% of baseline's.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MIN_HELDOUT_SUBJECTS = 3


def parse_tools(raw):
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if len(parts) != 2:
        raise ValueError(f"--tools must name CHALLENGER,BASELINE (two tools), got {raw!r}")
    return parts[0], parts[1]


def slot_keys(tools):
    tool_a, tool_b = tools
    if tool_a == tool_b:
        return f"{tool_a}#a", f"{tool_b}#b"
    return tool_a, tool_b


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_heldout_manifest(path):
    """Load a frozen held-out sha256 manifest (JSON or line-based hex, zero names).

    Accepted JSON shapes:
      {"sha256": ["<64-hex>", ...]}
      {"creatures": [{"sha256": "<64-hex>"}, ...]}
      ["<64-hex>", ...]
    Accepted text shape: one 64-char hex sha256 per non-empty non-`#` line.
    Requires at least MIN_HELDOUT_SUBJECTS (3) unique valid sha256 hashes.
    """
    if not os.path.isfile(path):
        raise ValueError(f"--heldout manifest not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        raise ValueError(f"held-out manifest is empty: {path}")

    hashes = []
    if raw.startswith("{") or raw.startswith("["):
        try:
            doc = json.loads(raw)
        except ValueError as e:
            raise ValueError(f"held-out manifest is not valid JSON ({path}): {e}") from e
        if isinstance(doc, list):
            items = doc
        elif isinstance(doc, dict):
            if "sha256" in doc and isinstance(doc["sha256"], list):
                items = doc["sha256"]
            elif "creatures" in doc and isinstance(doc["creatures"], list):
                items = [
                    it["sha256"] if isinstance(it, dict) and "sha256" in it else it
                    for it in doc["creatures"]
                ]
            else:
                raise ValueError(f"held-out manifest missing 'sha256' or 'creatures' list: {path}")
        else:
            raise ValueError(f"held-out manifest has unsupported JSON root: {path}")
        for it in items:
            if not isinstance(it, str):
                raise ValueError(f"held-out manifest entry must be a sha256 string: {it!r}")
            hashes.append(it.strip().lower())
    else:
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            hashes.append(s.split()[0].lower())

    for h in hashes:
        if not SHA256_RE.match(h):
            raise ValueError(f"held-out manifest contains invalid sha256 {h!r} in {path}")
    unique = list(dict.fromkeys(hashes))
    if len(unique) < MIN_HELDOUT_SUBJECTS:
        raise ValueError(
            f"held-out manifest must freeze at least {MIN_HELDOUT_SUBJECTS} unique sha256s, "
            f"got {len(unique)} in {path}"
        )
    return unique


def verify_heldout_workspace(work, manifest_path):
    """Verify that WORK contains a `dense.obj` matching every frozen held-out sha256.
    Returns the matching subject rows in manifest order; raises ValueError on mismatch."""
    expected = load_heldout_manifest(manifest_path)
    by_hash = {}
    for s in subjects(work):
        dense = os.path.join(work, s["name"], "dense.obj")
        if os.path.isfile(dense):
            by_hash[sha256_file(dense)] = s
    missing = [h for h in expected if h not in by_hash]
    if missing:
        raise ValueError(
            f"held-out verification failed for {work}: {len(missing)} of {len(expected)} "
            f"frozen sha256(s) missing or mismatched (first: {missing[0][:12]})"
        )
    return [by_hash[h] for h in expected]


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


def obj_holes(path):
    """Return (hole_count, open_edges) for an OBJ mesh.

    Counts undirected edges shared by only 1 face as open boundary edges, then
    counts connected components of boundary vertices as open holes.
    """
    edge_counts = {}
    with open(path) as f:
        for line in f:
            if not line.startswith("f "):
                continue
            verts = [int(tok.split("/")[0]) for tok in line.split()[1:]]
            nv = len(verts)
            if nv < 3:
                continue
            for i in range(nv):
                a, b = verts[i], verts[(i + 1) % nv]
                if a == b:
                    continue
                e = (a, b) if a < b else (b, a)
                edge_counts[e] = edge_counts.get(e, 0) + 1
    boundary = [e for e, c in edge_counts.items() if c == 1]
    if not boundary:
        return 0, 0
    adj = {}
    for a, b in boundary:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen = set()
    holes = 0
    for start in adj:
        if start in seen:
            continue
        holes += 1
        stack = [start]
        seen.add(start)
        while stack:
            cur = stack.pop()
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
    return holes, len(boundary)


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
    tsv = os.path.join(work, "subjects.tsv")
    if not os.path.isfile(tsv):
        raise ValueError(f"workspace missing subjects.tsv: {tsv}")
    rows = []
    with open(tsv) as f:
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


def cut_blender(blender_bin, mode, dense, out, tris, log):
    if os.path.exists(out):
        return True
    code = blender(blender_bin, "cut.py", [mode, dense, out, str(tris)], log)
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
        # Older meshweights fallback if `headline` is absent: invert 0-100 score.
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


def _measure_cut(slot, tool, tris, swap_limbs, dense, ref, ref_cut, rom, d, a, log):
    cand = os.path.join(d, "cand", f"{slot}@{tris}.obj")
    good = (
        cut_retopo(a.retopo, dense, cand, tris, a.retopo_tries, log)
        if tool == "retopo"
        else cut_blender(a.blender, tool, dense, cand, tris, log)
    )
    if not good or not os.path.exists(cand):
        return {"error": "cut failed"}, False

    rigged = os.path.join(d, "score", f"{slot}@{tris}.rigged.glb")
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
        return {"error": "transfer failed"}, False

    mj = os.path.join(d, "score", f"{slot}@{tris}.metrics.json")
    if not os.path.exists(mj):
        blender(a.blender, "metrics.py", [rigged, dense, mj, cand], log)

    ok = True
    entry = {}
    got, quads = obj_census(cand)
    holes, open_e = obj_holes(cand)
    entry.update(tris=got, quads=quads, holes=holes, open_edges=open_e)
    if os.path.exists(mj):
        with open(mj) as f:
            m = json.load(f)
        entry.update(
            dist_mean=m.get("dist_mean"),
            dist_p95=m.get("dist_p95"),
            dist_max=m.get("dist_max"),
            ndev_mean=m.get("ndev_mean"),
            open_edges=m.get("open_edges") if m.get("open_edges") is not None else open_e,
            irr_pct=m.get("irr_pct"),
            posed_flips=m.get("posed_flips"),
            joint_loops_mean=m.get("joint_loops_mean"),
        )
    else:
        ok = False

    bj = os.path.join(d, "score", f"{slot}@{tris}.bend.json")
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
        frame_png = os.path.join(d, f"frame_{slot}.png")
        if not os.path.exists(frame_png):
            blender(a.blender, "game_frame.py", [rigged, dense, frame_png], log)
        if os.path.exists(frame_png):
            entry["frame"] = os.path.basename(frame_png)
        else:
            ok = False

    if getattr(a, "image_check", False):
        dense_rigged = os.path.join(os.path.dirname(dense), "dense_rigged.glb")
        ref_for_img = dense_rigged if os.path.exists(dense_rigged) else ref
        ij = os.path.join(d, "score", f"{slot}@{tris}.image_check.json")
        if not os.path.exists(ij):
            cache = os.path.join(d, "score", "image_check_ref_cache")
            blender(
                a.blender,
                "posed_image_check.py",
                [ref_for_img, rigged, dense, ij, "--frames", "8", "--cache", cache],
                log,
            )
        if os.path.exists(ij):
            with open(ij) as f:
                entry["image_check"] = json.load(f)
        else:
            ok = False

    return entry, ok


def one_subject(s, work, out_root, a, log, tools=("retopo", "decimate")):
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

    tool_a, tool_b = tools
    slot_a, slot_b = slot_keys(tools)

    row = {"subject": name}
    cuts = {}
    ok = True

    entry_a, ok_a = _measure_cut(slot_a, tool_a, a.tris, False, dense, ref, ref_cut, rom, d, a, log)
    cuts[slot_a] = entry_a
    ok = ok and ok_a

    if tool_a == tool_b:
        # Self-comparison control: duplicate exact outputs into slot_b so A vs B delta is tested honestly.
        for suffix in (f"@{a.tris}.obj",):
            src_c = os.path.join(d, "cand", f"{slot_a}{suffix}")
            dst_c = os.path.join(d, "cand", f"{slot_b}{suffix}")
            if os.path.exists(src_c) and not os.path.exists(dst_c):
                shutil.copyfile(src_c, dst_c)
        entry_b, ok_b = _measure_cut(slot_b, tool_b, a.tris, False, dense, ref, ref_cut, rom, d, a, log)
        cuts[slot_b] = entry_b
        ok = ok and ok_b
        cuts["control"] = entry_b
    else:
        entry_b, ok_b = _measure_cut(slot_b, tool_b, a.tris, False, dense, ref, ref_cut, rom, d, a, log)
        cuts[slot_b] = entry_b
        ok = ok and ok_b
        # Re-read baseline bend/metrics as explicit self-control entry.
        cuts["control"] = json.loads(json.dumps(entry_b))

    entry_p, ok_p = _measure_cut("planted", "decimate", a.planted_tris, True, dense, ref, ref_cut, rom, d, a, log)
    cuts["planted"] = entry_p
    ok = ok and ok_p

    row["cuts"] = cuts
    row["ok"] = ok
    return row


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def _resolve_slots(cuts, tools):
    tool_a, tool_b = tools
    sa, sb = slot_keys(tools)
    ka = sa if sa in cuts else tool_a
    kb = sb if sb in cuts else tool_b
    return ka, kb


def verdict(rows, tools=("retopo", "decimate")):
    """Evaluate the neutral challenger-vs-baseline verdict rule and mandatory controls."""
    tool_a, tool_b = tools
    same_tool = tool_a == tool_b
    win_a_key, win_b_key = slot_keys(tools)

    subs = [r for r in rows if r.get("ok")]
    bend_wins = {win_a_key: 0, win_b_key: 0, "ties": 0}
    score_wins = {win_a_key: 0, win_b_key: 0, "ties": 0}
    surface_wins = {win_a_key: 0, win_b_key: 0, "ties": 0}
    planted_surface_worse = 0
    planted_bend_worse = 0
    planted_image_worse = 0
    image_checked_rows = 0
    holes_checked_rows = 0
    holes_no_worse_count = 0
    challenger_holes = []
    baseline_holes = []
    ratios = []
    cant_tell = True

    for r in subs:
        c = r["cuts"]
        ka, kb = _resolve_slots(c, tools)
        ca, cb, cp = c[ka], c[kb], c["planted"]

        at = ca["bend"]["tears"]
        bt = cb["bend"]["tears"]
        pt = cp["bend"]["tears"]
        if at < bt:
            bend_wins[win_a_key] += 1
        elif bt < at:
            bend_wins[win_b_key] += 1
        else:
            bend_wins["ties"] += 1

        asc = ca["bend"].get("score")
        bsc = cb["bend"].get("score")
        if asc is not None and bsc is not None:
            if asc > bsc:
                score_wins[win_a_key] += 1
            elif bsc > asc:
                score_wins[win_b_key] += 1
            else:
                score_wins["ties"] += 1

        ad95 = ca["dist_p95"]
        bd95 = cb["dist_p95"]
        pd95 = cp["dist_p95"]
        if ad95 < bd95:
            surface_wins[win_a_key] += 1
        elif bd95 < ad95:
            surface_wins[win_b_key] += 1
        else:
            surface_wins["ties"] += 1

        ratios.append(ad95 / max(bd95, 1e-9))
        if pd95 > bd95:
            planted_surface_worse += 1
        if pt > bt:
            planted_bend_worse += 1

        bi = cb.get("image_check")
        pi = cp.get("image_check")
        if bi is not None and pi is not None:
            image_checked_rows += 1
            if (
                pi.get("sil_diff_mean_pct", 0.0) > bi.get("sil_diff_mean_pct", 0.0)
                and pi.get("shade_rmse_mean", 0.0) > bi.get("shade_rmse_mean", 0.0)
            ):
                planted_image_worse += 1

        ah = ca.get("holes")
        bh = cb.get("holes")
        if ah is not None and bh is not None:
            holes_checked_rows += 1
            challenger_holes.append(int(ah))
            baseline_holes.append(int(bh))
            if int(ah) <= int(bh):
                holes_no_worse_count += 1

        # Real self-control check: when an explicit `control` entry is present,
        # verify exact zero delta against baseline; in self-comparison mode also
        # verify challenger A and baseline B have zero delta.
        ctrl = c.get("control")
        if ctrl is not None:
            ct = ctrl["bend"]["tears"]
            cd95 = ctrl["dist_p95"]
            if ct != bt or cd95 != bd95:
                cant_tell = False
        if same_tool and (at != bt or ad95 != bd95):
            cant_tell = False

    ratio = median(ratios)
    controls = {
        "cant_tell_zero_delta": cant_tell,
        "planted_surface_worse_count": planted_surface_worse,
        "planted_bend_worse_count": planted_bend_worse,
        "planted_surface_worse_on_all": len(subs) > 0 and planted_surface_worse == len(subs),
        "planted_bend_worse_on_all": len(subs) > 0 and planted_bend_worse == len(subs),
    }
    if image_checked_rows > 0:
        controls["planted_image_worse_count"] = planted_image_worse
        controls["planted_image_worse_on_all"] = (
            len(subs) > 0 and image_checked_rows == len(subs) and planted_image_worse == len(subs)
        )
    controls["valid"] = (
        controls["cant_tell_zero_delta"]
        and controls["planted_surface_worse_on_all"]
        and controls["planted_bend_worse_on_all"]
        and controls.get("planted_image_worse_on_all", True)
    )

    beats = (
        controls["valid"]
        and not same_tool
        and len(subs) > 0
        and bend_wins[win_a_key] > len(subs) / 2.0
        and ratio is not None
        and ratio <= 1.10
    )
    winner = tool_a if beats else None

    out = {
        "tools": {"challenger": tool_a, "baseline": tool_b},
        "n": len(subs),
        "bend_wins": bend_wins,
        "score_wins": score_wins,
        "surface_wins": surface_wins,
        "median_surface_ratio": round(ratio, 4) if ratio is not None else None,
        "challenger_beats_baseline": beats,
        "winner": winner,
        "controls": controls,
    }
    if holes_checked_rows > 0:
        out["holes_no_worse_on_all"] = (
            len(subs) > 0 and holes_checked_rows == len(subs) and holes_no_worse_count == len(subs)
        )
        out["challenger_holes_max"] = max(challenger_holes)
        out["baseline_holes_max"] = max(baseline_holes)
    # Legacy alias keys for backward compatibility with older scripts/tests.
    out["beats_decimate"] = beats if tool_b == "decimate" else False
    if tools == ("retopo", "decimate"):
        out["median_surface_ratio_retopo_over_decimate"] = out["median_surface_ratio"]
    return out


def fmt(v, digits=3):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}g}"
    return str(v)


def report(rows, v, a, tools=None):
    """Corpus-safe markdown report (subjects named as creature_1..N only)."""
    if tools is None:
        if "tools" in v:
            tools = (v["tools"]["challenger"], v["tools"]["baseline"])
        elif hasattr(a, "tools") and getattr(a, "tools"):
            tools = parse_tools(a.tools)
        else:
            tools = ("retopo", "decimate")
    tool_a, tool_b = tools
    win_a_key, win_b_key = slot_keys(tools)

    lines = [
        f"# Creature bend + surface benchmark: {tool_a} vs {tool_b}",
        "",
        f"{v['n']} creatures at {a.tris} triangles (planted bad case: "
        f"{a.planted_tris}-tri cut with swapped L/R limb weights).",
        "Bend = meshweights `tears%` on each creature's range-of-motion poses "
        "(lower is better; 0-100 score shown beside it). Surface = `dist_p95` "
        "to the dense source (% of bbox diagonal; lower is better).",
        "",
        f"| subject | tears% {tool_a} / {tool_b} / planted | bend 0-100 {tool_a} / {tool_b} | "
        f"surface p95% {tool_a} / {tool_b} / planted | open edges {tool_a} / {tool_b} | bend winner |",
        "|---|---|---|---|---|---|",
    ]
    for idx, r in enumerate(rows, 1):
        anon = f"creature_{idx}"
        if not r.get("ok"):
            lines.append(f"| {anon} | FAILED | - | - | - | - |")
            continue
        c = r["cuts"]
        ka, kb = _resolve_slots(c, tools)
        ca, cb, cp = c[ka], c[kb], c["planted"]
        at = ca["bend"]["tears"]
        bt = cb["bend"]["tears"]
        pt = cp["bend"]["tears"]
        asc = ca["bend"].get("score")
        bsc = cb["bend"].get("score")
        win = tool_a if at < bt else tool_b if bt < at else "tie"
        lines.append(
            f"| {anon} | {fmt(at)} / {fmt(bt)} / {fmt(pt)} | "
            f"{fmt(asc)} / {fmt(bsc)} | "
            f"{fmt(ca['dist_p95'])} / {fmt(cb['dist_p95'])} / "
            f"{fmt(cp['dist_p95'])} | "
            f"{fmt(ca.get('open_edges'))} / {fmt(cb.get('open_edges'))} | "
            f"{win} |"
        )
    lines += [
        "",
        f"- Bend tears wins: {tool_a} {v['bend_wins'][win_a_key]}, "
        f"{tool_b} {v['bend_wins'][win_b_key]}, ties {v['bend_wins']['ties']} of {v['n']}.",
        f"- Secondary 0-100 score wins: {tool_a} {v['score_wins'][win_a_key]}, "
        f"{tool_b} {v['score_wins'][win_b_key]} of {v['n']}.",
        f"- Median surface p95 ratio ({tool_a} / {tool_b}): "
        f"{fmt(v['median_surface_ratio'], 4)} (gate <= 1.10).",
        f"- Controls: can't-tell zero delta = {v['controls']['cant_tell_zero_delta']}, "
        f"planted surface worse = {v['controls']['planted_surface_worse_count']}/{v['n']}, "
        f"planted bend worse = {v['controls']['planted_bend_worse_count']}/{v['n']} -> "
        f"{'VALID' if v['controls']['valid'] else 'INVALID'}.",
        f"- Verdict: {tool_a} {'BEATS' if v['challenger_beats_baseline'] else 'does NOT beat'} "
        f"{tool_b} on this creature benchmark (winner: {v['winner'] or 'none'}).",
    ]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("work")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tools", default="retopo,decimate",
                    help="CHALLENGER,BASELINE cut pair (e.g. retopo,decimate or decimate,decimate)")
    ap.add_argument("--subjects", default="")
    ap.add_argument("--heldout", default=os.environ.get("GAMEBENCH_HELDOUT", ""),
                    help="Frozen held-out sha256 manifest file")
    ap.add_argument("--require-heldout", action="store_true",
                    help="Refuse to run (exit 2) unless --heldout is present and verified")
    ap.add_argument("--tris", type=int, default=10000)
    ap.add_argument("--planted-tris", type=int, default=1500)
    ap.add_argument("--retopo-tries", type=int, default=1)
    ap.add_argument("--blender", default=os.environ.get("BLENDER"))
    ap.add_argument("--retopo", default=os.environ.get("RETOPO"))
    ap.add_argument("--weights", default=os.environ.get("WEIGHTS") or os.environ.get("WEIGHTS_NEW"))
    ap.add_argument("--motion", default=os.environ.get("GAMEBENCH_MOTION", ""))
    ap.add_argument("--skip-pictures", action="store_true")
    ap.add_argument("--image-check", action="store_true",
                    help="Run 8-frame posed game-camera silhouette + shading check against reference")
    ap.add_argument("--gate", action="store_true",
                    help="Exit 1 unless controls are valid AND challenger beats baseline")
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()

    try:
        tools = parse_tools(a.tools)
    except ValueError as e:
        print(f"gamebench: {e}", file=sys.stderr)
        return 2

    required = [("blender", "BLENDER"), ("weights", "WEIGHTS")]
    if "retopo" in tools:
        required.append(("retopo", "RETOPO"))
    for attr, env_name in required:
        if not getattr(a, attr):
            print(f"gamebench: missing --{attr} (or env var {env_name})", file=sys.stderr)
            return 2

    work, out = os.path.abspath(a.work), os.path.abspath(a.out)

    if a.require_heldout and not a.heldout:
        print("gamebench: --require-heldout requires --heldout MANIFEST (or GAMEBENCH_HELDOUT)",
              file=sys.stderr)
        return 2

    try:
        if a.heldout:
            subj_rows = verify_heldout_workspace(work, a.heldout)
            if a.subjects:
                keep = set(a.subjects.split(","))
                subj_rows = [r for r in subj_rows if r["name"] in keep]
        else:
            subj_rows = subjects(work, a.subjects or None)
    except ValueError as e:
        print(f"gamebench: {e}", file=sys.stderr)
        return 2

    os.makedirs(out, exist_ok=True)
    log = os.path.join(out, "gamebench.log")

    rows = []
    for idx, s in enumerate(subj_rows, 1):
        print(f"== creature_{idx}", flush=True)
        r = one_subject(s, work, out, a, log, tools=tools)
        rows.append(r)
        if r.get("ok"):
            c = r["cuts"]
            ka, kb = _resolve_slots(c, tools)
            print(
                f"   tears% {tools[0]}/{tools[1]}/planted: "
                f"{fmt(c[ka]['bend'].get('tears'))}/"
                f"{fmt(c[kb]['bend'].get('tears'))}/"
                f"{fmt(c['planted']['bend'].get('tears'))}  "
                f"d95%: "
                f"{fmt(c[ka].get('dist_p95'))}/"
                f"{fmt(c[kb].get('dist_p95'))}/"
                f"{fmt(c['planted'].get('dist_p95'))}",
                flush=True,
            )
    v = verdict(rows, tools=tools)
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(
            {
                "tools": list(tools),
                "args": {"tris": a.tris, "planted_tris": a.planted_tris},
                "rows": rows,
                "verdict": v,
            },
            f,
            indent=1,
            sort_keys=True,
        )
    with open(os.path.join(out, "verdict.json"), "w") as f:
        json.dump(v, f, indent=1, sort_keys=True)
    md = report(rows, v, a, tools=tools)
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(md)
    print(md)
    if not v["controls"]["valid"]:
        return 1
    if a.gate and not v["challenger_beats_baseline"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
