"""Sprite programs: the drawing language Claude writes.

A program is JSON:

    {"width": 32, "height": 32,
     "palette": {"k": "#1b1622", "a": "#6b4a3a", "b": "#a8745a", "c": "#e0b48a"},
     "ramps": {"skin": ["a", "b", "c"]},          # dark -> light, optional
     "light": "top-left",                          # default light direction
     "base": "blank" | "current",                  # "current" = edit the given sprite
     "ops": [ {"op": "rect", ...}, ... ]}

Each op is rendered deterministically onto an indexed grid. Errors are collected
per op (not raised) so they can be fed back to the artist.
"""
from __future__ import annotations

import math
import random

import numpy as np

from dataclasses import dataclass, field

from .sprite import TRANSPARENT, Sprite, parse_hex

ERASE = "~"
SKIP = {".", " "}

DIRS = {
    "top-left": (-1, -1), "top": (0, -1), "top-right": (1, -1),
    "left": (-1, 0), "right": (1, 0),
    "bottom-left": (-1, 1), "bottom": (0, 1), "bottom-right": (1, 1),
}

N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
N8 = N4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))


@dataclass
class RenderResult:
    sprite: Sprite
    errors: list[str] = field(default_factory=list)


class Canvas:
    def __init__(self, sprite: Sprite, program: dict):
        self.s = sprite
        self.ramps: dict[str, list[str]] = program.get("ramps") or {}
        self.light = program.get("light", "top-left")
        self.clip: tuple[int, int, int, int] | None = None

    def color(self, key) -> str:
        if key in (None, "", ERASE, "transparent", "none"):
            return TRANSPARENT
        if not isinstance(key, str) or key not in self.s.palette:
            raise ValueError(f"unknown color key {key!r}")
        return key

    def put(self, x, y, key):
        x, y = int(x), int(y)
        if self.clip:
            cx, cy, cw, ch = self.clip
            if not (cx <= x < cx + cw and cy <= y < cy + ch):
                return
        self.s.set(x, y, key)

    def ramp_for(self, key: str) -> list[str] | None:
        for ramp in self.ramps.values():
            if key in ramp:
                return ramp
        return None


# ---------------------------------------------------------------- primitives
def _line_points(x0, y0, x1, y1):
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _ellipse_mask(cx, cy, rx, ry):
    """Pixels whose centre lies inside the ellipse (cx, cy may be .5)."""
    rx, ry = max(float(rx), 0.5), max(float(ry), 0.5)
    pts = set()
    for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
        for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
            if ((x - cx) / (rx + 0.35)) ** 2 + ((y - cy) / (ry + 0.35)) ** 2 <= 1.0 + 1e-9:
                pts.add((x, y))
    return pts


def _poly_mask(points):
    pts = set()
    ys = [p[1] for p in points]
    n = len(points)
    for y in range(int(min(ys)), int(max(ys)) + 1):
        yc = y + 0.5
        xs = []
        for i in range(n):
            (x0, y0), (x1, y1) = points[i], points[(i + 1) % n]
            x0, y0, x1, y1 = x0 + 0.5, y0 + 0.5, x1 + 0.5, y1 + 0.5
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
        xs.sort()
        for a, b in zip(xs[0::2], xs[1::2]):
            for x in range(int(round(a)), int(round(b))):
                pts.add((x, y))
    for i in range(n):  # make edges inclusive
        pts.update(_line_points(*points[i], *points[(i + 1) % n]))
    return pts


def _border(mask):
    return {p for p in mask if any((p[0] + dx, p[1] + dy) not in mask for dx, dy in N4)}


def _fill_or_stroke(cv: Canvas, mask, op):
    key = cv.color(op.get("c"))
    fill = op.get("fill", True)
    stroke = op.get("stroke")
    target = mask if fill else _border(mask)
    for x, y in target:
        cv.put(x, y, key)
    if stroke is not None:
        sk = cv.color(stroke)
        for x, y in _border(mask):
            cv.put(x, y, sk)


# ----------------------------------------------------------------------- ops
def op_rect(cv, op):
    x, y, w, h = int(op["x"]), int(op["y"]), int(op["w"]), int(op["h"])
    _fill_or_stroke(cv, {(i, j) for i in range(x, x + w) for j in range(y, y + h)}, op)


def op_ellipse(cv, op):
    if "r" in op:
        op = {**op, "rx": op["r"], "ry": op["r"]}
    _fill_or_stroke(cv, _ellipse_mask(float(op["cx"]), float(op["cy"]), op["rx"], op["ry"]), op)


