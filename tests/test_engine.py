import io
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from forge import dsl, qa
from forge.cleanup import CleanupOptions, cleanup
from forge.artist import extract_program
from forge.sprite import Sprite

GUIDE = (Path(__file__).parent.parent / "forge" / "artist_guide.md").read_text()


def example_program():
    return json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", GUIDE, re.S)[0])


def fake_pixel_art(sprite, scale, blur=0.8, jpeg=True, offset=(5, 3)):
    """Upscale onto white, shift, blur and JPEG it, like AI 'pixel art'."""
    img = sprite.to_image(scale)
    canvas = Image.new("RGB", (img.width + 2 * scale + offset[0], img.height + 2 * scale + offset[1]), "white")
    canvas.paste(img, (scale + offset[0], scale + offset[1]), img)
    canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    if jpeg:
        buf = io.BytesIO()
        canvas.save(buf, "JPEG", quality=80)
        canvas = Image.open(io.BytesIO(buf.getvalue()))
    return canvas


def test_guide_example_renders_cleanly():
    res = dsl.render(example_program())
    assert res.errors == []
    rep = qa.check(res.sprite)
    assert rep["outline_coverage"] == 1.0 and rep["components"] == 1


def test_ops_report_errors_instead_of_crashing():
    res = dsl.render({"width": 8, "height": 8, "palette": {"a": "#ff0000"},
                      "ops": [{"op": "rect", "x": 0, "y": 0, "w": 2, "h": 2, "c": "z"},
                              {"op": "nope"}, {"op": "rect", "x": 6, "y": 6, "w": 9, "h": 9, "c": "a"}]})
    assert len(res.errors) == 2
    assert res.sprite.get(7, 7) == "a"  # clipped, not crashed


def test_base_current_patches_only_what_it_touches():
    base = dsl.render(example_program()).sprite
    patched = dsl.render({"base": "current", "palette": {"z": "#00ff00"},
                          "ops": [{"op": "points", "points": [[8, 8]], "c": "z"}]}, base).sprite
    diff = [(x, y) for y in range(16) for x in range(16) if patched.get(x, y) != base.get(x, y)]
    assert diff == [(8, 8)]


def test_mirror_and_outline():
    s = dsl.render({"width": 6, "height": 3, "palette": {"a": "#fff", "k": "#000"},
                    "ops": [{"op": "points", "points": [[1, 1]], "c": "a"},
                            {"op": "mirror", "axis": "x"}, {"op": "outline", "c": "k"}]}).sprite
    assert s.get(1, 1) == "a" and s.get(4, 1) == "a" and s.get(0, 1) == "k" and s.get(5, 1) == "k"


def test_cleanup_recovers_fake_pixel_art():
    original = dsl.render(example_program()).sprite
    fake = fake_pixel_art(original, 12)
    out, rep = cleanup(fake, CleanupOptions(max_colors=len(original.used_keys())))
    assert rep.grid == (12, 12)
    # compare after cropping both to their content
    a = original.to_rgba()
    b = out.to_rgba()
    ya, xa = np.nonzero(a[..., 3]); yb, xb = np.nonzero(b[..., 3])
    ca = a[ya.min():ya.max() + 1, xa.min():xa.max() + 1]
    cb = b[yb.min():yb.max() + 1, xb.min():xb.max() + 1]
    assert ca.shape == cb.shape
    same_alpha = (ca[..., 3] > 0) == (cb[..., 3] > 0)
    assert same_alpha.mean() >= 0.98
    colour_err = np.abs(ca[..., :3].astype(int) - cb[..., :3].astype(int)).max(-1)[ca[..., 3] > 0]
    assert (colour_err < 40).mean() >= 0.95


def test_cleanup_fits_to_target_and_palette():
    img = Image.new("RGB", (200, 100), "white")
    img.paste((200, 30, 30), (20, 20, 180, 80))
    out, rep = cleanup(img, CleanupOptions(width=32, height=32, palette={"r": "#ff0000", "b": "#0000ff"}))
    assert (out.width, out.height) == (32, 32)
    assert out.used_keys() == {"r"}


def test_extract_program_from_reply():
    reply = "Plan: blah\n```json\n{\"width\": 8, \"ops\": []}\n```\nthanks"
    assert extract_program(reply)["width"] == 8


def test_project_learn_style(tmp_path, monkeypatch):
    from forge import profiles, projects
    monkeypatch.setattr(projects, "REGISTRY", tmp_path / "projects.json")
    monkeypatch.setattr(profiles, "ROOT", tmp_path / "profiles")
    art = tmp_path / "art" / "props"
    art.mkdir(parents=True)
    dsl.render(example_program()).sprite.save(art / "potion.png")
    projects.register(tmp_path / "art", "Test game", "tg")
    assert [f["path"] for f in projects.scan("tg")] == ["props/potion.png"]
    summary = projects.learn_style("tg")
    prof = profiles.load("tg")
    assert summary["references"] == ["props/potion.png"]
    assert prof["palette"] and prof["reference_paths"]
    try:
        projects.file_path("tg", "../../etc/passwd")
        assert False, "path escape allowed"
    except ValueError:
        pass


