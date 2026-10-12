"""Aseprite-backed animation (forge/animation.py).

Skipped when Aseprite is not installed, like the other real-Aseprite tests.
"""
import json
from pathlib import Path

import pytest

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