def op_poly(cv, op):
    pts = [(int(p[0]), int(p[1])) for p in op["points"]]
    if len(pts) < 3:
        raise ValueError("poly needs >= 3 points")
    _fill_or_stroke(cv, _poly_mask(pts), op)


def op_line(cv, op):
    key = cv.color(op.get("c"))
    pts = op.get("points") or [[op["x1"], op["y1"]], [op["x2"], op["y2"]]]
    for a, b in zip(pts, pts[1:]):
        for x, y in _line_points(a[0], a[1], b[0], b[1]):
            cv.put(x, y, key)


def op_pixels(cv, op):
    """Paste literal rows. '.' / ' ' = leave as is, '~' = erase."""
    ox, oy = int(op.get("x", 0)), int(op.get("y", 0))
    for j, row in enumerate(op["rows"]):
        for i, ch in enumerate(row):
            if ch in SKIP:
                continue
            cv.put(ox + i, oy + j, TRANSPARENT if ch == ERASE else cv.color(ch))


def op_points(cv, op):
    key = cv.color(op.get("c"))
    for p in op["points"]:
        cv.put(p[0], p[1], key)


def op_fill(cv, op):
    x, y = int(op["x"]), int(op["y"])
    key = cv.color(op.get("c"))
    s = cv.s
    if not s.inside(x, y):
        return
    src = s.get(x, y)
    if src == key:
        return
    stack, seen = [(x, y)], set()
    while stack:
        px, py = stack.pop()
        if (px, py) in seen or not s.inside(px, py) or s.get(px, py) != src:
            continue
        seen.add((px, py))
        cv.put(px, py, key)
        stack.extend((px + dx, py + dy) for dx, dy in N4)


def _region(cv, op):
    s = cv.s
    x, y = int(op.get("x", 0)), int(op.get("y", 0))
    w, h = int(op.get("w", s.width)), int(op.get("h", s.height))
    return [(i, j) for j in range(max(y, 0), min(y + h, s.height)) for i in range(max(x, 0), min(x + w, s.width))]


def op_replace(cv, op):
    src, dst = cv.color(op["from"]), cv.color(op["to"])
    for x, y in _region(cv, op):
        if cv.s.get(x, y) == src:
            cv.put(x, y, dst)


def op_erase(cv, op):
    for x, y in _region(cv, op):
        cv.put(x, y, TRANSPARENT)


