"""Normal maps and lit previews (forge/normalmap.py, forge/lighting.py).

Pure numpy + PIL, so these run everywhere (no Aseprite needed). The fixture is a
square with a hard alpha edge: interior is flat, the 1px alpha boundary tilts.
"""
import numpy as np
import pytest
import shutil
import subprocess
from PIL import Image

from forge import lighting, normalmap
from forge.sprite import Sprite


def solid(n: int = 16, color: str = "#808080") -> Sprite:
    s = Sprite(n, n, {"c": color})
    for y in range(n):
        for x in range(n):
            s.set(x, y, "c")
    return s


def square(n: int = 16, inset: int = 2, color: str = "#808080") -> Sprite:
    """Opaque square inset into a transparent canvas (so it has real edges)."""
    s = Sprite(n, n, {"c": color})
    for y in range(inset, n - inset):
        for x in range(inset, n - inset):
            s.set(x, y, "c")
    return s


def rgba_of(img) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), dtype=np.int16)


# ---- height / kernel rules -------------------------------------------------

def test_kernel_size_follows_spriteilluminators_rule():
    # odd(max(3, round(2*radius)))
    assert normalmap.kernel_size_for_radius(0.1) == 3
    assert normalmap.kernel_size_for_radius(0.6) == 3   # round(1.2) -> 1, floor at 3
    assert normalmap.kernel_size_for_radius(1.0) == 3
    assert normalmap.kernel_size_for_radius(1.5) == 3   # round(3) -> 3
    assert normalmap.kernel_size_for_radius(2.0) == 5
    assert normalmap.kernel_size_for_radius(3.0) == 7
    assert normalmap.kernel_size_for_radius(4.0) == 9


def test_height_comes_from_alpha_not_luminance():
    s = solid(4, "#000000")          # black but fully opaque
    assert pytest.approx(1.0) == normalmap.height_field(s.to_rgba(), "alpha").max()
    # luminance instead: a black sprite is low, so the two sources must differ
    assert normalmap.height_field(s.to_rgba(), "luma").max() == pytest.approx(0.0)


# ---- normal map ------------------------------------------------------------

def test_a_fully_opaque_sprite_is_flat_everywhere():
    out = rgba_of(normalmap.normal_map(solid(8)))
    assert np.all(out[..., 0] == 128) and np.all(out[..., 1] == 128)
    assert np.all(out[..., 2] == 255) and np.all(out[..., 3] == 255)


def test_the_alpha_edge_tilts_and_stays_flat_inside():
    out = rgba_of(normalmap.normal_map(square(16, inset=4)))
    # deep interior: flat
    assert tuple(out[8, 8]) == (128, 128, 255, 255)
    # left flank of the shape rises to the right, so its surface faces LEFT: R < 128
    assert out[8, 4, 0] < 128
    # right flank is the mirror image
    assert out[8, 11, 0] > 128


def test_y_convention_a_top_edge_faces_up():
    out = rgba_of(normalmap.normal_map(square(16, inset=4)))
    assert out[4, 8, 1] > 128     # top flank faces up (G > 128)
    assert out[11, 8, 1] < 128    # bottom flank faces down
    flipped = rgba_of(normalmap.normal_map(square(16, inset=4), invert_y=True))
    assert flipped[4, 8, 1] < 128 and flipped[11, 8, 1] > 128


def test_normals_are_unit_length_and_blue_never_dips_below_flat():
    out = rgba_of(normalmap.normal_map(square(16, inset=4))) / 255.0
    n = out[..., :3] * 2.0 - 1.0
    length = np.sqrt((n ** 2).sum(axis=2))
    assert np.allclose(length, 1.0, atol=0.02)
    assert np.all(out[..., 2] >= 0.5)     # Z stays positive


def test_the_z_channel_uses_the_standard_engine_encoding():
    out = rgba_of(normalmap.normal_map(square(16, inset=4)))
    # flat is 255 and a valid normal never encodes below 128
    assert out[8, 8, 2] == 255
    assert np.all(out[..., 2] >= 128)
    # a tilted pixel is still above flat's floor and below the flat value
    assert 128 <= out[4, 8, 2] < 255