def test_mcp_render_returns_image():
    import asyncio
    from forge.mcp_server import server
    r = asyncio.run(server.call_tool("forge_render", {"program": example_program(), "save": False}))
    assert [c.type for c in r.content] == ["text", "image"]
    assert json.loads(r.content[0].text)["errors"] == []


def _lum(hexc):
    from forge.sprite import parse_hex
    r, g, b, _ = parse_hex(hexc)
    return 0.3 * r + 0.59 * g + 0.11 * b


def test_light_brightens_side_facing_the_light():
    prog = {"width": 20, "height": 20, "light": "top-left",
            "palette": {"1": "#200020", "2": "#502050", "3": "#804080", "4": "#b070b0", "5": "#e0a0e0"},
            "ramps": {"p": ["1", "2", "3", "4", "5"]},
            "ops": [{"op": "rect", "x": 2, "y": 2, "w": 16, "h": 16, "c": "3"},
                    {"op": "light", "on": "3", "ramp": ["1", "2", "3", "4", "5"], "dither": False}]}
    s = dsl.render(prog).sprite
    tl = _lum(s.palette[s.get(4, 4)])
    br = _lum(s.palette[s.get(15, 15)])
    assert tl > br
    prog["ops"][1]["invert"] = True  # recessed: reversed
    s = dsl.render(prog).sprite
    assert _lum(s.palette[s.get(4, 4)]) < _lum(s.palette[s.get(15, 15)])


def test_gradient_light_end_is_toward_dir():
    prog = {"width": 4, "height": 10, "palette": {"a": "#000000", "b": "#808080", "c": "#ffffff"},
            "ops": [{"op": "rect", "x": 0, "y": 0, "w": 4, "h": 10, "c": "a"},
                    {"op": "gradient", "on": "a", "ramp": ["a", "b", "c"], "dir": "top", "dither": False}]}
    s = dsl.render(prog).sprite
    assert s.get(1, 0) == "c" and s.get(1, 9) == "a"


def test_polish_keeps_thin_cord_spark_and_hole():
    """Cleanup must not delete deliberate 1-px details (handoff Task 4 fixtures)."""
    from forge import polish
    rows = ["..........",
            ".kkkkkk...",
            ".kbbbbk...",
            ".kb..bk...",   # donut hole
            ".kbbbbk...",
            ".kkkkkk...",
            "...w......",   # thin copper cord hanging from the body
            "....w.....",
            "...w......",
            "........y."]   # detached spark
    s = Sprite.from_dict({"width": 10, "height": 10, "rows": rows,
                          "palette": {"k": "#101018", "b": "#6060a0", "w": "#d08a3a", "y": "#fff080"}})
    out, _ = polish.full_polish(s, "strong")
    for x, y, k in [(3, 6, "w"), (4, 7, "w"), (3, 8, "w"), (8, 9, "y")]:
        assert out.get(x, y) == k, (x, y, out.get(x, y))
    assert out.get(3, 3) == "." and out.get(4, 3) == "."   # hole stays open


def test_polish_removes_dense_speckle_and_fills_notch():
    from forge import polish
    s = Sprite(12, 12, {"k": "#101018", "b": "#406080", "p": "#ff40c0"})
    for y in range(1, 11):
        for x in range(1, 11):
            s.set(x, y, "k" if x in (1, 10) or y in (1, 10) else "b")
    for x, y in [(3, 3), (5, 3), (7, 4), (4, 5), (6, 6), (8, 7)]:   # scattered bright speckle
        s.set(x, y, "p")
    s.set(5, 1, ".")                                       # notch in the outline
    out, rep = polish.full_polish(s, "normal")
    assert sum(out.get(x, y) == "p" for x in range(12) for y in range(12)) <= 1
    assert out.get(5, 1) == "k"


def test_polish_keeps_a_row_of_button_lights():
    from forge import polish
    s = Sprite(12, 8, {"k": "#101018", "b": "#406080", "r": "#ff3030", "g": "#30ff60"})
    for y in range(1, 7):
        for x in range(1, 11):
            s.set(x, y, "k" if x in (1, 10) or y in (1, 6) else "b")
    for x, k in [(3, "r"), (5, "g"), (7, "r")]:
        s.set(x, 3, k)
    out, _ = polish.full_polish(s, "normal")
    assert [out.get(x, 3) for x in (3, 5, 7)] == ["r", "g", "r"]