def op_mirror(cv, op):
    """Copy one half onto the other. axis x: from=left|right; axis y: from=top|bottom."""
    s = cv.s
    axis, frm = op.get("axis", "x"), op.get("from")
    if axis == "x":
        frm = frm or "left"
        for y in range(s.height):
            for x in range(s.width // 2):
                a, b = x, s.width - 1 - x
                if frm == "left":
                    s.grid[y][b] = s.grid[y][a]
                else:
                    s.grid[y][a] = s.grid[y][b]
    else:
        frm = frm or "top"
        for y in range(s.height // 2):
            a, b = y, s.height - 1 - y
            if frm == "top":
                s.grid[b] = s.grid[a][:]
            else:
                s.grid[a] = s.grid[b][:]


def op_copy(cv, op):
    x, y, w, h = int(op["x"]), int(op["y"]), int(op["w"]), int(op["h"])
    dx, dy = int(op["dx"]), int(op["dy"])
    flip = op.get("flip")
    block = [[cv.s.get(x + i, y + j) for i in range(w)] for j in range(h)]
    if flip in ("x", "xy"):
        block = [row[::-1] for row in block]
    if flip in ("y", "xy"):
        block = block[::-1]
    for j in range(h):
        for i in range(w):
            if block[j][i] != TRANSPARENT or op.get("include_transparent"):
                cv.put(dx + i, dy + j, block[j][i])


def op_outline(cv, op):
    """Draw an outline on transparent pixels around the shape.

    selective=true colours each outline pixel with the darkest ramp colour of the
    neighbouring fill (sel-out) instead of a single colour.
    corners=false (default) gives the rounder 4-neighbour outline.
    """
    s = cv.s
    key = cv.color(op.get("c")) if op.get("c") else None
    nbrs = N8 if op.get("corners") else N4
    selective = op.get("selective", False)
    adds = []
    for y in range(s.height):
        for x in range(s.width):
            if s.grid[y][x] != TRANSPARENT:
                continue
            touching = [s.get(x + dx, y + dy) for dx, dy in nbrs]
            touching = [t for t in touching if t != TRANSPARENT]
            if not touching:
                continue
            col = key
            if selective:
                ramp = cv.ramp_for(max(set(touching), key=touching.count))
                col = ramp[0] if ramp else key
            if col:
                adds.append((x, y, col))
    for x, y, col in adds:
        cv.put(x, y, col)


def op_shade(cv, op):
    """Auto-shade every pixel of colour `c` along its ramp.

    Pixels on the edge facing the light step one ramp colour lighter, pixels on
    the far edge step darker. width = how many pixels deep the band goes.
    """
    s = cv.s
    key = cv.color(op["c"])
    ramp = op.get("ramp") or cv.ramp_for(key)
    if not ramp or key not in ramp:
        raise ValueError(f"shade needs a ramp containing {key!r}")
    idx = ramp.index(key)
    lx, ly = DIRS[op.get("light", cv.light)]
    width = int(op.get("width", 1))
    hi = ramp[min(idx + 1, len(ramp) - 1)] if op.get("highlight", True) else key
    lo = ramp[max(idx - 1, 0)] if op.get("shadow", True) else key
    changes = []
    for y in range(s.height):
        for x in range(s.width):
            if s.grid[y][x] != key:
                continue
            toward = any(s.get(x + lx * d, y + ly * d) != key for d in range(1, width + 1))
            away = any(s.get(x - lx * d, y - ly * d) != key for d in range(1, width + 1))
            if toward and not away:
                changes.append((x, y, hi))
            elif away and not toward:
                changes.append((x, y, lo))
    for x, y, col in changes:
        cv.put(x, y, col)


def op_dither(cv, op):
    """Replace some pixels of colour `on` with `c` in a pattern (checker|25|75|lines)."""
    on = cv.color(op["on"]) if op.get("on") else None
    key = cv.color(op.get("c"))
    pat = str(op.get("pattern", "checker"))
    for x, y in _region(cv, op):
        cur = cv.s.get(x, y)
        if on is not None and cur != on:
            continue
        if on is None and cur == TRANSPARENT:
            continue
        hit = {
            "checker": (x + y) % 2 == 0,
            "25": x % 2 == 0 and y % 2 == 0,
            "75": not (x % 2 == 1 and y % 2 == 1),
            "lines": y % 2 == 0,
            "vlines": x % 2 == 0,
        }.get(pat, (x + y) % 2 == 0)
        if hit:
            cv.put(x, y, key)


def op_noise(cv, op):
    """Deterministic speckle (grime, sparkle): density 0..1, seed int."""
    rng = random.Random(int(op.get("seed", 1)))
    on = cv.color(op["on"]) if op.get("on") else None
    key = cv.color(op.get("c"))
    density = float(op.get("density", 0.1))
    for x, y in _region(cv, op):
        cur = cv.s.get(x, y)
        if cur == TRANSPARENT or (on is not None and cur != on):
            continue
        if rng.random() < density:
            cv.put(x, y, key)


def op_clip(cv, op):
    """Restrict following ops to a rectangle; {"op":"clip"} with no rect clears it."""
    cv.clip = (int(op["x"]), int(op["y"]), int(op["w"]), int(op["h"])) if "x" in op else None


def op_shift(cv, op):
    """Move the whole drawing by dx, dy (to recentre)."""
    s = cv.s
    dx, dy = int(op.get("dx", 0)), int(op.get("dy", 0))
    old = [row[:] for row in s.grid]
    for y in range(s.height):
        for x in range(s.width):
            s.grid[y][x] = old[y - dy][x - dx] if 0 <= y - dy < s.height and 0 <= x - dx < s.width else TRANSPARENT


# ------------------------------------------------------------ painting passes
# These do the tedious "painterly" work so the artist only picks form + material.

BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def _mask_of(cv, op, key="on"):
    """Pixels selected by `on` (one key or a list) inside an optional rect."""
    on = op.get(key)
    keys = set(on) if isinstance(on, list) else ({on} if on else None)
    if keys is not None:
        for k in keys:
            cv.color(k)
    pts = set()
    for x, y in _region(cv, op):
        cur = cv.s.get(x, y)
        if cur != TRANSPARENT and (keys is None or cur in keys):
            pts.add((x, y))
    return pts


def _distance_inside(mask):
    """4-connected distance from each mask pixel to the nearest non-mask pixel."""
    dist = {}
    frontier = [p for p in mask if any((p[0] + dx, p[1] + dy) not in mask for dx, dy in N4)]
    for p in frontier:
        dist[p] = 1
    while frontier:
        nxt = []
        for x, y in frontier:
            for dx, dy in N4:
                q = (x + dx, y + dy)
                if q in mask and q not in dist:
                    dist[q] = dist[(x, y)] + 1
                    nxt.append(q)
        frontier = nxt
    return dist


def _height_field(mask, depth):
    """Blur the form's mask into a smooth dome; returns {pixel: (height, gx, gy)}."""
    xs = [p[0] for p in mask]
    ys = [p[1] for p in mask]
    x0, y0 = min(xs) - depth - 1, min(ys) - depth - 1
    w, h = max(xs) - x0 + depth + 2, max(ys) - y0 + depth + 2
    m = np.zeros((h, w))
    for x, y in mask:
        m[y - y0, x - x0] = 1.0
    sigma = max(0.8, depth / 2.0)
    r = int(math.ceil(sigma * 2.5))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    b = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 0, m)
    b = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 1, b)
    gy, gx = np.gradient(b)
    return {(x, y): (b[y - y0, x - x0], gx[y - y0, x - x0], gy[y - y0, x - x0]) for x, y in mask}


