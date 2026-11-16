#!/usr/bin/env python3
"""Blind same-frame A/B pair builder, PNG sheet composer, and scorer (stdlib only).

    bench/creature/blind.py build OUT [--seed 20261115]
    bench/creature/blind.py score BLIND_DIR ANSWERS.json [--key KEY.json] [--out VERDICT.json]

Protocol (blind-gamecam-bench):
1. Same frame, HLH game camera, no wireframes or labels inside the tiles.
2. Seeded coin flip (LEFT vs RIGHT) and seeded pair-order shuffle.
3. Unmarked judged directory (OUT/blind/): holds ONLY pair_01.png..pair_NN.png
   and manifest.json. Never holds tool names, creature names, scores, or keys.
4. External answer key: written BESIDE the judged directory as OUT/blind.key.json,
   never inside OUT/blind/. Verdict written beside it as OUT/blind.verdict.json.
5. Mandatory gates in every run:
   - Can't-tell control: identical render on both sides; pixel RMSE below the
     56 px header bar is asserted to be 0.0. Any answer other than cant_tell
     discards the judge's entire run (exit 2).
   - Planted bad case: coarse cut with swapped left/right limb weights. The
     blind judge must pick the unplanted side AND the benchmark's own bend and
     surface metrics must both flag the planted copy.
"""

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import struct
import sys
import zlib

HEADER_H = 56
BG_HEADER = (24, 26, 31)
LINE_HEADER = (52, 56, 66)
FG_TITLE = (232, 234, 238)
FG_SUB = (156, 162, 174)

QUESTION = (
    "Same creature, same stride frame, HLH follow camera. Which side's mesh "
    "holds its shape and bends cleaner at the joints (no torn skin, spikes, "
    "holes, or lost parts)? Answer left, right, or cant_tell."
)

