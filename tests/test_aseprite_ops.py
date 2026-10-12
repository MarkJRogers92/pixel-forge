"""Aseprite-backed passes (forge/aseprite_ops.py and the matching DSL ops).

These exercise a real Aseprite batch round trip, so they are skipped when
Aseprite is not installed (same policy as the existing Aseprite round-trip test).
"""
import pytest

from forge import aseprite_ops, dsl
from forge.sprite import KEY_CHARS, Sprite

pytestmark = pytest.mark.skipif(not aseprite_ops.available(), reason="Aseprite not installed")


def gradient(n: int = 20) -> Sprite:
    """An n x n sprite with 40 distinct colours (fewer than the 62 palette keys)."""
    s = Sprite(n, n, {})
    pal = {}
    for y in range(n):
        for x in range(n):
            b = (y * n + x) % 40
            pal[KEY_CHARS[b]] = "#%02x%02x%02x" % ((b * 6) % 256, (b * 11) % 256, (b * 17) % 256)
            s.set(x, y, KEY_CHARS[b])
    s.palette = pal
    return s


def flat(rgba: str, n: int = 2) -> Sprite:
    s = Sprite(n, n, {"a": rgba})
    s.grid = [["a"] * n for _ in range(n)]
    return s


def test_quantize_reduces_colours_and_keeps_size():
    src = gradient()
    out = aseprite_ops.quantize(src, 4)
    assert (out.width, out.height) == (src.width, src.height)
    assert len(out.used_keys()) <= 4


def test_dither_snaps_to_the_target_palette():
    src = gradient()
    target = ["#101018", "#c03040", "#f0e8c0"]
    out = aseprite_ops.dither(src, "ordered", palette=target)
    assert (out.width, out.height) == (src.width, src.height)
    assert set(out.palette.values()) <= set(target)


def test_blend_multiply_math():
    out = aseprite_ops.blend(flat("#ffffff"), flat("#808080"), mode="multiply")
    assert len(out.used_keys()) == 1
    assert out.palette[out.get(0, 0)].startswith("#808080")


def test_dsl_aseprite_ops_run_in_a_program():
    program = {
        "width": 16, "height": 16,
        "palette": {c: "#%02x%02x%02x" % (i * 16 % 256, i * 37 % 256, i * 91 % 256)
                    for i, c in enumerate("abcdefghijklmnop")},
        "ops": [
            {"op": "rect", "x": 0, "y": 0, "w": 16, "h": 16, "c": "a"},
            {"op": "gradient", "on": "a", "ramp": list("abcdefghijklmnop"), "dir": "top"},
            {"op": "aseprite_quantize", "colors": 4},
        ],
    }
    res = dsl.render(program)
    assert res.errors == []
    assert (res.sprite.width, res.sprite.height) == (16, 16)
    assert len(res.sprite.used_keys()) <= 4


def test_unknown_algorithm_is_an_op_error_not_a_crash():
    res = dsl.render({"width": 4, "height": 4, "palette": {"a": "#fff"},
                      "ops": [{"op": "rect", "x": 0, "y": 0, "w": 4, "h": 4, "c": "a"},
                              {"op": "aseprite_dither", "algorithm": "nope"}]})
    assert len(res.errors) == 1 and "nope" in res.errors[0]


def test_missing_aseprite_becomes_an_op_error(monkeypatch):
    monkeypatch.setattr(aseprite_ops.aseprite, "find_executable", lambda: None)
    res = dsl.render({"width": 4, "height": 4, "palette": {"a": "#fff"},
                      "ops": [{"op": "rect", "x": 0, "y": 0, "w": 4, "h": 4, "c": "a"},
                              {"op": "aseprite_quantize", "colors": 4}]})
    assert len(res.errors) == 1 and "Aseprite" in res.errors[0]


def test_run_pass_dispatches_and_validates():
    src = gradient()
    assert aseprite_ops.run_pass(src, None) is src  # no spec -> unchanged

    q = aseprite_ops.run_pass(src, {"op": "quantize", "colors": 4})
    assert len(q.used_keys()) <= 4

    target = ["#101018", "#c03040", "#f0e8c0"]
    d = aseprite_ops.run_pass(src, {"op": "dither", "algorithm": "ordered", "palette": target})
    assert set(d.palette.values()) <= set(target)

    with pytest.raises(ValueError):
        aseprite_ops.run_pass(src, {"op": "wobble"})