def _ramp_arg(cv, op, anchor):
    ramp = op.get("ramp") or cv.ramp_for(anchor)
    if not ramp:
        raise ValueError(f"needs a ramp (dark -> light) for {anchor!r}")
    for k in ramp:
        cv.color(k)
    return ramp


def _dithered_step(value: float, x: int, y: int, dither: bool) -> int:
    """Round a fractional ramp offset, using a Bayer pattern between steps."""
    base = math.floor(value)
    frac = value - base
    if not dither or frac < 0.3 or frac > 0.7:
        # clean bands; only the seam between two tones gets dithered
        return base + (1 if frac >= 0.5 else 0)
    t = (frac - 0.3) / 0.4
    return base + (1 if t * 16 > BAYER4[y % 4][x % 4] + 0.5 else 0)


def op_light(cv, op):
    """Volume lighting: shade a form along its ramp with smooth falloff.

    on      colour key(s) of the form (all get re-shaded along one ramp)
    ramp    dark -> light keys; the form's base tone is `base` (default: middle)
    form    "round" (falloff from every edge) | "flat" (gradient only)
    depth   how far edge lighting reaches inward (px)
    strength how many ramp steps the lit/shadow edges move (default 1.5)
    tilt    extra gradient across the whole form toward the light (steps, default 1)
    invert  true for recessed shapes (screens, slots): lit edge becomes the shadow
    dither  Bayer-dither between ramp steps for smooth falloff (default true)
    """
    mask = _mask_of(cv, op)
    if not mask:
        return
    first = next(iter(mask))
    ramp = _ramp_arg(cv, op, cv.s.get(*first))
    base = op.get("base")
    bi = ramp.index(base) if base in ramp else (len(ramp) - 1) // 2
    lx, ly = DIRS[op.get("light", cv.light)]
    ln = math.hypot(lx, ly) or 1
    lx, ly = lx / ln, ly / ln
    depth = max(1, int(op.get("depth", 3)))
    strength = float(op.get("strength", 1.5))
    tilt = float(op.get("tilt", 1.0))
    sign = -1 if op.get("invert") else 1
    dither = op.get("dither", True)
    form = op.get("form", "round")
    hfield = _height_field(mask, depth)
    xs = [p[0] for p in mask]
    ys = [p[1] for p in mask]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1) / 2
    out = {}
    for (x, y) in mask:
        v = 0.0
        if form == "round":
            h, gx, gy = hfield[(x, y)]
            gl = math.hypot(gx, gy)
            if gl > 1e-3:
                # outward normal = -gradient of the height field; lit when facing the light
                facing = (-gx * lx - gy * ly) / gl
                edge = min(1.0, max(0.0, (1.0 - h) * 2.0))  # 1 at the rim, 0 in the flat middle
                v += sign * strength * facing * edge
        # whole-form gradient: pixels nearer the light are brighter
        v += sign * tilt * ((x - cx) * lx + (y - cy) * ly) / span * 0.5
        idx = _dithered_step(bi + v, x, y, dither)
        out[(x, y)] = ramp[max(0, min(len(ramp) - 1, idx))]
    for (x, y), k in out.items():
        cv.put(x, y, k)