def test_transparent_background_zeroes_alpha_and_flattens_rgb():
    out = rgba_of(normalmap.normal_map(square(16, inset=4), transparent_background=True))
    assert tuple(out[0, 0]) == (128, 128, 255, 0)
    assert out[8, 8, 3] == 255            # inside stays opaque
    # without the flag every pixel is opaque
    assert np.all(rgba_of(normalmap.normal_map(square(16, inset=4)))[..., 3] == 255)


def test_blur_widens_the_tilted_band():
    sharp = rgba_of(normalmap.normal_map(square(32, inset=8)))[..., 0]
    soft = rgba_of(normalmap.normal_map(square(32, inset=8), blur=2.0))[..., 0]
    row = 16
    band = lambda a: int(np.sum(a[row, :] != 128))
    assert band(soft) > band(sharp)


# ---- rim (SI's Create Rim/Bevel) -------------------------------------------

def test_rim_makes_a_solid_sprite_volumetric():
    flat = rgba_of(normalmap.normal_map(square(24, inset=2)))
    domed = rgba_of(normalmap.normal_map(square(24, inset=2), rim=0.8, blur=1.0))
    # without rim the interior is flat; with it the flanks tilt outwards
    assert tuple(flat[12, 12]) == (128, 128, 255, 255)
    assert domed[12, 3, 0] < 128          # left flank faces left
    assert domed[3, 12, 1] > 128          # top flank faces up
    # the very centre of a dome is the flat top
    assert abs(int(domed[12, 12, 0]) - 128) <= 2 and abs(int(domed[12, 12, 1]) - 128) <= 2


def test_rim_and_contour_validation():
    with pytest.raises(ValueError):
        normalmap.normal_map(solid(4), rim=-1)
    with pytest.raises(ValueError):
        normalmap.normal_map(solid(4), contour=0)
    with pytest.raises(ValueError):
        normalmap.height_field(solid(4).to_rgba(), "brightness")


def test_rim_of_a_fully_transparent_sprite_is_empty_not_a_crash():
    out = rgba_of(normalmap.normal_map(Sprite(8, 8), rim=0.5))
    assert np.all(out[..., 2] == 255)


def _reference_distance(mask):
    """The obvious per-pixel chamfer loop, kept as the oracle for the fast one."""
    import numpy as _np
    d = _np.where(mask, _np.float32(1e6), _np.float32(0.0))
    h, w = d.shape
    for y in range(h):
        for x in range(w):
            if not mask[y, x]:
                continue
            best = d[y, x]
            if y:
                best = min(best, d[y - 1, x] + 1.0)
                if x:
                    best = min(best, d[y - 1, x - 1] + 1.41421356)
                if x < w - 1:
                    best = min(best, d[y - 1, x + 1] + 1.41421356)
            if x:
                best = min(best, d[y, x - 1] + 1.0)
            d[y, x] = best
    for y in range(h - 1, -1, -1):
        for x in range(w - 1, -1, -1):
            if not mask[y, x]:
                continue
            best = d[y, x]
            if y < h - 1:
                best = min(best, d[y + 1, x] + 1.0)
                if x < w - 1:
                    best = min(best, d[y + 1, x + 1] + 1.41421356)
                if x:
                    best = min(best, d[y + 1, x - 1] + 1.41421356)
            if x < w - 1:
                best = min(best, d[y, x + 1] + 1.0)
            d[y, x] = best
    return d


@pytest.mark.parametrize("shape", [(1, 1), (1, 9), (9, 1), (16, 16), (33, 21)])
def test_the_fast_distance_transform_matches_the_plain_loop(shape):
    rng = np.random.default_rng(7)
    for _ in range(5):
        mask = rng.random(shape) > 0.45
        fast = normalmap.distance_inside(mask)
        slow = _reference_distance(mask)
        assert np.allclose(fast, slow, atol=1e-5), f"mismatch for\n{mask}"


def test_a_big_sprite_is_not_painfully_slow():
    """The row-vectorised transform keeps a 512x512 rim off the seconds scale."""
    import time
    s = solid(512)
    t0 = time.perf_counter()
    normalmap.normal_map(s, rim=0.8, blur=2.0)
    assert time.perf_counter() - t0 < 2.0


# ---- lit preview -----------------------------------------------------------

def lum(img) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float64)


