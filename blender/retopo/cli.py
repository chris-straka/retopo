# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI discovery and stdout parsing helpers for RetopoForge."""

from __future__ import annotations

import os
import re
import shutil

_SUMMARY_RE = re.compile(
    r"Quads:\s*(\d+).*?Non-quads:\s*(\d+).*?Vertices:\s*(\d+).*?Time:\s*([\d.]+)",
    re.DOTALL,
)

# One line per rung in the CLI's multimode stdout, e.g.
# "LOD 0: target-quads=10000 output=/tmp/x_lod0.obj quads=9051 non-quads=3 ...".
_LOD_RUNG_RE = re.compile(
    # The space before quads= skips the "target-quads=" field earlier on
    # the same line (its prefix is '-', not whitespace).
    r"LOD\s+(\d+):.*?\squads=(\d+).*?non-quads=(\d+)",
)


def _is_executable_file(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _expand_blender_path(path: str) -> str:
    try:
        import bpy
    except ImportError:
        bpy = None
    if bpy is not None:
        return bpy.path.abspath(path)
    return os.path.abspath(os.path.expanduser(path))


def find_retopo_binary(explicit_path: str = "") -> str:
    """Locate the `retopo` CLI binary.

    When `explicit_path` is non-empty, only that path is checked (returning
    `""` if it is missing or not executable, rather than silently falling
    back). Otherwise checks `$RETOPO_BIN`, `PATH`, and build-tree locations
    relative to a checkout.
    """
    explicit = (explicit_path or "").strip()
    if explicit:
        expanded = _expand_blender_path(explicit)
        return expanded if _is_executable_file(expanded) else ""

    env_bin = os.environ.get("RETOPO_BIN", "").strip()
    if _is_executable_file(env_bin):
        return env_bin

    on_path = shutil.which("retopo")
    if on_path:
        return on_path

    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        os.path.join(here, "..", "..", "rust", "target", "release", "retopo"),
        os.path.join(here, "..", "..", "rust", "target", "debug", "retopo"),
    ):
        normalized = os.path.normpath(candidate)
        if _is_executable_file(normalized):
            return normalized
    return ""


def parse_summary(stdout_text: str | None) -> tuple[int, int, int, float] | None:
    """Parse the CLI's `=== retopo Report ===` block.

    Returns `(quads, non_quads, verts, seconds)` or `None`.
    """
    match = _SUMMARY_RE.search(stdout_text or "")
    if not match:
        return None
    quads, non_quads, verts, seconds = match.groups()
    return int(quads), int(non_quads), int(verts), float(seconds)


def parse_lod_targets(text: str | None) -> list[int]:
    """Parse a comma-separated LOD target string into positive ints.

    Raises `ValueError` with a user-facing message on bad input.
    """
    raw = text or ""
    parts = [p.strip() for p in raw.split(",")]
    targets: list[int] = []
    for part in parts:
        if not part.isdigit() or int(part) <= 0:
            raise ValueError(
                f"LOD targets must be positive integers, got '{part}' "
                f"in '{raw}'"
            )
        targets.append(int(part))
    if not targets:
        raise ValueError("LOD targets must list at least one quad count")
    return targets


def parse_lod_rung_counts(stdout_text: str | None) -> dict[int, tuple[int, int]]:
    """Parse per-rung `(quads, non_quads)` counts from `--lods` stdout."""
    return {
        int(rung): (int(quads), int(non_quads))
        for rung, quads, non_quads in _LOD_RUNG_RE.findall(stdout_text or "")
    }
