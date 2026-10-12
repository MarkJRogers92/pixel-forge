"""Collision shapes from alpha (forge/collision.py).

Geometry is checked directly (a rectangle traces to 4 corners, an L decomposes
into convex hulls, every hull is convex and within the vertex cap, points stay in
the silhouette). The Godot emitter is checked by loading the .tscn in the engine,
which skips without `godot`.
"""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from forge import collision
from forge.sprite import Sprite

GODOT = shutil.which("godot")


def sprite_of(rows: list[str]) -> Sprite:
    """A sprite from ASCII art; '.' is transparent, anything else is opaque."""
    height = len(rows)
    width = len(rows[0])
    s = Sprite(width, height, {"c": "#808080"})
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != ".":
                s.set(x, y, "c")
    return s


RECT = sprite_of([
    "....",
    ".XX.",
    ".XX.",
    "....",
])

L_SHAPE = sprite_of([
    "......",
    ".XX...",
    ".XX...",
    ".XXXX.",
    ".XXXX.",
    "......",
])

STAIRS = sprite_of([
    "......",
    ".X....",
    ".XX...",
    ".XXX..",
    ".XXXX.",
    "......",
])


# ---- tracing -----------------------------------------------------------------

def test_a_rectangle_traces_as_corner_to_corner_and_simplifies_to_four_corners():
    rings = collision.trace_outline(collision.mask_of(RECT))
    assert len(rings) == 1
    # the raw trace walks pixel corners, so a 2x2 block gives 8 points...
    assert set(rings[0]) == {(1, 1), (2, 1), (3, 1), (3, 2), (3, 3), (2, 3), (1, 3), (1, 2)}
    # ...and simplification keeps exactly the four real corners
    ring = collision.simplify_ring(rings[0], 1.0)
    assert len(ring) == 4
    assert set(map(tuple, ring)) == {(1, 1), (3, 1), (3, 3), (1, 3)}


def test_ring_simplification_does_not_clip_the_closing_edge():
    """The bug this caught: a closed ring treated as an open line lost a corner."""
    ring = [(1, 1), (2, 1), (3, 1), (3, 2), (3, 3), (2, 3), (1, 3), (1, 2)]
    assert set(map(tuple, collision.simplify_ring(ring, 1.0))) == \
        {(1, 1), (3, 1), (3, 3), (1, 3)}


def test_two_shapes_trace_to_two_rings():
    s = sprite_of([
        ".....",
        ".X.X.",
        ".X.X.",
        ".....",
    ])
    rings = collision.trace_outline(collision.mask_of(s))
    assert len(rings) == 2


def test_a_silhouette_is_closed_and_has_the_right_area():
    rings = collision.trace_outline(collision.mask_of(STAIRS))
    assert len(rings) == 1
    # the outline encloses exactly the opaque pixels
    assert collision.area(rings[0]) == 1 + 2 + 3 + 4


def test_a_hole_ring_is_dropped_not_mistaken_for_a_silhouette():
    s = sprite_of([
        ".....",
        ".XXX.",
        ".X.X.",
        ".XXX.",
        ".....",
    ])
    rings = collision.trace_outline(collision.mask_of(s))
    assert len(rings) == 1                      # the hole is not a silhouette
    assert collision.area(rings[0]) == 9        # the outer 3x3 block


# ---- simplification ----------------------------------------------------------

def test_simplify_collapses_a_staircase_to_a_line():
    ring = [(0, 0)] + [(i, i) for i in range(1, 6)]
    out = collision.simplify(ring, tolerance=2.0)
    assert len(out) < len(ring)
    assert tuple(out[0]) == (0, 0)


def test_simplify_with_zero_tolerance_keeps_everything():
    ring = [(0, 0), (1, 1), (2, 0)]
    assert len(collision.simplify(ring, tolerance=0.0)) == 3


# ---- decomposition -----------------------------------------------------------

