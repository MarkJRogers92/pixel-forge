"""Final automatic cleanup applied to finished sprites.

Removes *noise* while keeping *intent*:
  - a stray pixel whose colour is close to its surroundings (texture speckle,
    a lone dither dot) is replaced by the dominant neighbour colour;
  - a stray pixel that contrasts strongly (glint, rivet, button light, eye) is kept.
Checkerboard dithering is untouched: its pixels share colour with diagonal neighbours.
"""
from __future__ import annotations

import numpy as np

from .cleanup import rgb_to_lab
from .sprite import TRANSPARENT, Sprite, parse_hex

N8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


def _lab(hex_color: str) -> np.ndarray:
    return rgb_to_lab(np.array([parse_hex(hex_color)[:3]], dtype=np.uint8))[0]


def remove_noise(s: Sprite, keep_contrast: float = 30.0, passes: int = 2) -> tuple[Sprite, int]:
    """Return a cleaned copy and how many pixels changed.

    keep_contrast: Lab distance above which a lone pixel is treated as a deliberate accent.
    """
    out = s.copy()
    lab = {k: _lab(v) for k, v in s.palette.items()}
    changed = 0
    for _ in range(passes):
        g = out.grid
        edits = []
        for y in range(out.height):
            for x in range(out.width):
                c = g[y][x]
                if c == TRANSPARENT:
                    continue
                nb = [out.get(x + dx, y + dy) for dx, dy in N8]
                if TRANSPARENT in nb or c in nb:
                    continue  # silhouette pixels and connected pixels are never touched
                vals, counts = np.unique(nb, return_counts=True)
                major = vals[counts.argmax()]
                if c in lab and major in lab and np.linalg.norm(lab[c] - lab[major]) >= keep_contrast:
                    continue  # strong accent: keep
                edits.append((x, y, str(major)))
        for x, y, k in edits:
            out.grid[y][x] = k
        changed += len(edits)
        if not edits:
            break
    return out, changed


N4 = [(0, -1), (-1, 0), (1, 0), (0, 1)]


def _darkest(s: Sprite, keys) -> str:
    return min(keys, key=lambda k: _lab(s.palette[k])[0])


def _exterior(s: Sprite) -> set:
    """Transparent pixels connected to the canvas border (outside the shape)."""
    seen = set()
    stack = [(x, y) for x in range(s.width) for y in (0, s.height - 1)] + \
            [(x, y) for y in range(s.height) for x in (0, s.width - 1)]
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or not s.inside(x, y) or s.get(x, y) != TRANSPARENT:
            continue
        seen.add((x, y))
        stack.extend((x + dx, y + dy) for dx, dy in N4)
    return seen


def clean_silhouette(s: Sprite, outline: str | None = None, passes: int = 2) -> tuple[Sprite, int]:
    """Fill 1px notches, trim 1px spurs and give the shape one consistent dark outline."""
    out = s.copy()
    changed = 0
    used = out.used_keys()
    if not used:
        return out, 0
    outline = outline or _darkest(out, used)
    for _ in range(passes):
        exterior = _exterior(out)
        edits = []
        for y in range(out.height):
            for x in range(out.width):
                c = out.grid[y][x]
                if c == TRANSPARENT and (x, y) not in exterior:
                    continue  # enclosed holes (donuts, windows) are never filled
                n4 = [out.get(x + dx, y + dy) for dx, dy in N4]
                opaque_n = [k for k in n4 if k != TRANSPARENT]
                if c == TRANSPARENT and len(opaque_n) == 3:
                    # a dent in a solid outline: the pixel across from the open side is body
                    # interior. Gaps between zig-zag cord segments fail this test and stay open.
                    ox, oy = next((dx, dy) for dx, dy in N4 if out.get(x + dx, y + dy) == TRANSPARENT)
                    ix, iy = x - ox, y - oy
                    inner = out.get(ix, iy)
                    if inner != TRANSPARENT and all(out.get(ix + dx, iy + dy) != TRANSPARENT
                                                    for dx, dy in N4 if (ix + dx, iy + dy) != (x, y)):
                        edits.append((x, y, outline))
                # (nothing is ever deleted: thin cords, antennae and sparks are often intended)
        for x, y, k in edits:
            out.grid[y][x] = k
        changed += len(edits)
        if not edits:
            break
    # consistent outer outline on solid bodies: a silhouette pixel becomes the outline colour
    # only if it borders the body's interior. 1-px features (cords, wires, sparks) have no
    # interior and keep their own colours.
    def edge(px, py):
        return out.get(px, py) != TRANSPARENT and \
            any(out.get(px + dx, py + dy) == TRANSPARENT for dx, dy in N4)

    edits = []
    for y in range(out.height):
        for x in range(out.width):
            c = out.grid[y][x]
            if c in (TRANSPARENT, outline) or not edge(x, y):
                continue
            solid = [(dx, dy) for dx, dy in N4 if out.get(x + dx, y + dy) != TRANSPARENT]
            body_edge = len(solid) >= 3 or (len(solid) == 2 and solid[0][0] != -solid[1][0]
                                             and solid[0][1] != -solid[1][1])  # side or corner
            if body_edge and any(not edge(x + dx, y + dy) for dx, dy in solid):
                edits.append((x, y))
    for x, y in edits:
        out.grid[y][x] = outline
    return out, changed + len(edits)


