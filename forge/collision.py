"""Collision shapes from a sprite's alpha: what PhysicsEditor does, in numpy.

Read out of PhysicsEditor's shipped binary (CodeAndWeb REA run:
`reports/02_physicseditor.md`): the alpha threshold is a strict `>` comparison,
the outline is simplified by Douglas-Peucker with the tolerance in **pixels** (not
percent), and the concave result is decomposed into convex hulls the way Box2D
does (`b2PolygonDecomposition`: ear clipping, then greedy merging), with a vertex
cap per hull. PhysicsEditor's defaults cap at 8 vertices per hull and merge edges
within 2 degrees; both are parameters here.

What this does not do, matching PhysicsEditor: holes are not supported (a hole in
a pixel-art silhouette is rare, and Godot's `ConvexPolygonShape2D` cannot express
one), and circle detection is not automatic there either.

A convex hull is what Godot wants — `ConvexPolygonShape2D` has the same 3..8 vertex
rule — so the emitter writes a `.tscn` with a `StaticBody2D` and one
`CollisionShape2D` per hull. Verified by loading it in the engine
(`tests/test_collision.py`); no `skimage`/`shapely`/`pyclipper` dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .normalmap import to_rgba

DEFAULT_TOLERANCE = 1.0
DEFAULT_ALPHA_THRESHOLD = 0
DEFAULT_MAX_VERTICES = 8
MIN_VERTICES = 3


# ---- outline -----------------------------------------------------------------

def mask_of(source, alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD) -> np.ndarray:
    """Opaque mask: `alpha > threshold`, strictly greater (PhysicsEditor's test)."""
    if not 0 <= alpha_threshold <= 255:
        raise ValueError("alpha_threshold must be 0..255")
    return to_rgba(source)[..., 3] > alpha_threshold


def trace_outline(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Closed outlines of the opaque region, in pixel-corner coordinates.

    Collects the directed border edges (each opaque pixel contributes the sides
    that face a transparent pixel or the image border) and chains them into loops.
    Outer boundaries and holes come out with opposite winding; only the outer ones
    are kept, so the returned loops are the silhouettes.
    """
    h, w = mask.shape
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(a, b):
        edges.setdefault(a, []).append(b)

    for y in range(h):
        for x in range(w):
            if not mask[y, x]:
                continue
            if y == 0 or not mask[y - 1, x]:
                add((x, y), (x + 1, y))            # top, running right
            if x == w - 1 or not mask[y, x + 1]:
                add((x + 1, y), (x + 1, y + 1))    # right, running down
            if y == h - 1 or not mask[y + 1, x]:
                add((x + 1, y + 1), (x, y + 1))    # bottom, running left
            if x == 0 or not mask[y, x - 1]:
                add((x, y + 1), (x, y))            # left, running up

    loops: list[list[tuple[int, int]]] = []
    for start in list(edges):
        while edges.get(start):
            loop = [start]
            current = start
            while True:
                outgoing = edges.get(current)
                if not outgoing:
                    break
                nxt = outgoing.pop(0)
                if not edges[current]:
                    del edges[current]
                current = nxt
                if current == start:
                    break
                loop.append(current)
            if len(loop) >= MIN_VERTICES:
                loops.append(loop)

    if not loops:
        return []
    # keep the outer rings: same winding as the biggest ring
    areas = [signed_area(loop) for loop in loops]
    outer_sign = areas[int(np.argmax(np.abs(areas)))] > 0
    return [loop for loop, area in zip(loops, areas) if (area > 0) == outer_sign]


def signed_area(points) -> float:
    pts = np.asarray(points, dtype=np.float64)
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


# ---- simplification ----------------------------------------------------------

def simplify(points, tolerance: float = DEFAULT_TOLERANCE):
    """Douglas-Peucker on an open polyline, tolerance in pixels (PhysicsEditor's rule)."""
    pts = np.asarray(points, dtype=np.float64)
    if tolerance <= 0 or len(pts) <= MIN_VERTICES:
        return pts
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        seg = pts[last] - pts[first]
        length = float(np.hypot(*seg))
        chunk = pts[first + 1:last]
        if length == 0:
            dist = np.hypot(*(chunk - pts[first]).T)
        else:
            # perpendicular distance to the segment
            rel = chunk - pts[first]
            dist = np.abs(rel[:, 0] * seg[1] - rel[:, 1] * seg[0]) / length
        if len(dist) == 0:
            continue
        far = int(np.argmax(dist))
        if dist[far] > tolerance:
            idx = first + 1 + far
            keep[idx] = True
            stack.extend([(first, idx), (idx, last)])
    return pts[keep]


def simplify_ring(points, tolerance: float = DEFAULT_TOLERANCE):
    """Douglas-Peucker for a **closed** ring.

    An outline is a loop, so simplifying it as one open polyline mis-treats the
    closing edge: that clips a corner off a rectangle and flattens a whole notch
    out of an L-shaped silhouette (both observed). Splitting the ring at the two
    most distant points and simplifying each half keeps the closure honest.
    """
    pts = np.asarray(points, dtype=np.float64)
    if tolerance <= 0 or len(pts) <= MIN_VERTICES:
        return pts
    far = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
    if far == 0:
        return pts
    first = simplify(pts[:far + 1], tolerance)
    second = simplify(np.vstack([pts[far:], pts[:1]]), tolerance)
    return np.vstack([first[:-1], second[:-1]])


# ---- convex decomposition ----------------------------------------------------

def is_convex(points, eps: float = 1e-9) -> bool:
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < MIN_VERTICES:
        return False
    nxt = np.roll(pts, -1, axis=0)
    prev = np.roll(pts, 1, axis=0)
    cross = ((pts - prev)[:, 0] * (nxt - pts)[:, 1]
             - (pts - prev)[:, 1] * (nxt - pts)[:, 0])
    return bool(np.all(cross >= -eps) or np.all(cross <= eps))


def _ccw(points):
    pts = np.asarray(points, dtype=np.float64)
    return pts if signed_area(pts) > 0 else pts[::-1].copy()


def ear_clip(points) -> list[np.ndarray]:
    """Triangulate a simple polygon (ear clipping), tolerating collinear points."""
    pts = _ccw(points)
    indices = list(range(len(pts)))
    triangles = []
    guard = 0
    while len(indices) > MIN_VERTICES and guard < 10 * len(pts) ** 2 + 100:
        guard += 1
        clipped = False
        for k in range(len(indices)):
            i, j, l = indices[k - 1], indices[k], indices[(k + 1) % len(indices)]
            a, b, c = pts[i], pts[j], pts[l]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cross <= 0:           # reflex or degenerate: not an ear
                continue
            if any(_inside(pts[m], a, b, c) for m in indices
                   if m not in (i, j, l)):
                continue
            triangles.append(np.array([a, b, c]))
            indices.pop(k)
            clipped = True
            break
        if not clipped:
            break                   # self-intersecting input: give up gracefully
    if len(indices) == MIN_VERTICES:
        triangles.append(pts[indices])
    return triangles


def _inside(p, a, b, c, eps: float = 1e-9) -> bool:
    d1 = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    d2 = (c[0] - b[0]) * (p[1] - b[1]) - (c[1] - b[1]) * (p[0] - b[0])
    d3 = (a[0] - c[0]) * (p[1] - c[1]) - (a[1] - c[1]) * (p[0] - c[0])
    return d1 >= -eps and d2 >= -eps and d3 >= -eps


def _merge(a, b):
    """Merge two polygons sharing the directed edge a[i]->a[i+1] / b[j]->b[j+1]."""
    a, b = _ccw(a), _ccw(b)
    for i in range(len(a)):
        p, q = a[i], a[(i + 1) % len(a)]
        for j in range(len(b)):
            if abs(b[j][0] - q[0]) < 1e-9 and abs(b[j][1] - q[1]) < 1e-9 and \
               abs(b[(j + 1) % len(b)][0] - p[0]) < 1e-9 and \
               abs(b[(j + 1) % len(b)][1] - p[1]) < 1e-9:
                path_a = np.roll(a, -i - 1, axis=0)[: len(a) - 1]
                path_b = np.roll(b, -j - 1, axis=0)[: len(b) - 1]
                return np.vstack([path_a, path_b])
    return None


def decompose_convex(points, max_vertices: int = DEFAULT_MAX_VERTICES,
                     vertex_eps: float = 1e-6) -> list[np.ndarray]:
    """Convex hulls covering the polygon: ear clipping, then greedy merging."""
    pts = _ccw(points)
    if len(pts) < MIN_VERTICES:
        return []
    if is_convex(pts):
        return [pts]
    hulls = ear_clip(pts)
    merged = True
    while merged:
        merged = False
        for i in range(len(hulls)):
            for j in range(i + 1, len(hulls)):
                if len(hulls[i]) + len(hulls[j]) - 2 > max_vertices:
                    continue
                candidate = _merge(hulls[i], hulls[j])
                if candidate is None or len(candidate) > max_vertices:
                    continue
                if is_convex(candidate):
                    hulls = [h for k, h in enumerate(hulls) if k not in (i, j)]
                    hulls.append(candidate)
                    merged = True
                    break
            if merged:
                break
    return [np.asarray(h, dtype=np.float64) for h in hulls
            if len(h) >= MIN_VERTICES]


# ---- validation --------------------------------------------------------------

def area(points) -> float:
    return abs(signed_area(points))


def validate(hull, max_vertices: int = DEFAULT_MAX_VERTICES) -> list[str]:
    """PhysicsEditor's sanity rules, as warnings rather than errors.

    No sliver rule: measuring thinness by area/perimeter flagged a *legitimate*
    shallow wedge of a U-shaped decomposition (measured 0.31), and a warning that
    fires on correct geometry just teaches people to ignore warnings. Genuinely
    degenerate shapes are caught by the area and vertex-count rules.
    """
    problems = []
    n = len(hull)
    if n < MIN_VERTICES or n > max_vertices:
        problems.append(f"{n} vertices outside 3..{max_vertices}")
    if area(hull) < 0.5:
        problems.append("area too small")
    if not is_convex(hull):
        problems.append("not convex")
    return problems


# ---- public API --------------------------------------------------------------

def collision_polygons(source, *, alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
                       tolerance: float = DEFAULT_TOLERANCE,
                       max_vertices: int = DEFAULT_MAX_VERTICES,
                       anchor: tuple[float, float] | None = None) -> tuple[list, list]:
    """Convex hulls (pixel coordinates) for the sprite's silhouette, plus warnings.

    `tolerance` is in pixels, as PhysicsEditor's is. Coordinates are relative to
    the sprite's top-left; `anchor` records the pivot a body should use (defaults
    to the centre, rounded to a whole pixel).
    """
    if max_vertices < MIN_VERTICES:
        raise ValueError(f"max_vertices must be at least {MIN_VERTICES}")
    if tolerance < 0:
        raise ValueError("tolerance must be >= 0")
    mask = mask_of(source, alpha_threshold)
    if not mask.any():
        return [], [{"rule": "empty", "message": "no opaque pixels"}]
    if anchor is None:
        anchor = (round(mask.shape[1] / 2), round(mask.shape[0] / 2))
    hulls: list = []
    warnings: list = []
    for ring in trace_outline(mask):
        simplified = simplify_ring(ring, tolerance)
        for hull in decompose_convex(simplified, max_vertices):
            hull = hull - np.asarray(anchor, dtype=np.float64)   # anchor becomes the origin
            for problem in validate(hull, max_vertices):
                warnings.append({"rule": problem, "hull": len(hulls),
                                 "message": problem})
            hulls.append(hull)

    # A coarse tolerance changes the shape even when the total area is preserved,
    # so measure how well the shapes actually agree with the silhouette (IoU) and
    # say so. Lower tolerance fits tighter; the cost is more hulls.
    if hulls:
        accuracy = accuracy_of(mask, hulls, anchor=anchor)
        if accuracy < 0.9:
            warnings.append({"rule": "approximate", "accuracy": round(accuracy, 4),
                             "message": f"shapes match {accuracy:.0%} of the silhouette "
                                        f"(IoU) at tolerance {tolerance}; lower the "
                                        f"tolerance for a tighter fit"})
    return hulls, warnings


def to_json(hulls, *, anchor=(0, 0), warnings=None) -> dict:
    """The engine-agnostic shape PhysicsEditor's own exports carry."""
    return {"anchor": [int(anchor[0]), int(anchor[1])],
            "polygons": [[[round(float(x), 2), round(float(y), 2)] for x, y in hull]
                         for hull in hulls],
            "warnings": warnings or []}


def write_json(hulls, path, *, anchor=(0, 0), warnings=None) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(to_json(hulls, anchor=anchor, warnings=warnings), indent=1))
    return str(p)


# ---- Godot -------------------------------------------------------------------

def godot_scene(hulls, out_tscn, *, name: str = "Collision") -> dict:
    """Write a `.tscn`: a StaticBody2D with one CollisionShape2D per convex hull."""
    out = Path(out_tscn)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"[gd_scene load_steps={len(hulls) + 1} format=3]", ""]
    for i, hull in enumerate(hulls):
        flat = ", ".join(f"{float(x):g}, {float(y):g}" for x, y in hull)
        lines += [f'[sub_resource type="ConvexPolygonShape2D" id="Hull{i}"]',
                  f"points = PackedVector2Array({flat})", ""]
    lines += [f'[node name="{name}" type="StaticBody2D"]']
    for i in range(len(hulls)):
        lines += ["", f'[node name="Hull{i}" type="CollisionShape2D" parent="."]',
                  f'shape = SubResource("Hull{i}")']
    out.write_text("\n".join(lines) + "\n")
    return {"tscn": str(out), "hulls": len(hulls)}


def save_collision(source, out=None, *, tscn=None, name: str = "Collision", **kwargs) -> dict:
    """`collision_polygons` plus whichever files were asked for."""
    hulls, warnings = collision_polygons(source, **kwargs)
    anchor = kwargs.get("anchor") or _centre_of(source)
    result = {"hulls": len(hulls), "warnings": warnings, "anchor": list(anchor),
              "vertices": [len(h) for h in hulls],
              "accuracy": round(accuracy_of(source, hulls, anchor=anchor), 4)}
    if out:
        result["json"] = write_json(hulls, out, anchor=anchor, warnings=warnings)
    if tscn:
        result.update(godot_scene(hulls, tscn, name=name))
    return result


def rasterize(hulls, shape, *, anchor=(0, 0)) -> np.ndarray:
    """Boolean mask of the hulls, using pixel centres (the mask's own convention).

    PIL's `ImageDraw.polygon` is not usable here: it fills a pixel when the polygon
    merely touches it, so a 2x2 square came back as 3x3 and every accuracy figure
    was wrong (measured 0.44 for an exact match). Pixel (x, y) counts as covered
    when its centre is inside the hull.
    """
    h, w = shape
    ys, xs = np.mgrid[0:h, 0:w]
    px = xs + 0.5 - anchor[0]
    py = ys + 0.5 - anchor[1]
    cover = np.zeros((h, w), dtype=bool)
    for hull in hulls:
        pts = np.asarray(hull, dtype=np.float64)
        if len(pts) < MIN_VERTICES:
            continue
        sign = 1.0 if signed_area(pts) > 0 else -1.0
        inside = np.ones((h, w), dtype=bool)
        for i in range(len(pts)):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % len(pts)]
            cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
            inside &= (sign * cross) >= -1e-9
        cover |= inside
    return cover


def accuracy_of(source, hulls, *, anchor=(0, 0)) -> float:
    """IoU between the shapes and the sprite's silhouette (1.0 = exact).

    The hulls must be in the same space as `anchor` - `collision_polygons` returns
    them relative to it, so pass the same anchor back or the answer is meaningless
    (measured: 0.04 instead of 1.00 when the anchor is dropped).

    Area alone cannot see a shape change: a coarse tolerance can drop a notch off
    an L-shaped silhouette and gain an equal area elsewhere, leaving the ratio at
    1.0 (measured). Intersection-over-union can see it, so this is what the
    tolerance warning uses.
    """
    mask = source if isinstance(source, np.ndarray) and source.dtype == bool else mask_of(source)
    if not mask.any() or not hulls:
        return 0.0
    covered = rasterize(hulls, mask.shape, anchor=anchor)
    union = int((covered | mask).sum())
    return float((covered & mask).sum() / union) if union else 0.0


def _centre_of(source) -> tuple[int, int]:
    rgba = to_rgba(source)
    return (round(rgba.shape[1] / 2), round(rgba.shape[0] / 2))
