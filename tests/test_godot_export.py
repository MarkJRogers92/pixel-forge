"""The Godot side of a sheet (forge/godot.py).

The text of the .tres is checked here without Godot; the semantics — that Godot
actually loads it, keeps the frame count, loop, speed, region and the trimmed
margin — are checked by loading it in the engine, and skip when godot is absent.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from forge import godot

GODOT = shutil.which("godot")

# Two frames, 8x8 canvas, the second trimmed: Aseprite's json-hash shape.
ATLAS = {
    "frames": {
        "Sprite 0": {"frame": {"x": 0, "y": 0, "w": 8, "h": 8}, "rotated": False,
                     "trimmed": False, "spriteSourceSize": {"x": 0, "y": 0, "w": 8, "h": 8},
                     "sourceSize": {"w": 8, "h": 8}, "duration": 100},
        "Sprite 1": {"frame": {"x": 8, "y": 0, "w": 4, "h": 6}, "rotated": False,
                     "trimmed": True, "spriteSourceSize": {"x": 2, "y": 1, "w": 4, "h": 6},
                     "sourceSize": {"w": 8, "h": 8}, "duration": 100},
    },
    "meta": {"size": {"w": 12, "h": 8}, "format": "RGBA8888"},
}


@pytest.fixture()
def atlas_file(tmp_path):
    p = tmp_path / "sheet.json"
    p.write_text(json.dumps(ATLAS))
    return p


def test_tres_text_has_a_region_per_frame_and_a_margin_when_trimmed(atlas_file, tmp_path):
    out = tmp_path / "frames.tres"
    res = godot.sprite_frames_tres(atlas_file, "res://sheet.png", out, fps=12)
    text = out.read_text()
    assert res == {"frames": 2, "animation": "default", "fps": 12.0, "loop": True,
                   "texture": "res://sheet.png", "tres": str(out), "trimmed": True}
    assert text.startswith('[gd_resource type="SpriteFrames" load_steps=4 format=3]')
    assert text.count('[sub_resource type="AtlasTexture"') == 2
    assert "region = Rect2(0, 0, 8, 8)" in text
    assert "region = Rect2(8, 0, 4, 6)" in text
    # the trimmed frame gets the trimmed-off padding: where the kept content sat
    # (2, 1) and how much was cut (8-4, 8-6)
    assert "margin = Rect2(2, 1, 4, 2)" in text
    assert '"name": &"default"' in text and '"speed": 12.0' in text


def test_tres_of_an_untrimmed_atlas_has_no_margins(tmp_path):
    flat = {"frames": [{"frame": {"x": 0, "y": 0, "w": 8, "h": 8}, "duration": 100},
                       {"frame": {"x": 8, "y": 0, "w": 8, "h": 8}, "duration": 100}]}
    atlas = tmp_path / "a.json"
    atlas.write_text(json.dumps(flat))
    out = tmp_path / "flat.tres"
    res = godot.sprite_frames_tres(atlas, "res://s.png", out, loop=False)
    assert res["trimmed"] is False
    assert "margin" not in out.read_text()
    assert '"loop": false' in out.read_text()


def test_bad_input_is_rejected(tmp_path):
    empty = tmp_path / "e.json"
    empty.write_text(json.dumps({"frames": {}}))
    with pytest.raises(ValueError):
        godot.sprite_frames_tres(empty, "res://s.png", tmp_path / "x.tres")
    with pytest.raises(ValueError):
        godot.sprite_frames_tres(empty, "res://s.png", tmp_path / "x.tres", fps=0)


_GDScript = '''extends SceneTree

func _initialize() -> void:
\tvar res: SpriteFrames = load("res://frames.tres")
\tif res == null:
\t\tprint("LOAD_FAILED")
\t\tquit(1)
\t\treturn
\tprint("ANIMS ", res.get_animation_names())
\tprint("COUNT ", res.get_frame_count("default"))
\tprint("LOOP ", res.get_animation_loop("default"))
\tprint("SPEED ", res.get_animation_speed("default"))
\tfor i in range(res.get_frame_count("default")):
\t\tvar t: AtlasTexture = res.get_frame_texture("default", i)
\t\tprint("FRAME ", i, " region=", t.region, " margin=", t.margin)
\t\tprint("CANVAS ", i, " ", t.get_width(), "x", t.get_height())
\tquit(0)
'''


@pytest.mark.skipif(not GODOT, reason="godot not installed")
def test_godot_loads_what_we_wrote(atlas_file, tmp_path):
    """The claim that matters: the engine reads the file and keeps the geometry."""
    (tmp_path / "project.godot").write_text(
        'config_version=5\n\n[application]\nconfig/name="frames-check"\n')
    (tmp_path / "check.gd").write_text(_GDScript)
    # the atlas fixture already lives in tmp_path as sheet.json
    # any PNG will do: Godot imports it, we only inspect region/margin
    with open(tmp_path / "sheet.png", "wb") as fh:
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGBA", (12, 8), (0, 0, 0, 0)).save(buf, "PNG")
        fh.write(buf.getvalue())
    godot.sprite_frames_tres(tmp_path / "sheet.json", "res://sheet.png",
                             tmp_path / "frames.tres", fps=12)

    proc = subprocess.run([GODOT, "--headless", "--import", "--path", str(tmp_path)],
                          capture_output=True, text=True, timeout=300)
    proc = subprocess.run([GODOT, "--headless", "--path", str(tmp_path), "-s", "check.gd"],
                          capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    assert "LOAD_FAILED" not in out, out
    assert 'ANIMS ["default"]' in out, out
    assert "COUNT 2" in out, out
    assert "LOOP true" in out, out
    assert "SPEED 12" in out, out
    # Godot prints Rect2 as "[P: (x, y), S: (w, h)]"; assert the numbers, not the format
    import re
    rows = re.findall(
        r"FRAME (\d+) region=\[P: \(([-\d.]+), ([-\d.]+)\), S: \(([-\d.]+), ([-\d.]+)\)\]"
        r" margin=\[P: \(([-\d.]+), ([-\d.]+)\), S: \(([-\d.]+), ([-\d.]+)\)\]", out)
    assert len(rows) == 2, out
    nums = [[float(v) for v in row[1:]] for row in rows]
    assert nums[0][0:4] == [0.0, 0.0, 8.0, 8.0]        # untrimmed: full canvas
    assert nums[0][4:8] == [0.0, 0.0, 0.0, 0.0]        # ...and no margin
    assert nums[1][0:4] == [8.0, 0.0, 4.0, 6.0]        # trimmed: the atlas rect
    assert nums[1][4:8] == [2.0, 1.0, 4.0, 2.0]        # ...plus the trimmed-off padding
    # The claim that actually matters, checked against the engine rather than my
    # arithmetic: both frames must present the ORIGINAL 8x8 canvas, so a trimmed
    # animation does not shift or resize. AtlasTexture draws the region at
    # margin.position inside a canvas of region.size + margin.size.
    canvases = re.findall(r"CANVAS (\d+) (\d+)x(\d+)", out)
    assert canvases == [("0", "8", "8"), ("1", "8", "8")], out