def test_a_convex_shape_stays_one_hull():
    hulls, warnings = collision.collision_polygons(RECT)
    assert len(hulls) == 1 and warnings == []
    assert collision.is_convex(hulls[0])


def test_an_l_shape_decomposes_into_two_convex_hulls_that_cover_it_exactly():
    hulls, warnings = collision.collision_polygons(L_SHAPE, tolerance=0.5, anchor=(0, 0))
    shape_area = collision.area(collision.trace_outline(collision.mask_of(L_SHAPE))[0])
    assert shape_area == 12
    assert len(hulls) == 2
    assert all(collision.is_convex(h) for h in hulls)
    assert all(len(h) <= 8 for h in hulls)
    # ear clipping + merging is exact here: the two hulls tile the L with no gap
    assert sum(collision.area(h) for h in hulls) == pytest.approx(shape_area, abs=1e-6)
    # and the notch survives at a sane tolerance
    assert any((abs(x - 3) < 1e-6 and abs(y - 3) < 1e-6) for h in hulls for x, y in h)
    assert warnings == []


def test_tolerance_is_a_real_knob_and_the_warning_catches_what_area_cannot():
    """Coarse tolerance silently changes the shape; say so instead of hiding it."""
    # A coarse tolerance spans the L's notch: one hull, and its total area is
    # still exactly 12 - so an area check sees nothing. IoU does, which is why the
    # warning is built on it.
    loose, loose_warnings = collision.collision_polygons(L_SHAPE, tolerance=1.0)
    assert len(loose) == 1
    assert sum(collision.area(h) for h in loose) == pytest.approx(12.0)
    assert [w["rule"] for w in loose_warnings] == ["approximate"]
    assert loose_warnings[0]["accuracy"] < 0.9

    # coarser still and the hitbox also shrinks below the sprite
    shrunk, shrink_warnings = collision.collision_polygons(L_SHAPE, tolerance=2.0)
    assert sum(collision.area(h) for h in shrunk) < 12 - 1e-6
    assert any(w["rule"] == "approximate" for w in shrink_warnings)


U_SHAPE = sprite_of([
    "..........",
    ".XXX..XXX.",
    ".XXX..XXX.",
    ".XXX..XXX.",
    ".XXXXXXXX.",
    "..........",
])


def test_accuracy_is_exactly_one_when_the_decomposition_is_exact():
    """The rasteriser's own convention (pixel centres), pinned."""
    for shape in (RECT, L_SHAPE, U_SHAPE):
        hulls, warnings = collision.collision_polygons(shape, tolerance=0.5, anchor=(0, 0))
        assert collision.accuracy_of(shape, hulls, anchor=(0, 0)) == 1.0, shape
        assert warnings == []


def test_a_u_shape_decomposes_into_prongs_and_a_wedge_with_no_overshoot():
    hulls, warnings = collision.collision_polygons(U_SHAPE, tolerance=0.5, anchor=(0, 0))
    assert len(hulls) == 3
    assert all(collision.is_convex(h) for h in hulls)
    assert sum(collision.area(h) for h in hulls) == pytest.approx(26.0, abs=1e-6)
    assert warnings == []
    # a much coarser tolerance bridges the gap between the prongs, and says so
    bridged, bridged_warnings = collision.collision_polygons(U_SHAPE, tolerance=3.0)
    assert len(bridged) == 1
    assert any(w["rule"] == "approximate" for w in bridged_warnings)


def test_every_hull_respects_the_vertex_cap():
    hulls, warnings = collision.collision_polygons(STAIRS, tolerance=0.0, max_vertices=4)
    assert hulls
    assert all(len(h) <= 4 for h in hulls)
    assert not [w for w in warnings if "vertices" in w["rule"]]


def test_points_land_inside_the_silhouette():
    mask = collision.mask_of(L_SHAPE)
    hulls, _ = collision.collision_polygons(L_SHAPE, anchor=(0, 0), tolerance=0.5)
    for hull in hulls:
        for x, y in hull:
            assert -1e-6 <= x <= mask.shape[1] + 1e-6
            assert -1e-6 <= y <= mask.shape[0] + 1e-6


