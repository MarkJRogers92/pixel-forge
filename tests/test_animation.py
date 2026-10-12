"""Aseprite-backed animation (forge/animation.py).

Skipped when Aseprite is not installed, like the other real-Aseprite tests.
"""
import json
from pathlib import Path

import pytest
from PIL import Image

from forge import animation, aseprite_ops
from forge.sprite import Sprite

pytestmark = pytest.mark.skipif(not aseprite_ops.available(), reason="Aseprite not installed")


def block(color: str, n: int = 8) -> Sprite:
    s = Sprite(n, n, {"c": color})
    s.grid = [["." for _ in range(n)] for _ in range(n)]
    for y in range(2, 6):
        for x in range(2, 6):
            s.set(x, y, "c")
    return s


def test_animate_writes_sheet_data_and_gif(tmp_path):
    frames = [block("#c03040"), block("#30a040"), block("#3040c0")]
    res = animation.animate(frames, tmp_path, tag="glint", fps=8, loop="pingpong")

    assert res["frames"] == 3 and res["size"] == [8, 8]
    assert res["duration"] == pytest.approx(0.125)
    assert Path(res["sheet"]).exists()
    assert Path(res["gif"]).exists()

    data = json.loads(Path(res["data"]).read_text())
    assert len(data["frames"]) == 3
    assert {f["duration"] for f in data["frames"].values()} == {125}
    assert Path(res["gif"]).read_bytes()[:6] in (b"GIF89a", b"GIF87a")


def test_animate_can_save_a_project_for_editing(tmp_path):
    res = animation.animate([block("#101010"), block("#eeeeee")], tmp_path,
                            project_name="anim.aseprite")
    assert Path(res["project"]).exists()
    assert Path(res["project"]).read_bytes()[:4] == b"\x89PNG" or Path(res["project"]).suffix == ".aseprite"


def test_animate_rejects_mismatched_frames(tmp_path):
    with pytest.raises(ValueError):
        animation.animate([block("#000000", 8), block("#ffffff", 6)], tmp_path)


def test_animate_rejects_bad_options(tmp_path):
    with pytest.raises(ValueError):
        animation.animate([block("#000000")], tmp_path, fps=0)
    with pytest.raises(ValueError):
        animation.animate([block("#000000")], tmp_path, loop="sideways")
    with pytest.raises(ValueError):
        animation.animate([], tmp_path)


def test_single_frame_animation(tmp_path):
    res = animation.animate([block("#123456")], tmp_path, gif_name=None, data_name=None)
    assert res["frames"] == 1
    assert "gif" not in res
    assert Path(res["sheet"]).exists()


def test_animate_writes_a_phaser_descriptor(tmp_path):
    res = animation.animate([block("#c03040"), block("#30a040"), block("#3040c0")],
                            tmp_path, tag="flicker", fps=12, loop="pingpong", game="phaser")
    desc = json.loads(Path(res["descriptor"]).read_text())
    assert desc["engine"] == "phaser"
    assert desc["sheetType"] == "horizontal"
    assert (desc["frameWidth"], desc["frameHeight"], desc["frameCount"]) == (8, 8, 3)
    assert desc["loop"] == "pingpong"
    assert [f["x"] for f in desc["frames"]] == [0, 8, 16]
    assert desc["frames"][0]["durationMs"] == 83
    assert desc["phaser"]["createAnim"].endswith("yoyo: true })")


def test_animate_rejects_an_unknown_game(tmp_path):
    with pytest.raises(ValueError):
        animation.animate([block("#000000")], tmp_path, game="unity")


def test_animate_is_unchanged_when_no_sheet_options_are_given(tmp_path):
    res = animation.animate([block("#c03040"), block("#30a040")], tmp_path)
    assert res["sheet_options"] == {}


def test_animate_rejects_the_broken_grid_sheet_type(tmp_path):
    # Aseprite's "grid" layout reads the sprite's grid, which this flow never
    # creates: every frame lands at (0,0) and the atlas is unusable.
    with pytest.raises(ValueError, match="grid"):
        animation.animate([block("#000000"), block("#ffffff")], tmp_path, sheet_type="grid")


def test_animate_rejects_bad_padding(tmp_path):
    for kwargs in ({"shape_padding": -1}, {"border_padding": True}, {"inner_padding": "2"}):
        with pytest.raises(ValueError):
            animation.animate([block("#000000")], tmp_path, **kwargs)


