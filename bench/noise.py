#!/usr/bin/env python3
"""retopo noise-floor + best-of-K harness (stdlib only).

For each model x target, remeshes K deterministic sub-visible
perturbations of the input (seed 0 = the unmodified file; seeds 1..K-1
jitter every vertex by up to +-JITTER/2 of the bbox diagonal), scores each
output with bench/score.py, and reports per-metric spread plus the
best-of-K pick (rank sum, no tuned weights) against seed 0.

Why: the pipeline makes integer decisions (decimation collapse order,
singularity cancellation ties, extraction cliffs) that flip under 1e-9
input noise, so one run per configuration cannot tell an engine change
from noise. A change is real only if it moves the distribution.

Usage:
  bench/noise.py [--models armadillo.obj,beast.obj] [--targets 1000,5000]
                 [--seeds 8] [--binary rust/target/release/retopo]
                 [--json out.json] [-- extra retopo args...]
"""

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JITTER = 1e-9
REPORT_METRICS = ("quads", "yield", "irr_pct", "angdev_mean", "angdev_p95",
                  "dist_mean", "dist_max", "non_quads")


def write_jittered(verts, faces, path, seed, diag):
    # Identical positions get identical offsets: exports split vertices
    # at uv seams, and independent jitter would unweld them (topology
    # change, not noise).
    rng = random.Random(seed)
    eps = JITTER * diag
    offsets = {}
    with open(path, "w", encoding="utf-8") as f:
        for v in verts:
            if v not in offsets:
                offsets[v] = tuple((rng.random() - 0.5) * eps for _ in v)
            f.write("v %.17g %.17g %.17g\n" % tuple(
                c + o for c, o in zip(v, offsets[v])))
        for face in faces:
            f.write("f " + " ".join(str(i + 1) for i in face) + "\n")


def run_case(binary, model_path, target, seeds, extra, source, tmp):
    verts, faces = source
    diag = score.bbox_diag(verts)
    rows = []
    for seed in range(seeds):
        if seed == 0:
            inp = model_path
        else:
            inp = os.path.join(tmp, "in.obj")
            write_jittered(verts, faces, inp, seed, diag)
        out = os.path.join(tmp, f"out{seed}.obj")
        if os.path.exists(out):
            os.remove(out)
        cmd = [binary, "--input", inp, "--output", out,
               "--target-quads", str(target), "--quiet"] + extra
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError) as exc:
            rows.append({"seed": seed, "failed": True, "error": str(exc)})
            continue
        try:
            if proc.returncode != 0 or not os.path.exists(out):
                rows.append({"seed": seed, "failed": True})
                continue
            try:
                m = score.score(model_path, out, target, source=source)
            except (OSError, ValueError) as exc:
                rows.append({"seed": seed, "failed": True, "error": str(exc)})
                continue
            m["seed"] = seed
            if m.get("empty"):
                m["failed"] = True
            rows.append(m)
        finally:
            if os.path.exists(out):
                os.remove(out)
    return rows


def fmt(v):
    return f"{v:.3g}" if isinstance(v, float) else str(v)


def summarize(name, target, rows):
    ok = [r for r in rows if not r.get("failed") and not r.get("empty")]
    print(f"\n### {name} @ {target}  ({len(ok)}/{len(rows)} runs ok)")
    if not ok:
        return {}
    pick = ok[score.rank_sum_pick(ok)]
    base = next((r for r in ok if r["seed"] == 0), None)
    print("| metric | seed0 | median | min | max | best-of-K "
          f"(seed {pick['seed']}) |")
    print("|---|---:|---:|---:|---:|---:|")
    out = {"pick_seed": pick["seed"], "metrics": {}}
    for key in REPORT_METRICS:
        vals = [r[key] for r in ok if key in r]
        if not vals:
            continue
        b = base.get(key) if base else None
        print(f"| {key} | {fmt(b) if b is not None else '-'} | "
              f"{fmt(statistics.median(vals))} | {fmt(min(vals))} | "
              f"{fmt(max(vals))} | {fmt(pick[key])} |")
        out["metrics"][key] = {"seed0": b, "median": statistics.median(vals),
                               "min": min(vals), "max": max(vals),
                               "pick": pick[key]}
    return out


def main(argv):
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--models", default="armadillo.obj,beast.obj")
    ap.add_argument("--targets", default="1000,5000")
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--binary",
                    default=os.path.join(ROOT, "rust/target/release/retopo"))
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    if args.seeds < 1:
        print(f"noise: --seeds must be >= 1, got {args.seeds}",
              file=sys.stderr)
        return 2
    try:
        targets = [int(t) for t in args.targets.split(",") if t.strip()]
    except ValueError as exc:
        print(f"noise: invalid --targets {args.targets!r}: {exc}",
              file=sys.stderr)
        return 2
    if not targets or any(t <= 0 for t in targets):
        print(f"noise: --targets must be positive integers, got {args.targets!r}",
              file=sys.stderr)
        return 2
    if not os.path.exists(args.binary):
        print(f"binary not found: {args.binary} "
              f"(build with cargo first: cargo build --locked --release -p retopo)",
              file=sys.stderr)
        return 2

    loaded = []
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        path = model if os.path.isabs(model) else os.path.join(
            ROOT, "bench", "models", model)
        if not os.path.exists(path):
            print(f"model not found: {path} (run bench/fetch_models.sh)",
                  file=sys.stderr)
            return 2
        try:
            source = score.load_obj(path)
            if not source[0] or not source[1]:
                raise ValueError(f"empty source mesh: {path}")
            if score.bbox_diag(source[0]) == 0.0:
                raise ValueError(f"zero-extent source mesh: {path}")
        except (OSError, ValueError) as exc:
            print(f"noise: {exc}", file=sys.stderr)
            return 2
        loaded.append((path, source))
    if not loaded:
        print("noise: --models must name at least one model", file=sys.stderr)
        return 2

    report = {"seeds": args.seeds, "jitter": JITTER, "extra": extra,
              "cases": {}}
    any_failed = False
    with tempfile.TemporaryDirectory(prefix="retopo_noise_") as tmp:
        for path, source in loaded:
            for target in targets:
                rows = run_case(args.binary, path, target, args.seeds, extra,
                                source, tmp)
                if any(r.get("failed") or r.get("empty") for r in rows):
                    any_failed = True
                summary = summarize(os.path.basename(path), target, rows)
                report["cases"][f"{os.path.basename(path)}@{target}"] = {
                    "rows": rows, "summary": summary}
                sys.stdout.flush()
    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=1)
        except OSError as exc:
            print(f"noise: cannot write {args.json}: {exc}", file=sys.stderr)
            return 2
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
