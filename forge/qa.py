"""Deterministic checks on a sprite. Measurements, not taste."""
from __future__ import annotations

from .lint import lint
from .sprite import TRANSPARENT, Sprite, parse_hex


def _lum(hex_color: str) -> float:
    r, g, b, _ = parse_hex(hex_color)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def check(s: Sprite, max_colors: int | None = None) -> dict:
    g = s.grid
    opaque = [(x, y) for y in range(s.height) for x in range(s.width) if g[y][x] != TRANSPARENT]
    rep: dict = {"size": [s.width, s.height], "opaque_pixels": len(opaque), "warnings": []}
    warn = rep["warnings"]
    if not opaque:
        warn.append("sprite is empty")
        return rep

    xs, ys = [p[0] for p in opaque], [p[1] for p in opaque]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    bw, bh = bbox[2] - bbox[0] + 1, bbox[3] - bbox[1] + 1
    rep["bbox"] = bbox
    rep["fill_of_canvas"] = round(bw * bh / (s.width * s.height), 2)
    used = s.used_keys()
    rep["colors"] = len(used)
    unknown = [k for k in used if k not in s.palette]
    if unknown:
        warn.append(f"keys without palette entry: {unknown}")

    edges = [n for n, hit in (("left", bbox[0] == 0), ("top", bbox[1] == 0),
                              ("right", bbox[2] == s.width - 1)) if hit]
    if edges:  # touching the bottom is normal for things standing on the ground
        warn.append(f"touches the {'/'.join(edges)} edge (possible cropping)")
    if rep["fill_of_canvas"] < 0.3:
        warn.append("subject uses less than 30% of the canvas; consider drawing larger")
    if max_colors and len(used) > max_colors:
        warn.append(f"{len(used)} colours exceeds limit {max_colors}")

    # orphans: opaque pixel matching none of its 8 neighbours, inside the shape
    orphans = 0
    for x, y in opaque:
        nb = [s.get(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
        if TRANSPARENT not in nb and g[y][x] not in nb:
            orphans += 1
    rep["orphan_pixels"] = orphans
    if orphans > max(3, len(opaque) // 60):
        warn.append(f"{orphans} isolated single pixels (noisy texture)")

    # outline: are silhouette-edge pixels darker than the interior?
    edge = [(x, y) for x, y in opaque
            if any(s.get(x + dx, y + dy) == TRANSPARENT for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
    lums = {k: _lum(v) for k, v in s.palette.items() if k in used}
    if edge and lums:
        dark_cut = sorted(lums.values())[max(0, len(lums) // 4 - 1)] + 0.02
        dark_edge = sum(1 for x, y in edge if lums.get(g[y][x], 1) <= dark_cut)
        rep["outline_coverage"] = round(dark_edge / len(edge), 2)

    # stray fragments: opaque components disconnected from the main body
    seen, comps = set(), []
    for p in opaque:
        if p in seen:
            continue
        stack, n = [p], 0
        while stack:
            q = stack.pop()
            if q in seen or not s.inside(*q) or g[q[1]][q[0]] == TRANSPARENT:
                continue
            seen.add(q)
            n += 1
            stack.extend((q[0] + dx, q[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)
        comps.append(n)
    rep["components"] = len(comps)
    small = [c for c in comps if c <= 2]
    if small:
        warn.append(f"{len(small)} tiny detached fragment(s)")

    # horizontal symmetry (informational)
    same = sum(1 for x, y in opaque if g[y][s.width - 1 - x] == g[y][x])
    rep["mirror_symmetry"] = round(same / len(opaque), 2)
    # located problems, the same verdict the Aseprite window's validate gives (lint.py)
    rep["lint"] = lint(s)
    return rep