def test_trim_shrinks_the_sheet_and_records_the_offset(tmp_path):
    frames = [block("#c03040"), block("#30a040"), block("#3040c0")]
    plain = animation.animate(frames, tmp_path / "plain", sheet_type="packed")
    trimmed = animation.animate(frames, tmp_path / "trim", sheet_type="packed", trim=True)

    with Image.open(plain["sheet"]) as im:
        plain_area = im.width * im.height
    with Image.open(trimmed["sheet"]) as im:
        trim_area = im.width * im.height
    # block() draws 4x4 of content inside an 8x8 frame
    assert trim_area < plain_area

    data = json.loads(Path(trimmed["data"]).read_text())
    entry = next(iter(data["frames"].values()))
    assert entry["trimmed"] is True
    assert entry["spriteSourceSize"] == {"x": 2, "y": 2, "w": 4, "h": 4}
    assert entry["sourceSize"] == {"w": 8, "h": 8}


def test_trim_plus_extrude_costs_area_but_stays_smaller(tmp_path):
    frames = [block("#c03040"), block("#30a040"), block("#3040c0")]
    trimmed = animation.animate(frames, tmp_path / "t", sheet_type="packed", trim=True)
    padded = animation.animate(frames, tmp_path / "p", sheet_type="packed", trim=True,
                               extrude=True, shape_padding=2, border_padding=2)
    with Image.open(trimmed["sheet"]) as im:
        trim_area = im.width * im.height
    with Image.open(padded["sheet"]) as im:
        pad_area = im.width * im.height
    assert trim_area < pad_area
    assert padded["sheet_options"] == {"trim": True, "extrude": True,
                                       "shapePadding": 2, "borderPadding": 2}


def test_power_of_two_uses_the_real_field_name(tmp_path):
    # "powerOfTwo" is silently ignored by Aseprite; "powerOfTwoSize" is the key.
    res = animation.animate([block("#c03040"), block("#30a040"), block("#3040c0")],
                            tmp_path, sheet_type="packed", power_of_two=True)
    assert res["sheet_options"] == {"powerOfTwoSize": True}
    with Image.open(res["sheet"]) as im:
        for size in (im.width, im.height):
            assert size & (size - 1) == 0, f"{size} is not a power of two"


def test_trimmed_phaser_descriptor_keeps_the_per_frame_rects(tmp_path):
    res = animation.animate([block("#c03040"), block("#30a040"), block("#3040c0")],
                            tmp_path, sheet_type="packed", trim=True, game="phaser")
    desc = json.loads(Path(res["descriptor"]).read_text())
    assert desc["atlasTrimmed"] is True
    assert (desc["frameWidth"], desc["frameHeight"]) == (8, 8)  # untrimmed canvas
    assert all(f["trimmed"] for f in desc["frames"])
    assert desc["frames"][0]["spriteSourceSize"]["w"] == 4
    # the uniform-strip snippet would load the wrong pixels once frames differ
    assert "f.x, f.y, f.w, f.h" in desc["phaser"]["addFrames"]
    assert "trimmedNote" in desc["phaser"]
    assert desc["phaser"]["createAnim"].endswith("yoyo: false })")


def test_untrimmed_phaser_descriptor_keeps_the_uniform_snippet(tmp_path):
    res = animation.animate([block("#c03040"), block("#30a040")], tmp_path, game="phaser")
    desc = json.loads(Path(res["descriptor"]).read_text())
    assert desc["atlasTrimmed"] is False
    assert "i * frameWidth, 0, frameWidth, frameHeight" in desc["phaser"]["addFrames"]
    assert "trimmedNote" not in desc["phaser"]


def test_atlas_utilisation_reports_texture_usage(tmp_path):
    """TexturePacker's "texture usage": the share of the sheet that is sprite."""
    res = animation.animate([block("#c03040"), block("#30a040")], tmp_path / "u",
                            sheet_type="packed", trim=True)
    # 4x4 of content per frame in an 8x8 canvas; packed+trim tiles it exactly
    assert 0.0 < res["atlas_utilisation"] <= 1.0
    # the same number computed by hand from the atlas we wrote
    data = json.loads(Path(res["data"]).read_text())
    frames = list(data["frames"].values())
    size = data["meta"]["size"]
    expected = sum(f["frame"]["w"] * f["frame"]["h"] for f in frames) / (size["w"] * size["h"])
    assert res["atlas_utilisation"] == pytest.approx(expected, abs=1e-4)
    # and it is absent when no atlas is written
    assert "atlas_utilisation" not in animation.animate(
        [block("#000000")], tmp_path / "n", data_name=None, gif_name=None)
