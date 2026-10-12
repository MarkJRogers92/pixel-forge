"""The optional normal-map pass and lighting preview (forge/normalmap.py).

Fixtures are synthetic or the project's own approved reference art; nothing here
calls a provider or the (absent) SpriteIlluminator binary.
"""
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from forge import normalmap as nm

REFS = Path(__file__).resolve().parent.parent / "profiles" / "refs" / "dead-mall"
FLAT = nm.FLAT


# --------------------------------------------------------------------------- #
# fixtures: (1) a known orientation, (2) a stepped silhouette, (3) approved art
# --------------------------------------------------------------------------- #
def block(w=16, h=16) -> np.ndarray:
    arr = np.zeros((h, w, 4), np.uint8)
    arr[4:12, 4:12] = (200, 120, 60, 255)
    return arr


def two_tone(w=20, h=14) -> np.ndarray:
    """A flat bar with a hard vertical colour boundary down the middle."""
    arr = np.zeros((h, w, 4), np.uint8)
    arr[3:11, 3:17] = (60, 60, 60, 255)
    arr[3:11, 10:17] = (220, 220, 220, 255)
    return arr


def staircase() -> np.ndarray:
    arr = np.zeros((20, 20, 4), np.uint8)
    for i in range(5):
        arr[2 + i * 2:4 + i * 2, 2:6 + i * 2] = (120, 120, 120, 255)
    return arr


# --------------------------------------------------------------------------- #
# (1) known orientation
# --------------------------------------------------------------------------- #
def test_block_edge_normals_point_outward():
    arr, n = block(), nm.normal_map_array(block())
    assert list(n[7, 4, :3]) == [37, 128, 218], "left edge must point left (-x)"
    assert n[7, 11, 0] > 128, "right edge must point right (+x)"
    assert n[4, 7, 1] > 128, "top edge must point up (opengl +y)"
    assert n[11, 7, 1] < 128, "bottom edge must point down"
    assert np.array_equal(n[..., 3], np.where(arr[..., 3] >= 128, 255, 0))
    # blue is always out of the screen and never negative
    assert (n[..., 2] >= 128).all()


def test_directx_flips_only_the_green_channel():
    o = nm.normal_map_array(block(), convention="opengl")
    d = nm.normal_map_array(block(), convention="directx")
    assert np.array_equal(o[..., 0], d[..., 0])
    assert np.array_equal(o[..., 2], d[..., 2])
    assert np.array_equal(o[..., 3], d[..., 3])
    # the encode maps [-1, 1] onto [0, 255], so flipping g gives 255 - value
    assert int(o[4, 7, 1]) + int(d[4, 7, 1]) == 255
    # decoded directions agree once each is read with its own convention (8-bit
    # quantisation keeps a flat normal at ~+0.004, not exactly 0)
    no, nd = nm.decode_normals(o), nm.decode_normals(d, convention="directx")
    assert np.allclose(no, nd, atol=0.01)


def test_decoded_normals_are_unit_and_flat_is_plus_z():
    flat = np.array([[[*FLAT, 255]]], np.uint8)
    n = nm.decode_normals(flat)
    assert np.allclose(n[0, 0], [0, 0, 1], atol=0.01)
    mapped = nm.normal_map_array(block())
    lengths = np.linalg.norm(nm.decode_normals(mapped), axis=-1)
    assert np.allclose(lengths[mapped[..., 3] > 0], 1.0, atol=1e-3)


# --------------------------------------------------------------------------- #
# transparency behaviour
# --------------------------------------------------------------------------- #
def test_transparent_pixels_are_flat_and_empty_input_is_safe():
    arr = block()
    n = nm.normal_map_array(arr)
    transparent = arr[..., 3] < 128
    assert np.array_equal(n[transparent][:, :3], np.tile(FLAT, (transparent.sum(), 1)))
    assert (n[transparent][:, 3] == 0).all()
    empty = nm.normal_map_array(np.zeros((8, 8, 4), np.uint8))
    assert np.all(empty[..., :3] == FLAT) and np.all(empty[..., 3] == 0)
    opaque = nm.normal_map_array(np.full((8, 8, 4), 200, np.uint8))
    assert np.all(opaque[..., 3] == 255)


# --------------------------------------------------------------------------- #
# (2) stepped silhouette
# --------------------------------------------------------------------------- #
def test_staircase_ledges_face_up_and_step_ends_face_right():
    n = nm.normal_map_array(staircase())
    for i in range(1, 5):
        ledge_x, ledge_y = 6 + (i - 1) * 2, 2 + i * 2      # first exposed row of the step
        assert n[ledge_y, ledge_x, 1] > 128, f"step {i} ledge must point up"
    for i in range(5):
        step_y, step_x = 2 + i * 2, 5 + i * 2              # right end of the step
        assert n[step_y, step_x, 0] > 128, f"step {i} end must point right"


def test_edge_treatment_changes_the_border_not_the_interior():
    arr = block()
    clamp = nm.normal_map_array(arr, edge_treatment="clamp")
    zero = nm.normal_map_array(arr, edge_treatment="zero")
    interior = (slice(5, 11), slice(5, 11))
    assert np.array_equal(clamp[interior], zero[interior])
    # a silhouette sitting on the canvas border: clamp keeps it flat, zero tilts it away
    touching = np.zeros((12, 12, 4), np.uint8)
    touching[0:12, 0:6] = (120, 120, 120, 255)
    ct = nm.normal_map_array(touching, edge_treatment="clamp")
    zt = nm.normal_map_array(touching, edge_treatment="zero")
    assert not np.array_equal(ct[0], zt[0])
    assert ct[0, 0, 0] == 128 and zt[0, 0, 0] < 128
    assert np.array_equal(ct[4:8, 3:5], zt[4:8, 3:5]), "the interior is unaffected"
    with pytest.raises(nm.NormalMapError):
        nm.normal_map_array(arr, edge_treatment="bounce")