def test_the_anchor_becomes_the_origin():
    centred, _ = collision.collision_polygons(RECT)               # anchor = centre
    at_origin, _ = collision.collision_polygons(RECT, anchor=(0, 0))
    assert np.allclose(centred[0], at_origin[0] - np.array([2.0, 2.0]))


def test_an_empty_sprite_warns_instead_of_crashing():
    hulls, warnings = collision.collision_polygons(Sprite(8, 8))
    assert hulls == [] and warnings[0]["rule"] == "empty"


def test_bad_arguments_are_rejected():
    with pytest.raises(ValueError):
        collision.collision_polygons(RECT, max_vertices=2)
    with pytest.raises(ValueError):
        collision.collision_polygons(RECT, tolerance=-1)
    with pytest.raises(ValueError):
        collision.mask_of(RECT, alpha_threshold=999)


def test_validation_flags_a_degenerate_shape_and_a_vertex_overflow():
    sliver = np.array([[0, 0], [4, 0], [4, 0.05], [0, 0.05]])   # area 0.2
    assert "area too small" in collision.validate(sliver)
    many = np.array([[np.cos(a), np.sin(a)] for a in np.linspace(0, 2 * np.pi, 13)[:-1]])
    assert any("vertices outside" in p for p in collision.validate(many))


# ---- outputs -----------------------------------------------------------------

def test_json_output_carries_anchor_and_warnings(tmp_path):
    res = collision.save_collision(L_SHAPE, tmp_path / "shapes.json", tolerance=0.5)
    data = json.loads((tmp_path / "shapes.json").read_text())
    assert data["anchor"] == [3, 3]
    assert len(data["polygons"]) == res["hulls"] >= 2
    assert data["warnings"] == res["warnings"]
    assert all(len(p) >= 3 for p in data["polygons"])


_GDScript = '''extends SceneTree

func _initialize() -> void:
\tvar scene: PackedScene = load("res://collision.tscn")
\tif scene == null:
\t\tprint("LOAD_FAILED")
\t\tquit(1)
\t\treturn
\tvar root := scene.instantiate()
\tprint("ROOT ", root.get_class())
\tvar n := 0
\tfor child in root.get_children():
\t\tif child is CollisionShape2D:
\t\t\tn += 1
\t\t\tvar shape: ConvexPolygonShape2D = child.shape
\t\t\tprint("POINTS ", shape.points.size(), " ", shape.points)
\tprint("COUNT ", n)
\tquit(0)
'''


@pytest.mark.skipif(not GODOT, reason="godot not installed")
def test_godot_loads_the_collision_scene(tmp_path):
    """The claim that matters: the engine loads the scene and keeps the shapes."""
    (tmp_path / "project.godot").write_text(
        'config_version=5\n\n[application]\nconfig/name="collision-check"\n')
    (tmp_path / "check.gd").write_text(_GDScript)
    hulls, _ = collision.collision_polygons(L_SHAPE, anchor=(0, 0), tolerance=0.5)
    assert len(hulls) == 2
    collision.godot_scene(hulls, tmp_path / "collision.tscn")

    proc = subprocess.run([GODOT, "--headless", "--path", str(tmp_path), "-s", "check.gd"],
                          capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    assert "LOAD_FAILED" not in out, out
    assert "ROOT StaticBody2D" in out, out
    assert f"COUNT {len(hulls)}" in out, out
    import re
    counts = [int(c) for c in re.findall(r"POINTS (\d+)", out)]
    assert counts == [len(h) for h in hulls], (counts, out)
    # and the geometry the engine holds is the geometry we computed, not just the
    # right number of points
    for hull in hulls:
        for x, y in hull:
            assert f"({float(x):.1f}, {float(y):.1f})" in out, (hull, out)