# Compact 5x7 uppercase + digit + punctuation bitmap font (rows MSB..LSB of 5 bits).
FONT_5X7 = {
    " ": (0, 0, 0, 0, 0, 0, 0),
    "-": (0, 0, 0, 31, 0, 0, 0),
    ".": (0, 0, 0, 0, 0, 12, 12),
    ":": (0, 12, 12, 0, 12, 12, 0),
    "/": (1, 2, 2, 4, 8, 8, 16),
    "%": (25, 25, 2, 4, 8, 19, 19),
    "(": (2, 4, 8, 8, 8, 4, 2),
    ")": (8, 4, 2, 2, 2, 4, 8),
    "?": (14, 17, 1, 2, 4, 0, 4),
    "'": (4, 4, 0, 0, 0, 0, 0),
    "0": (14, 17, 19, 21, 25, 17, 14),
    "1": (4, 12, 4, 4, 4, 4, 14),
    "2": (14, 17, 1, 2, 4, 8, 31),
    "3": (31, 2, 4, 2, 1, 17, 14),
    "4": (2, 6, 10, 18, 31, 2, 2),
    "5": (31, 16, 30, 1, 1, 17, 14),
    "6": (6, 8, 16, 30, 17, 17, 14),
    "7": (31, 1, 2, 4, 8, 8, 8),
    "8": (14, 17, 17, 14, 17, 17, 14),
    "9": (14, 17, 17, 15, 1, 2, 12),
    "A": (14, 17, 17, 31, 17, 17, 17),
    "B": (30, 17, 17, 30, 17, 17, 30),
    "C": (14, 17, 16, 16, 16, 17, 14),
    "D": (28, 18, 17, 17, 17, 18, 28),
    "E": (31, 16, 16, 30, 16, 16, 31),
    "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (14, 17, 16, 23, 17, 17, 15),
    "H": (17, 17, 17, 31, 17, 17, 17),
    "I": (14, 4, 4, 4, 4, 4, 14),
    "J": (7, 2, 2, 2, 2, 18, 12),
    "K": (17, 18, 20, 24, 20, 18, 17),
    "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17),
    "N": (17, 25, 21, 19, 17, 17, 17),
    "O": (14, 17, 17, 17, 17, 17, 14),
    "P": (30, 17, 17, 30, 16, 16, 16),
    "Q": (14, 17, 17, 17, 21, 18, 13),
    "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (15, 16, 16, 14, 1, 1, 30),
    "T": (31, 4, 4, 4, 4, 4, 4),
    "U": (17, 17, 17, 17, 17, 17, 14),
    "V": (17, 17, 17, 17, 17, 10, 4),
    "W": (17, 17, 17, 21, 21, 21, 10),
    "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 10, 4, 4, 4, 4),
    "Z": (31, 1, 2, 4, 8, 16, 31),
}


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path):
    """Read an 8-bit non-interlaced RGB or RGBA PNG into (w, h, bytearray_rgb)."""
    with open(path, "rb") as fp:
        data = fp.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    pos = 8
    w = h = bit_depth = color_type = interlace = None
    idat = bytearray()
    while pos < len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            w, h, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif ctype == b"IDAT":
            idat.extend(chunk)
        elif ctype == b"IEND":
            break
    if bit_depth != 8 or color_type not in (2, 6) or interlace != 0:
        raise ValueError(f"{path}: unsupported PNG format depth={bit_depth} color={color_type} interlace={interlace}")
    bpp = 3 if color_type == 2 else 4
    stride = w * bpp
    raw = zlib.decompress(bytes(idat))
    prev = bytearray(stride)
    cur = bytearray(stride)
    out = bytearray(w * h * 3)
    src_i = 0
    dst_i = 0
    for _ in range(h):
        ftype = raw[src_i]
        src_i += 1
        row = raw[src_i:src_i + stride]
        src_i += stride
        if ftype == 0:
            cur[:] = row
        elif ftype == 1:
            for x in range(stride):
                cur[x] = (row[x] + (cur[x - bpp] if x >= bpp else 0)) & 0xFF
        elif ftype == 2:
            for x in range(stride):
                cur[x] = (row[x] + prev[x]) & 0xFF
        elif ftype == 3:
            for x in range(stride):
                a = cur[x - bpp] if x >= bpp else 0
                cur[x] = (row[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif ftype == 4:
            for x in range(stride):
                a = cur[x - bpp] if x >= bpp else 0
                c = prev[x - bpp] if x >= bpp else 0
                cur[x] = (row[x] + _paeth(a, prev[x], c)) & 0xFF
        else:
            raise ValueError(f"{path}: bad PNG filter {ftype}")
        if bpp == 3:
            out[dst_i:dst_i + stride] = cur
            dst_i += stride
        else:
            for x in range(0, stride, 4):
                out[dst_i] = cur[x]
                out[dst_i + 1] = cur[x + 1]
                out[dst_i + 2] = cur[x + 2]
                dst_i += 3
        prev, cur = cur, prev
    return w, h, out


def _png_chunk(ctype, payload):
    crc = zlib.crc32(ctype)
    crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)


def write_png(path, w, h, rgb):
    """Write an 8-bit RGB buffer (len == w * h * 3) as a PNG."""
    stride = w * 3
    raw = bytearray((stride + 1) * h)
    for y in range(h):
        ro = y * (stride + 1)
        so = y * stride
        raw[ro] = 0
        raw[ro + 1:ro + 1 + stride] = rgb[so:so + stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    comp = zlib.compress(bytes(raw), level=6)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", comp))
        f.write(_png_chunk(b"IEND", b""))


def fill_rect(rgb, w, h, x0, y0, rw, rh, color):
    x1 = min(w, max(0, x0 + rw))
    y1 = min(h, max(0, y0 + rh))
    x0 = max(0, x0)
    y0 = max(0, y0)
    if x1 <= x0 or y1 <= y0:
        return
    row_bytes = bytes(color) * (x1 - x0)
    n = (x1 - x0) * 3
    for y in range(y0, y1):
        o = (y * w + x0) * 3
        rgb[o:o + n] = row_bytes


def draw_text(rgb, w, h, x, y, text, scale=2, color=FG_TITLE):
    cx = x
    r, g, b = color
    for ch in text.upper():
        glyph = FONT_5X7.get(ch, FONT_5X7["?"])
        for gy, bits in enumerate(glyph):
            for gx in range(5):
                if bits & (1 << (4 - gx)):
                    for sy in range(scale):
                        py = y + gy * scale + sy
                        if 0 <= py < h:
                            for sx in range(scale):
                                px = cx + gx * scale + sx
                                if 0 <= px < w:
                                    o = (py * w + px) * 3
                                    rgb[o] = r
                                    rgb[o + 1] = g
                                    rgb[o + 2] = b
        cx += 6 * scale


def compose_pair_sheet(left_rgb, right_rgb, tile_w, tile_h, pair_num):
    """Side-by-side sheet with header bar ONLY above HEADER_H.
    Below HEADER_H, left and right halves are exact unmodified copies of
    left_rgb and right_rgb so a self-control has RMSE == 0.0 below header."""
    sw = tile_w * 2
    sh = tile_h + HEADER_H
    out = bytearray(sw * sh * 3)
    fill_rect(out, sw, sh, 0, 0, sw, HEADER_H, BG_HEADER)
    fill_rect(out, sw, sh, 0, HEADER_H - 1, sw, 1, LINE_HEADER)
    draw_text(out, sw, sh, 14, 10, f"PAIR {pair_num:02d}", scale=2, color=FG_TITLE)
    draw_text(out, sw, sh, 120, 13, "WHICH SIDE HOLDS SHAPE AND BENDS CLEANER? (LEFT / RIGHT / CANT_TELL)",
              scale=1, color=FG_SUB)
    lw = len("LEFT") * 6 * 2
    rw = len("RIGHT") * 6 * 2
    draw_text(out, sw, sh, (tile_w - lw) // 2, 34, "LEFT", scale=2, color=FG_TITLE)
    draw_text(out, sw, sh, tile_w + (tile_w - rw) // 2, 34, "RIGHT", scale=2, color=FG_TITLE)

    row_bytes = tile_w * 3
    for y in range(tile_h):
        dst = (y + HEADER_H) * sw * 3
        src = y * row_bytes
        out[dst:dst + row_bytes] = left_rgb[src:src + row_bytes]
        out[dst + row_bytes:dst + 2 * row_bytes] = right_rgb[src:src + row_bytes]
    return sw, sh, out


def rmse_below_header(sheet_rgb, sheet_w, sheet_h, header_h=HEADER_H):
    """Root-mean-square pixel difference (0..255 scale) between the left half
    and right half strictly below header_h."""
    if sheet_w % 2 != 0 or sheet_h <= header_h:
        raise ValueError("invalid sheet dimensions")
    half_w = sheet_w // 2
    half_bytes = half_w * 3
    total_sq = 0
    count = 0
    for y in range(header_h, sheet_h):
        base = y * sheet_w * 3
        left = sheet_rgb[base:base + half_bytes]
        right = sheet_rgb[base + half_bytes:base + 2 * half_bytes]
        if left != right:
            for a, b in zip(left, right):
                d = a - b
                total_sq += d * d
        count += half_bytes
    return math.sqrt(total_sq / count) if count else 0.0


def downsample_2x(rgb, w, h):
    """Box-filter 2x downsample for compact phone-friendly summary sheets."""
    nw, nh = max(1, w // 2), max(1, h // 2)
    out = bytearray(nw * nh * 3)
    for y in range(nh):
        y0, y1 = 2 * y, min(h - 1, 2 * y + 1)
        for x in range(nw):
            x0, x1 = 2 * x, min(w - 1, 2 * x + 1)
            o00 = (y0 * w + x0) * 3
            o01 = (y0 * w + x1) * 3
            o10 = (y1 * w + x0) * 3
            o11 = (y1 * w + x1) * 3
            do = (y * nw + x) * 3
            for c in range(3):
                out[do + c] = (rgb[o00 + c] + rgb[o01 + c] + rgb[o10 + c] + rgb[o11 + c] + 2) >> 2
    return nw, nh, out


def compose_summary_sheet(rows, out_dir, dest_path):
    """Write an anonymized visual summary grid (CREATURE 1..N: DECIMATE vs RETOPO)
    OUTSIDE the blind folder so the owner can inspect results without corpus names."""
    ok_rows = [r for r in rows if r.get("ok")]
    if not ok_rows:
        return None
    tiles = []
    tw = th = None
    for idx, r in enumerate(ok_rows, 1):
        d = os.path.join(out_dir, r["subject"])
        p_dec = os.path.join(d, "frame_decimate.png")
        p_ret = os.path.join(d, "frame_retopo.png")
        if not (os.path.exists(p_dec) and os.path.exists(p_ret)):
            continue
        w1, h1, rgb_dec = read_png(p_dec)
        w2, h2, rgb_ret = read_png(p_ret)
        if (w1, h1) != (w2, h2):
            continue
        w1, h1, rgb_dec = downsample_2x(rgb_dec, w1, h1)
        w2, h2, rgb_ret = downsample_2x(rgb_ret, w2, h2)
        tw, th = w1, h1
        tiles.append((idx, r["cuts"], rgb_dec, rgb_ret))
    if not tiles or tw is None:
        return None
    row_head = 34
    top_head = 38
    sw = tw * 2
    sh = top_head + len(tiles) * (th + row_head)
    out = bytearray(sw * sh * 3)
    fill_rect(out, sw, sh, 0, 0, sw, top_head, BG_HEADER)
    draw_text(out, sw, sh, 12, 12, "DECIMATE 10K (LEFT) VS RETOPOFORGE 10K (RIGHT) - HLH GAMECAM",
              scale=1, color=FG_TITLE)
    for k, (idx, cuts, rgb_dec, rgb_ret) in enumerate(tiles):
        y0 = top_head + k * (th + row_head)
        fill_rect(out, sw, sh, 0, y0, sw, row_head, BG_HEADER)
        fill_rect(out, sw, sh, 0, y0, sw, 1, LINE_HEADER)
        dc = cuts["decimate"]
        rt = cuts["retopo"]
        dt = dc["bend"]["tears"]
        rtt = rt["bend"]["tears"]
        lbl_l = f"CREATURE {idx} DECIMATE: TEARS {dt:.1f}% D95 {dc['dist_p95']:.2f}%"
        lbl_r = f"RETOPO: TEARS {rtt:.1f}% D95 {rt['dist_p95']:.2f}%"
        draw_text(out, sw, sh, 10, y0 + 12, lbl_l, scale=1, color=FG_TITLE)
        draw_text(out, sw, sh, tw + 10, y0 + 12, lbl_r, scale=1, color=FG_TITLE)
        rb = tw * 3
        for y in range(th):
            dst = (y0 + row_head + y) * sw * 3
            src = y * rb
            out[dst:dst + rb] = rgb_dec[src:src + rb]
            out[dst + rb:dst + 2 * rb] = rgb_ret[src:src + rb]
    write_png(dest_path, sw, sh, out)
    return dest_path


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def key_path_for(blind_dir):
    return os.path.abspath(blind_dir).rstrip(os.sep) + ".key.json"


def verdict_path_for(blind_dir):
    return os.path.abspath(blind_dir).rstrip(os.sep) + ".verdict.json"


def measure_of(cut_entry):
    tears = cut_entry["bend"]["tears"]
    d95 = cut_entry["dist_p95"]
    return {
        "tears": tears,
        "score": cut_entry["bend"].get("score"),
        "dist_p95": d95,
        "open_edges": cut_entry.get("open_edges"),
    }


def numeric_bend_winner(row):
    c = row["cuts"]
    rt = c["retopo"]["bend"]["tears"]
    dc = c["decimate"]["bend"]["tears"]
    if rt < dc:
        return "retopo"
    return "decimate" if dc < rt else "tie"


def numeric_surface_winner(row):
    c = row["cuts"]
    rt = c["retopo"]["dist_p95"]
    dc = c["decimate"]["dist_p95"]
    if rt < dc:
        return "retopo"
    return "decimate" if dc < rt else "tie"


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def _write_json(path, obj, sort_keys=False):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=sort_keys)


def build(out_dir, seed=20261115):
    res_path = os.path.join(out_dir, "results.json")
    res = _read_json(res_path)
    ok_rows = [r for r in res["rows"] if r.get("ok")]
    if not ok_rows:
        raise SystemExit("blind build: no completed rows in results.json")

    blind_dir = os.path.join(out_dir, "blind")
    if os.path.exists(blind_dir):
        shutil.rmtree(blind_dir)
    os.makedirs(blind_dir, exist_ok=True)

    specs = []
    for idx, r in enumerate(ok_rows, 1):
        subj = r["subject"]
        anon = f"creature_{idx}"
        d = os.path.join(out_dir, subj)
        specs.append({
            "kind": "claim",
            "anon_subject": anon,
            "row": r,
            "a": ("retopo", os.path.join(d, "frame_retopo.png"), measure_of(r["cuts"]["retopo"])),
            "b": ("decimate", os.path.join(d, "frame_decimate.png"), measure_of(r["cuts"]["decimate"])),
        })

    # Mandatory can't-tell control (exact same decimate render on both sides)
    # and planted bad case (decimate vs coarse swapped-limb planted cut).
    r0 = ok_rows[0]
    d0 = os.path.join(out_dir, r0["subject"])
    dec0_m = measure_of(r0["cuts"]["decimate"])
    plt0_m = measure_of(r0["cuts"]["planted"])
    specs.append({
        "kind": "cant_tell",
        "anon_subject": "creature_1",
        "row": r0,
        "a": ("decimate", os.path.join(d0, "frame_decimate.png"), dec0_m),
        "b": ("decimate", os.path.join(d0, "frame_decimate.png"), dec0_m),
    })
    # Pick the creature whose planted copy tears worst relative to decimate for the planted gate.
    best_p_idx, best_p_row = max(
        enumerate(ok_rows, 1),
        key=lambda it: it[1]["cuts"]["planted"]["bend"]["tears"] - it[1]["cuts"]["decimate"]["bend"]["tears"],
    )
    dp = os.path.join(out_dir, best_p_row["subject"])
    specs.append({
        "kind": "planted",
        "anon_subject": f"creature_{best_p_idx}",
        "row": best_p_row,
        "a": ("decimate", os.path.join(dp, "frame_decimate.png"), measure_of(best_p_row["cuts"]["decimate"])),
        "b": ("planted", os.path.join(dp, "frame_planted.png"), measure_of(best_p_row["cuts"]["planted"])),
    })

    rng = random.Random(seed)
    ordered = []
    for sp in specs:
        swap = rng.random() < 0.5
        left = sp["b"] if swap else sp["a"]
        right = sp["a"] if swap else sp["b"]
        ordered.append({**sp, "left": left, "right": right})
    rng.shuffle(ordered)

    manifest_pairs = []
    key_pairs = []
    for i, sp in enumerate(ordered, 1):
        pid = f"pair_{i:02d}"
        fname = f"{pid}.png"
        lw, lh, l_rgb = read_png(sp["left"][1])
        rw, rh, r_rgb = read_png(sp["right"][1])
        if (lw, lh) != (rw, rh):
            raise ValueError(f"{pid}: mismatched tile sizes {(lw, lh)} vs {(rw, rh)}")
        sw, sh, sheet_rgb = compose_pair_sheet(l_rgb, r_rgb, lw, lh, i)
        rmse = rmse_below_header(sheet_rgb, sw, sh, HEADER_H)
        if sp["kind"] == "cant_tell" and rmse != 0.0:
            raise AssertionError(f"can't-tell control {pid} has non-zero RMSE {rmse}")
        write_png(os.path.join(blind_dir, fname), sw, sh, sheet_rgb)
        manifest_pairs.append({"id": pid, "file": fname})

        entry = {
            "id": pid,
            "file": fname,
            "kind": sp["kind"],
            "subject": sp["anon_subject"],
            "left_tool": sp["left"][0],
            "right_tool": sp["right"][0],
            "left_measure": sp["left"][2],
            "right_measure": sp["right"][2],
            "left_sha256": sha256_bytes(l_rgb),
            "right_sha256": sha256_bytes(r_rgb),
            "rmse_below_header": round(rmse, 6),
        }
        if sp["kind"] == "claim":
            entry["bend_winner"] = numeric_bend_winner(sp["row"])
            entry["surface_winner"] = numeric_surface_winner(sp["row"])
        elif sp["kind"] == "planted":
            dc_c = sp["row"]["cuts"]["decimate"]
            pl_c = sp["row"]["cuts"]["planted"]
            entry["planted_flagged_bend"] = pl_c["bend"]["tears"] > dc_c["bend"]["tears"]
            entry["planted_flagged_surface"] = pl_c["dist_p95"] > dc_c["dist_p95"]
        key_pairs.append(entry)

    manifest = {
        "question": QUESTION,
        "answers": ["left", "right", "cant_tell"],
        "header_px": HEADER_H,
        "pairs": manifest_pairs,
    }
    _write_json(os.path.join(blind_dir, "manifest.json"), manifest)

    ext_key_path = key_path_for(blind_dir)
    key_doc = {
        "seed": seed,
        "header_px": HEADER_H,
        "pairs": key_pairs,
    }
    _write_json(ext_key_path, key_doc, sort_keys=True)
    compose_summary_sheet(ok_rows, out_dir, os.path.join(out_dir, "summary_sheet.png"))
    print(f"blind: wrote {len(manifest_pairs)} unmarked sheets to {blind_dir} "
          f"(external key: {ext_key_path})")
    return 0


def _norm_answer(val):
    if val is None:
        return None
    s = "".join(ch for ch in str(val).strip().lower() if ch not in " '-_")
    if s in ("canttell", "cant", "neither", "same", "equal", "tie"):
        return "cant_tell"
    if s in ("left", "l", "a"):
        return "left"
    if s in ("right", "r", "b"):
        return "right"
    return s


def _load_answers(path):
    raw = _read_json(path)
    if isinstance(raw, dict):
        if "answers" in raw and isinstance(raw["answers"], (list, dict)):
            raw = raw["answers"]
        else:
            return {k: _norm_answer(v) for k, v in raw.items()}
    out = {}
    for item in raw:
        pid = item.get("id") or item.get("pair")
        ans = item.get("answer") if "answer" in item else item.get("pick")
        if pid is not None:
            if isinstance(pid, int) or (isinstance(pid, str) and pid.isdigit()):
                pid = f"pair_{int(pid):02d}"
            out[str(pid)] = _norm_answer(ans)
    return out


def score(blind_dir, answers_path, key_path=None, out_path=None):
    blind_dir = os.path.abspath(blind_dir)
    key_path = key_path or key_path_for(blind_dir)
    out_path = out_path or verdict_path_for(blind_dir)
    key_doc = _read_json(key_path)
    answers = _load_answers(answers_path)

    control_ok = False
    planted_judge_ok = False
    planted_metric_ok = False
    saw_control = saw_planted = False

    pair_verdicts = []
    claims_total = claims_judged = 0
    agree_bend = agree_surface = 0
    picks = {"retopo": 0, "decimate": 0, "cant_tell": 0}

    for kp in key_doc["pairs"]:
        pid = kp["id"]
        ans = answers.get(pid)
        picked_tool = kp["left_tool"] if ans == "left" else kp["right_tool"] if ans == "right" else None
        pv = {
            "id": pid,
            "kind": kp["kind"],
            "subject": kp["subject"],
            "answer": ans,
            "picked_tool": picked_tool if ans != "cant_tell" else "cant_tell",
        }
        if kp["kind"] == "cant_tell":
            saw_control = True
            ok = (ans == "cant_tell") and (kp.get("rmse_below_header", 1.0) == 0.0)
            control_ok = ok
            pv["ok"] = ok
            pv["rmse_below_header"] = kp.get("rmse_below_header")
        elif kp["kind"] == "planted":
            saw_planted = True
            p_j = picked_tool == "decimate"
            p_m = bool(kp.get("planted_flagged_bend")) and bool(kp.get("planted_flagged_surface"))
            planted_judge_ok = p_j
            planted_metric_ok = p_m
            pv["ok"] = p_j and p_m
            pv["planted_judge_ok"] = p_j
            pv["planted_metric_ok"] = p_m
        else:
            claims_total += 1
            bw = kp["bend_winner"]
            sw = kp["surface_winner"]
            pv["bend_winner"] = bw
            pv["surface_winner"] = sw
            if ans == "cant_tell":
                picks["cant_tell"] += 1
                pv["agrees_with_bend"] = None
                pv["agrees_with_surface"] = None
            elif picked_tool in ("retopo", "decimate"):
                claims_judged += 1
                picks[picked_tool] += 1
                ab = picked_tool == bw
                asurf = picked_tool == sw
                agree_bend += int(ab)
                agree_surface += int(asurf)
                pv["agrees_with_bend"] = ab
                pv["agrees_with_surface"] = asurf
        pair_verdicts.append(pv)

    valid = saw_control and saw_planted and control_ok and planted_judge_ok and planted_metric_ok
    verdict = {
        "pass": bool(valid),
        "discarded": not control_ok,
        "gates": {
            "control_cant_tell": bool(control_ok),
            "planted_judge_ok": bool(planted_judge_ok),
            "planted_metric_ok": bool(planted_metric_ok),
        },
        "claims": {
            "total": claims_total,
            "judged": claims_judged,
            "judge_picks": picks,
            "agree_with_bend": agree_bend,
            "agree_with_surface": agree_surface,
        },
        "pairs": pair_verdicts,
    }
    _write_json(out_path, verdict, sort_keys=True)
    print(json.dumps(verdict, indent=1, sort_keys=True))
    return 0 if valid else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("build", "score"))
    ap.add_argument("target", help="OUT dir for build; BLIND_DIR for score")
    ap.add_argument("answers", nargs="?", help="ANSWERS.json for score")
    ap.add_argument("--seed", type=int, default=20261115)
    ap.add_argument("--key", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.cmd == "build":
        return build(os.path.abspath(a.target), a.seed)
    if not a.answers:
        print("blind score requires ANSWERS.json", file=sys.stderr)
        return 2
    return score(a.target, a.answers, key_path=a.key, out_path=a.out)


if __name__ == "__main__":
    sys.exit(main())