def test_light_above_brightens_the_top_of_a_dome():
    sprite = square(32, inset=2)
    normal = normalmap.normal_map(sprite, rim=0.8, blur=1.0)
    above = lum(lighting.lit_preview(sprite, normal, light=(0.5, 0.1), flatten=True))
    below = lum(lighting.lit_preview(sprite, normal, light=(0.5, 0.9), flatten=True))
    # compare the top and bottom flanks of the dome, away from its edges
    top, bottom = slice(4, 12), slice(20, 28)
    assert above[top, 8:24].mean() > above[bottom, 8:24].mean()
    assert below[bottom, 8:24].mean() > below[top, 8:24].mean()


def test_light_left_sides_are_brighter_on_the_left():
    sprite = square(32, inset=2)
    normal = normalmap.normal_map(sprite, rim=0.8, blur=1.0)
    left = lum(lighting.lit_preview(sprite, normal, light=(0.1, 0.5), flatten=True))
    assert left[8:24, 4:12].mean() > left[8:24, 20:28].mean()


def test_ambient_only_still_renders_and_brightness_scales():
    sprite = square(16, inset=2)
    normal = normalmap.normal_map(sprite)
    dim = lum(lighting.lit_preview(sprite, normal, color=(0, 0, 0, 0)))
    bright = lum(lighting.lit_preview(sprite, normal, color=(0, 0, 0, 0),
                                      ambient=(255, 255, 255, 255)))
    assert bright[8, 8] > dim[8, 8]
    lit50 = lum(lighting.lit_preview(sprite, normal, light=(0.5, 0.5), brightness=50))
    lit100 = lum(lighting.lit_preview(sprite, normal, light=(0.5, 0.5), brightness=100))
    assert lit100[8, 8] > lit50[8, 8]


def test_lit_sprite_writes_the_engine_files(tmp_path):
    sprite = square(16, inset=2)
    normal = normalmap.normal_map(sprite, rim=0.6)
    res = lighting.lit_sprite(sprite, normal, tmp_path / "hero_lit.png", engine="godot")
    assert res["lit"].endswith("hero_lit.png")
    import json
    params = json.loads(open(res["params"]).read())
    assert params["sprite_size"] == [16, 16]
    assert set(params) >= {"light_pos", "light_height", "light_color", "brightness", "ambient_color"}
    shader = open(res["shader"]).read()
    assert "shader_type canvas_item" in shader and "hint_normal" in shader
    assert lighting.godot_shader() == shader


def test_mismatched_normal_size_is_rejected():
    with pytest.raises(ValueError):
        lighting.lit_preview(square(16), normalmap.normal_map(square(8)))


# ---- the shipped Godot shader ----------------------------------------------

GODOT = shutil.which("godot")
_CHECK_GD = '''extends SceneTree

func _initialize() -> void:
\tfor path in ["res://light2d.gdshader", "res://broken.gdshader"]:
\t\tvar sh: Shader = load(path)
\t\tsh.code = sh.code
\t\tprint("UNIFORMS ", path, " -> ", sh.get_shader_uniform_list().size())
\t\tprint("PARSED ", path)
\tquit(0)
'''


@pytest.mark.skipif(not GODOT, reason="godot not installed")
def test_the_godot_shader_parses_and_the_check_can_fail(tmp_path):
    """A shader that does not parse is worthless, and only Godot can tell us."""
    from pathlib import Path as P
    import forge
    template = P(forge.__file__).parent / "templates" / "light2d.gdshader"
    (tmp_path / "project.godot").write_text(
        'config_version=5\n\n[application]\nconfig/name="shader-check"\n')
    (tmp_path / "check.gd").write_text(_CHECK_GD)
    (tmp_path / "light2d.gdshader").write_text(template.read_text())
    # negative control: the check must actually fail on a broken shader
    (tmp_path / "broken.gdshader").write_text(
        template.read_text().replace("vec3 amb =", "vec3 amb = undefined_thing *"))

    proc = subprocess.run([GODOT, "--headless", "--path", str(tmp_path), "-s", "check.gd"],
                          capture_output=True, text=True, timeout=180)
    out = proc.stdout + proc.stderr
    assert "PARSED res://light2d.gdshader" in out, out
    assert "UNIFORMS res://light2d.gdshader -> 7" in out, out
    # the error below must belong to the control, i.e. the good shader is clean
    good, _, bad = out.partition("res://broken.gdshader")
    assert "SHADER ERROR" not in good, good
    assert "SHADER ERROR" in bad, "the check cannot detect a broken shader"