# --------------------------------------------------------------------------- #
# independent controls
# --------------------------------------------------------------------------- #
def test_surface_and_detail_strengths_are_independent():
    arr = two_tone()
    surf_only = nm.normal_map_array(arr, strength=1.0, detail_strength=0.0)
    with_detail = nm.normal_map_array(arr, strength=1.0, detail_strength=1.0)
    # strength=0 removes the silhouette inflation entirely
    no_surface = nm.normal_map_array(arr, strength=0.0, detail_strength=1.0)
    assert list(no_surface[6, 3, :3]) == list(FLAT)
    # the colour boundary is embossed only by the detail term
    boundary = (slice(4, 10), slice(9, 11))
    assert not np.array_equal(surf_only[boundary], with_detail[boundary])
    # ... and it does not move the silhouette rim, which the surface term owns
    rim = (slice(5, 9), 3)
    assert np.max(np.abs(surf_only[rim].astype(int) - with_detail[rim].astype(int))) <= 2
    # a flat-coloured sprite has no emboss at all, whatever the detail strength
    plain = block()
    assert np.array_equal(nm.normal_map_array(plain, detail_strength=0.0),
                          nm.normal_map_array(plain, detail_strength=3.0))


def test_strength_scales_the_tilt_monotonically():
    arr = block()
    weak = int(nm.normal_map_array(arr, strength=0.5)[7, 4, 0])
    strong = int(nm.normal_map_array(arr, strength=2.0)[7, 4, 0])
    assert strong < weak < 128, "more strength tilts the left rim further away from flat"


# --------------------------------------------------------------------------- #
# lighting response direction
# --------------------------------------------------------------------------- #
def test_light_response_follows_the_light_direction():
    arr, n = block(), nm.normal_map_array(block())
    box = (slice(4, 12), slice(4, 12))

    def halves(light):
        img = np.asarray(nm.lighting_preview(arr, n, light))[box].astype(float)
        return img[:, :4, 0].mean(), img[:, 4:, 0].mean()

    left_lit, right_lit = halves("left")
    assert left_lit > right_lit, "a light from the left must brighten the left flank"
    right_lit_left, right_lit_right = halves("right")
    assert right_lit_right > right_lit_left, "a light from the right must brighten the right flank"
    # the response genuinely REVERSES with the light
    assert (left_lit - right_lit) > 0 > (right_lit_left - right_lit_right)

    top = np.asarray(nm.lighting_preview(arr, n, "top")).astype(float)[box]
    front = np.asarray(nm.lighting_preview(arr, n, "front")).astype(float)[box]
    assert top[:, :, 0].mean() != front[:, :, 0].mean()
    with pytest.raises(nm.NormalMapError):
        nm.lighting_preview(arr, n, "from-the-ceiling")
    with pytest.raises(nm.NormalMapError):
        nm.lighting_preview(arr, n, (0.0, 0.0, 0.0))


def test_lighting_preview_keeps_dimensions_and_transparency():
    arr, n = block(), nm.normal_map_array(block())
    lit = np.asarray(nm.lighting_preview(arr, n, "top-left"))
    assert lit.shape == arr.shape
    assert (lit[..., 3] == arr[..., 3]).all()
    assert (lit[0, 0] == 0).all()


def test_review_sheet_has_one_panel_per_light_plus_colour_and_normal():
    arr, n = block(), nm.normal_map_array(block())
    sheet = np.asarray(nm.review_sheet(arr, n, ("left", "right"), scale=1))
    assert sheet.shape[0] >= arr.shape[0] and sheet.shape[1] >= 4 * arr.shape[1]


# --------------------------------------------------------------------------- #
# (3) approved project art
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["payphone.png", "rc-car.png"])
def test_dead_mall_reference_is_supported(name):
    src = Image.open(REFS / name).convert("RGBA")
    n = nm.normal_map_image(src)
    assert n.size == src.size
    a = np.asarray(src)
    m = np.asarray(n)
    assert np.array_equal(m[..., 3] > 0, a[..., 3] >= 128)
    ys, xs = np.nonzero(a[..., 3] >= 128)
    # the top of the silhouette faces up, the bottom faces down (opengl convention)
    assert m[ys[ys == ys.min()], xs[ys == ys.min()], 1].mean() > 128
    assert m[ys[ys == ys.max()], xs[ys == ys.max()], 1].mean() < 128
    sheet = nm.review_sheet(src, n, ("top-left", "bottom-right"))
    assert sheet.width > src.width


def test_prototype_fixtures_run_through_the_cli_shaped_api(tmp_path):
    """The same call the `forge.sh normalmap` command makes, on a synthetic fixture."""
    src = Image.fromarray(staircase(), "RGBA")
    normal = nm.normal_map_image(src, strength=1.0, detail_strength=0.5)
    normal.save(tmp_path / "normal.png")
    assert Image.open(tmp_path / "normal.png").size == src.size
    assert isinstance(normal.tobytes(), bytes) and len(normal.tobytes()) == src.width * src.height * 4


def test_normal_map_of_a_sprite_matches_the_image_path():
    from forge.sprite import Sprite
    s = Sprite.from_image(Image.fromarray(block(), "RGBA"))
    a = np.asarray(nm.normal_map_sprite(s))
    b = np.asarray(nm.normal_map_image(Image.fromarray(block(), "RGBA")))
    assert np.array_equal(a, b)


def test_rgba_input_is_validated():
    with pytest.raises(nm.NormalMapError):
        nm.normal_map_array(np.zeros((4, 4), np.uint8))
    with pytest.raises(nm.NormalMapError):
        nm.normal_map_array(block(), convention="unity")