def op_gradient(cv, op):
    """Linear ramp across a region along `dir` (dark end -> light end), dithered."""
    mask = _mask_of(cv, op)
    if not mask:
        return
    ramp = _ramp_arg(cv, op, cv.s.get(*next(iter(mask))))
    dx, dy = DIRS[op.get("dir", "top")]
    proj = {p: p[0] * dx + p[1] * dy for p in mask}  # larger = closer to `dir` (the light end)
    lo, hi = min(proj.values()), max(proj.values())
    rng = (hi - lo) or 1
    for (x, y), t in proj.items():
        v = (t - lo) / rng * (len(ramp) - 1)
        idx = _dithered_step(v, x, y, op.get("dither", True))
        cv.put(x, y, ramp[max(0, min(len(ramp) - 1, idx))])


def op_cast_shadow(cv, op):
    """Darken `on` pixels that something from `from` blocks from the light.

    Gives contact shadows under ledges, inside recesses and below attached parts.
    """
    targets = _mask_of(cv, op)
    blockers = set(op["from"]) if isinstance(op["from"], list) else {op["from"]}
    lx, ly = DIRS[op.get("light", cv.light)]
    length = int(op.get("length", 2))
    dark = cv.color(op["c"]) if op.get("c") else None
    changes = []
    for x, y in targets:
        for k in range(1, length + 1):
            if cv.s.get(x + lx * k, y + ly * k) in blockers:
                cur = cv.s.get(x, y)
                col = dark
                if col is None:
                    ramp = cv.ramp_for(cur)
                    col = ramp[max(0, ramp.index(cur) - 1)] if ramp else cur
                changes.append((x, y, col))
                break
    for x, y, col in changes:
        cv.put(x, y, col)


def op_rim(cv, op):
    """Rim light: silhouette-edge pixels facing away from the main light get colour c
    (use a cool/bounce colour; for neon scenes, the neon colour)."""
    mask = _mask_of(cv, op)
    key = cv.color(op["c"])
    lx, ly = DIRS[op.get("light", cv.light)]
    width = int(op.get("width", 1))
    s = cv.s

    def outside(px, py):
        # transparent, or an outline pixel (not part of the form, touching transparency)
        if s.get(px, py) == TRANSPARENT:
            return True
        return (px, py) not in mask and any(s.get(px + dx, py + dy) == TRANSPARENT for dx, dy in N8)

    changes = []
    for x, y in mask:
        for k in range(1, width + 1):
            if (lx and outside(x - lx * k, y)) or (ly and outside(x, y - ly * k)):
                changes.append((x, y))
                break
    for x, y in changes:
        cv.put(x, y, key)


def op_glare(cv, op):
    """Diagonal reflection streak(s) across glass/screens: pixels of `on` inside the
    band get colour c. offset moves the band, width sets its thickness."""
    mask = _mask_of(cv, op)
    if not mask:
        return
    key = cv.color(op["c"])
    width = float(op.get("width", 2))
    xs = [p[0] for p in mask]
    ys = [p[1] for p in mask]
    x0, y0 = min(xs), min(ys)
    off = float(op.get("offset", 0.35)) * (max(xs) - x0 + max(ys) - y0)
    bands = op.get("bands", 1)
    for x, y in mask:
        t = (x - x0) + (y - y0) - off
        if 0 <= t < width or (bands > 1 and width + 1.5 <= t < width + 2.5):
            cv.put(x, y, key)


