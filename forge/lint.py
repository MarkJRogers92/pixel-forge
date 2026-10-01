"""Pixel-art lint: concrete, located problems a reviewer would point at.

Ported from the `validate` tool of Aseprite AI Artist (MIT,
github.com/with-pebbly/aseprite-ai-artist, extension/ai-artist.lua), so a
sprite gets the same verdict here and in the Aseprite window:

- strays        an opaque pixel with no opaque 4-neighbour (noise at 1x);
                more than three is an error
- outline       one colour owns 60%+ of the silhouette edge but not all of
                it: the stragglers are holes in the outline
- banding       a long straight boundary between two colours reads as a
                contour line, not a surface (a straight edge against the
                outline colour is the outline doing its job, so it is skipped)
- antialiasing  semi-transparent pixels (PNG input only): a soft brush or a
                resize, reads as blur
- palette       near-duplicate colours (a palette that has sprawled)

Score: 100 minus 15 per error and 5 per warning; notes are free.
"""
from __future__ import annotations

from PIL import Image

from .sprite import TRANSPARENT, Sprite, parse_hex

ERROR_COST, WARNING_COST = 15, 5
NEAR_DUPLICATE = 6.0          # CIE76 delta-E below which two colours look the same


def _finding(check: str, severity: str, message: str, at=None, count: int | None = None) -> dict:
    f = {"check": check, "severity": severity, "message": message}
    if at is not None:
        f["at"] = [int(at[0]), int(at[1])]
    if count is not None:
        f["count"] = int(count)
    return f


def _lab(hex_color: str) -> tuple[float, float, float]:
    r, g, b, _ = parse_hex(hex_color)

    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = lin(r), lin(g), lin(b)
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def lint(s: Sprite) -> dict:
    g, w, h = s.grid, s.width, s.height
    opaque = lambda x, y: 0 <= x < w and 0 <= y < h and g[y][x] != TRANSPARENT
    findings: list[dict] = []
    n4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

    # strays
    strays = [(x, y) for y in range(h) for x in range(w)
              if opaque(x, y) and not any(opaque(x + dx, y + dy) for dx, dy in n4)]
    if strays:
        findings.append(_finding("strays", "error" if len(strays) > 3 else "warning",
                                 f"{len(strays)} isolated pixel(s) with no neighbour; these read as noise at 1x.",
                                 strays[0], len(strays)))

    # outline
    lone = set(strays)   # strays are their own finding, not gaps in the outline
    edge = [(x, y) for y in range(h) for x in range(w)
            if opaque(x, y) and (x, y) not in lone and any(not opaque(x + dx, y + dy) for dx, dy in n4)]
    outline_key = None
    if edge:
        counts: dict[str, int] = {}
        for x, y in edge:
            counts[g[y][x]] = counts.get(g[y][x], 0) + 1
        best, best_n = max(counts.items(), key=lambda kv: kv[1])
        share = best_n / len(edge)
        if share >= 0.6:
            outline_key = best
        if 0.6 <= share < 1.0:
            first = next(p for p in edge if g[p[1]][p[0]] != best)
            findings.append(_finding(
                "outline", "warning",
                f"Outline is {round(share * 100)}% {s.palette.get(best, best)} but {len(edge) - best_n} edge "
                f"pixel(s) use another colour; a broken outline reads as a hole in the silhouette.",
                first, len(edge) - best_n))
        elif share < 0.6:
            findings.append(_finding("outline", "note",
                                     "No single colour owns the silhouette edge, so the sprite is not outlined "
                                     "(a valid style; keep it consistent)."))

    # banding: the longest run of one colour pair across a horizontal or vertical seam
    threshold = max(8, w // 3)
    worst, worst_at, worst_pair = 0, None, None

    def scan(cells):
        nonlocal worst, worst_at, worst_pair
        run, pair, start = 0, None, None
        for (ax, ay), (bx, by) in cells:
            a = g[ay][ax] if opaque(ax, ay) else None
            b = g[by][bx] if opaque(bx, by) else None
            here = (a, b) if a and b and a != b and outline_key not in (a, b) else None
            if here and here == pair:
                run += 1
            else:
                pair, run, start = here, (1 if here else 0), (ax, ay)
            if run > worst:
                worst, worst_at, worst_pair = run, start, pair

    for y in range(h - 1):
        scan([((x, y), (x, y + 1)) for x in range(w)])
    for x in range(w - 1):
        scan([((x, y), (x + 1, y)) for y in range(h)])
    if worst >= threshold and worst_pair:
        findings.append(_finding(
            "banding", "note",
            f"A {worst}-pixel straight boundary between {s.palette.get(worst_pair[0])} and "
            f"{s.palette.get(worst_pair[1])}; let the edge wander or dither part of it.",
            worst_at, worst))

    # palette: near-duplicate colours in use
    used = sorted(k for k in s.used_keys() if k in s.palette)
    labs = {k: _lab(s.palette[k]) for k in used}
    dupes = [(a, b) for i, a in enumerate(used) for b in used[i + 1:]
             if sum((p - q) ** 2 for p, q in zip(labs[a], labs[b])) ** 0.5 < NEAR_DUPLICATE]
    if dupes:
        findings.append(_finding("palette", "note",
                                 f"{len(dupes)} pair(s) of near-identical colours, e.g. "
                                 f"{s.palette[dupes[0][0]]} and {s.palette[dupes[0][1]]}; merge them.",
                                 count=len(dupes)))

    return _report(findings)


def lint_image(img: Image.Image) -> dict:
    """Lint a PNG as it is on disk: soft alpha first, then the sprite it becomes."""
    img = img.convert("RGBA")
    alphas = img.getchannel("A").getdata()
    semi = sum(1 for a in alphas if 0 < a < 255)
    rep = lint(Sprite.from_image(img))
    if semi:
        rep["findings"].insert(0, _finding("antialiasing", "warning",
                                           f"{semi} semi-transparent pixel(s); partial alpha usually comes "
                                           f"from a soft brush or a resize and reads as blur.", count=semi))
        rep.update(_report(rep["findings"]))
    return rep


def _report(findings: list[dict]) -> dict:
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"findings": findings, "passed": errors == 0,
            "score": max(0, 100 - ERROR_COST * errors - WARNING_COST * warnings)}