def declutter(s: Sprite, keep_contrast: float = 30.0, density: int = 3, passes: int = 2) -> tuple[Sprite, int]:
    """Calm noisy texture while keeping edges and sparse deliberate accents.

    1. majority filter: an interior pixel whose 8 neighbours mostly (>= 5) share another
       colour, and that is not a strong accent, takes that colour.
    2. dense speckle: strong-contrast lone pixels are accents only when sparse; if `density`
       or more of them sit within 2 px of each other, they are noise and get replaced.
    """
    out = s.copy()
    lab = {k: _lab(v) for k, v in s.palette.items()}
    changed = 0
    for _ in range(passes):
        g = out.grid
        edits = []
        lone_accents = []
        for y in range(out.height):
            for x in range(out.width):
                c = g[y][x]
                if c == TRANSPARENT:
                    continue
                nb = [out.get(x + dx, y + dy) for dx, dy in N8]
                if TRANSPARENT in nb:
                    continue
                vals, counts = np.unique(nb, return_counts=True)
                major, cnt = str(vals[counts.argmax()]), int(counts.max())
                if major == c:
                    continue
                strong = np.linalg.norm(lab[c] - lab[major]) >= keep_contrast
                # neighbours of a similar colour (same ramp: a light's highlight/shade) count as kin
                kin = [k for k in nb if k == c or np.linalg.norm(lab[k] - lab[c]) < 32]
                # never break a 1-px line: kin on two opposite sides
                on_line = any(out.get(x + dx, y + dy) in kin and out.get(x - dx, y - dy) in kin
                              for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)))
                if on_line:
                    continue
                if not kin and strong:
                    lone_accents.append((x, y, major))
                elif cnt >= 5 and np.linalg.norm(lab[c] - lab[major]) < 18:
                    edits.append((x, y, major))   # near-identical shade speckle: smooth it
        # accents that are crowded together are noise
        pos = {(x, y) for x, y, _ in lone_accents}
        for x, y, major in lone_accents:
            crowd = [(x + dx, y + dy) for dx in range(-2, 3) for dy in range(-2, 3)
                     if (dx or dy) and (x + dx, y + dy) in pos]
            if len(crowd) + 1 < density:
                continue
            # lights, buttons and rivets line up in a row or column; speckle is scattered
            if all(cy == y for _, cy in crowd) or all(cx == x for cx, _ in crowd):
                continue
            edits.append((x, y, major))
        for x, y, k in edits:
            out.grid[y][x] = k
        changed += len(edits)
        if not edits:
            break
    return out, changed


def full_polish(s: Sprite, strength: str = "normal") -> tuple[Sprite, dict]:
    """noise removal + optional declutter + silhouette cleanup. strength: light|normal|strong"""
    report = {}
    s, report["noise"] = remove_noise(s)
    if strength in ("normal", "strong"):
        s, report["declutter"] = declutter(s, density=3 if strength == "normal" else 2,
                                           passes=2 if strength == "normal" else 3)
    s, report["silhouette"] = clean_silhouette(s)
    return s, report


def reduce_palette(s: Sprite, colors: int) -> Sprite:
    """Merge similar colours (k-means in Lab over the used colours, weighted by use)."""
    from .cleanup import kmeans_palette, map_to_palette
    arr = s.to_rgba()
    mask = arr[..., 3] > 0
    if len(s.used_keys()) <= colors:
        return s.copy()
    pal = kmeans_palette(arr[..., :3][mask], colors)
    idx = map_to_palette(arr, pal)
    out = s.copy()
    keys = {}
    for i, rgb in enumerate(pal):
        keys[i] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"[i]
    out.palette = {keys[i]: "#%02x%02x%02x" % tuple(int(v) for v in pal[i]) for i in keys}
    out.grid = [[keys[idx[y, x]] if idx[y, x] >= 0 else TRANSPARENT for x in range(s.width)]
                for y in range(s.height)]
    return out