def op_wear(cv, op):
    """Clustered damage instead of single-pixel noise.

    style grime: small 2-4 px blobs of c
          chips: a light pixel (c) with a dark pixel (dark) under it, like chipped paint
          scratches: short 2-3 px diagonal strokes of c
    """
    rng = random.Random(int(op.get("seed", 3)))
    mask = sorted(_mask_of(cv, op))
    if not mask:
        return
    key = cv.color(op["c"])
    dark = cv.color(op["dark"]) if op.get("dark") else None
    style = op.get("style", "grime")
    count = int(op.get("count", max(1, len(mask) // 40)))
    mset = set(mask)
    for _ in range(count):
        x, y = mask[rng.randrange(len(mask))]
        if style == "chips":
            cv.put(x, y, key)
            if dark and (x, y + 1) in mset:
                cv.put(x, y + 1, dark)
        elif style == "scratches":
            dx = rng.choice((-1, 1))
            for k in range(rng.choice((2, 3))):
                if (x + dx * k, y + k) in mset:
                    cv.put(x + dx * k, y + k, key)
        else:
            shape = rng.choice([[(0, 0), (1, 0)], [(0, 0), (0, 1)], [(0, 0), (1, 0), (0, 1)],
                                [(0, 0), (1, 0), (1, 1), (2, 1)], [(0, 0), (1, 1)]])
            for sx, sy in shape:
                if (x + sx, y + sy) in mset:
                    cv.put(x + sx, y + sy, key)


MATERIALS = {
    # op sequences applied to one form (ramp / on / hi are filled in from the material op)
    "plastic": [{"op": "light", "depth": 2, "strength": 1.6, "tilt": 1.6}],
    "painted_metal": [{"op": "light", "depth": 2, "strength": 1.4, "tilt": 2.0}],
    "metal": [{"op": "light", "depth": 3, "strength": 2.0, "tilt": 2.4}],
    "glass": [{"op": "gradient", "dir": "bottom-right"}, {"op": "glare", "width": 2, "bands": 2}],
    "screen": [{"op": "light", "invert": True, "depth": 2, "strength": 1.6, "tilt": 1.6},
               {"op": "glare", "width": 1, "bands": 2, "offset": 0.25}],
    "fabric": [{"op": "light", "depth": 3, "strength": 1.2, "tilt": 1.0}],
    "wood": [{"op": "light", "depth": 2, "strength": 1.0, "tilt": 1.4, "form": "flat"}],
}

def op_material(cv, op):
    """Shade a form as a material: plastic | painted_metal | metal | glass | screen | fabric | wood.
    Needs on + ramp (dark -> light); glass/screen also use `hi` for reflections."""
    kind = op.get("type", "plastic")
    if kind not in MATERIALS:
        raise ValueError(f"unknown material {kind!r}; choose {sorted(MATERIALS)}")
    on = op["on"]
    ramp = _ramp_arg(cv, op, on if isinstance(on, str) else on[0])
    hi = op.get("hi") or ramp[-1]
    region = {k: op[k] for k in ("x", "y", "w", "h") if k in op}
    all_keys = list(dict.fromkeys(([on] if isinstance(on, str) else list(on)) + ramp))
    for step in MATERIALS[kind]:
        sub = {**region, **step, "ramp": ramp, "on": all_keys}
        if step["op"] == "glare":
            sub["c"] = hi
            sub["on"] = all_keys
        if "light" in op:
            sub.setdefault("light", op["light"])
        OPS[step["op"]](cv, sub)


OPS = {
    "rect": op_rect, "ellipse": op_ellipse, "circle": op_ellipse, "poly": op_poly,
    "line": op_line, "pixels": op_pixels, "points": op_points, "fill": op_fill,
    "replace": op_replace, "erase": op_erase, "mirror": op_mirror, "copy": op_copy,
    "outline": op_outline, "shade": op_shade, "dither": op_dither, "noise": op_noise,
    "clip": op_clip, "shift": op_shift,
    "light": op_light, "gradient": op_gradient, "cast_shadow": op_cast_shadow, "rim": op_rim,
    "glare": op_glare, "wear": op_wear, "material": op_material,
}


def render(program: dict, base: Sprite | None = None) -> RenderResult:
    errors: list[str] = []
    palette = {}
    for k, v in (program.get("palette") or {}).items():
        if len(k) != 1 or k in SKIP or k == ERASE:
            errors.append(f"palette key {k!r} must be one char and not '.', ' ' or '~'")
            continue
        try:
            parse_hex(v)
            palette[k] = v
        except ValueError as e:
            errors.append(str(e))

    if program.get("base") == "current" and base is not None:
        sprite = base.copy()
        sprite.palette.update(palette)
    else:
        w = int(program.get("width") or (base.width if base else 32))
        h = int(program.get("height") or (base.height if base else 32))
        sprite = Sprite(w, h, palette)

    cv = Canvas(sprite, program)
    for i, op in enumerate(program.get("ops") or []):
        name = op.get("op") if isinstance(op, dict) else None
        fn = OPS.get(name)
        if not fn:
            errors.append(f"op[{i}]: unknown op {name!r}")
            continue
        try:
            fn(cv, op)
        except (KeyError, ValueError, TypeError, IndexError) as e:
            errors.append(f"op[{i}] {name}: {type(e).__name__}: {e}")
    return RenderResult(sprite, errors)
