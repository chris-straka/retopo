"""Pure-Python silhouette and shading comparison for 8-frame posed game-camera renders
(`retopo-3`, stdlib only)."""
import math
import struct
import zlib

DEFAULT_FRAME_COUNT = 8


def sample_frame_indices(start, end, count=DEFAULT_FRAME_COUNT):
    """Return `count` evenly-spaced integer frame indices across `[start, end]`."""
    count = max(1, int(count))
    start = int(round(start))
    end = int(round(end))
    if count == 1 or end <= start:
        return [start] * count
    span = end - start
    return [start + int(round(i * span / float(count - 1))) for i in range(count)]


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png_rgba(path):
    """Read an 8-bit non-interlaced RGB or RGBA PNG into `(w, h, bytearray_rgba)`."""
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
        raise ValueError(
            f"{path}: unsupported PNG format depth={bit_depth} color={color_type} interlace={interlace}"
        )
    bpp = 3 if color_type == 2 else 4
    stride = w * bpp
    raw = zlib.decompress(bytes(idat))
    prev = bytearray(stride)
    cur = bytearray(stride)
    out = bytearray(w * h * 4)
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
        if bpp == 4:
            out[dst_i:dst_i + stride] = cur
            dst_i += stride
        else:
            for x in range(0, stride, 3):
                out[dst_i] = cur[x]
                out[dst_i + 1] = cur[x + 1]
                out[dst_i + 2] = cur[x + 2]
                out[dst_i + 3] = 255
                dst_i += 4
        prev[:] = cur
    return w, h, out


def compare_frame_pair(ref_png, cand_png):
    """Compare one posed reference RGBA PNG against a candidate RGBA PNG.

    Silhouette mask uses `alpha >= 128`. Shading error is computed over the
    foreground union (`mask_ref | mask_cand`) using premultiplied RGB (transparent
    pixels contribute 0), so both silhouette misses and interior normal/shading
    shifts register cleanly while identical renders return exact 0.0.
    """
    rw, rh, ref = read_png_rgba(ref_png)
    cw, ch, cand = read_png_rgba(cand_png)
    if (rw, rh) != (cw, ch):
        raise ValueError(f"mismatched frame sizes {(rw, rh)} vs {(cw, ch)}")
    if ref == cand:
        fg = sum(1 for i in range(3, len(ref), 4) if ref[i] >= 128)
        return {
            "sil_diff_pct": 0.0,
            "shade_rmse": 0.0,
            "shade_l1_pct": 0.0,
            "ref_pixels": fg,
            "cand_pixels": fg,
        }

    ref_fg = 0
    cand_fg = 0
    xor_fg = 0
    union_fg = 0
    sq_err = 0.0
    l1_err = 0.0
    n = len(ref)
    for i in range(0, n, 4):
        ra = ref[i + 3]
        ca = cand[i + 3]
        rm = ra >= 128
        cm = ca >= 128
        if rm:
            ref_fg += 1
        if cm:
            cand_fg += 1
        if rm != cm:
            xor_fg += 1
        if rm or cm:
            union_fg += 1
            r0 = ref[i] if rm else 0
            g0 = ref[i + 1] if rm else 0
            b0 = ref[i + 2] if rm else 0
            r1 = cand[i] if cm else 0
            g1 = cand[i + 1] if cm else 0
            b1 = cand[i + 2] if cm else 0
            dr = r0 - r1
            dg = g0 - g1
            db = b0 - b1
            sq_err += dr * dr + dg * dg + db * db
            l1_err += abs(dr) + abs(dg) + abs(db)

    sil_pct = 100.0 * xor_fg / max(1, ref_fg)
    denom = max(1, union_fg * 3)
    shade_rmse = math.sqrt(sq_err / denom)
    shade_l1_pct = 100.0 * (l1_err / denom) / 255.0
    return {
        "sil_diff_pct": round(sil_pct, 4),
        "shade_rmse": round(shade_rmse, 4),
        "shade_l1_pct": round(shade_l1_pct, 4),
        "ref_pixels": ref_fg,
        "cand_pixels": cand_fg,
    }


def aggregate_frames(per_frame):
    """Aggregate per-frame silhouette and shading readings across a clip."""
    if not per_frame:
        raise ValueError("aggregate_frames requires at least one frame")
    n = len(per_frame)
    sil_vals = [float(f["sil_diff_pct"]) for f in per_frame]
    rmse_vals = [float(f["shade_rmse"]) for f in per_frame]
    l1_vals = [float(f["shade_l1_pct"]) for f in per_frame]
    return {
        "frames": n,
        "sil_diff_mean_pct": round(sum(sil_vals) / n, 4),
        "sil_diff_max_pct": round(max(sil_vals), 4),
        "shade_rmse_mean": round(sum(rmse_vals) / n, 4),
        "shade_rmse_max": round(max(rmse_vals), 4),
        "shade_l1_mean_pct": round(sum(l1_vals) / n, 4),
        "per_frame": per_frame,
    }
