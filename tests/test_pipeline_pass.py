"""End-to-end: the Aseprite finishing pass applied by make()/prepare().

These run the real pipeline function with a stubbed artist, so no model calls or
credits are spent, and assert the produced sprite actually went through the pass.
The "applied" case needs Aseprite; the "absent" case does not (it fakes absence).
"""
import json
from pathlib import Path

import pytest
from PIL import Image

from forge import artist, aseprite_ops, cli, library, qa
from forge.sprite import KEY_CHARS, Sprite

pytestmark = pytest.mark.skipif(not aseprite_ops.available(), reason="Aseprite not installed")


def many_colours(n: int = 8, colours: int = 12) -> Sprite:
    """An n x n sprite with `colours` distinct colours (so quantisation is visible)."""
    s = Sprite(n, n, {})
    pal = {}
    for y in range(n):
        for x in range(n):
            b = (y * n + x) % colours
            pal[KEY_CHARS[b]] = "#%02x%02x%02x" % ((b * 20) % 256, (b * 7) % 256, (b * 13) % 256)
            s.set(x, y, KEY_CHARS[b])
    s.palette = pal
    return s


def stub_artist(monkeypatch, sprite: Sprite):
    """Replace the model backend and generation with a fixed sprite."""
    class FakeBackend:
        name = "fake"
        model = None

    def fake_generate(req, backend, work, log):
        work.mkdir(parents=True, exist_ok=True)
        return [artist.Step(kind="draft", program={}, sprite=sprite, errors=[], qa=qa.check(sprite))]

    monkeypatch.setattr(artist, "make_backend", lambda *a, **k: FakeBackend())
    monkeypatch.setattr(artist, "generate", fake_generate)


def colours_of(png: Path) -> int:
    return len(Sprite.from_image(Image.open(png)).used_keys())


def test_make_applies_the_finishing_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    stub_artist(monkeypatch, many_colours(colours=12))
    logs = []

    result = cli.make("pass smoke", width=8, height=8, artist_name="fake",
                      aseprite_pass={"op": "quantize", "colors": 4}, log=logs.append)

    assert colours_of(Path(result["png"])) <= 4
    assert (Path(result["png"]).parent / "work" / "pre_pass.png").exists()
    meta = json.loads((Path(result["png"]).parent / "meta.json").read_text())
    assert meta["aseprite_pass"]["op"] == "quantize"
    assert any("applied" in m for m in logs)


def test_make_pass_is_not_fatal_without_aseprite(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(aseprite_ops.aseprite, "find_executable", lambda: None)
    stub_artist(monkeypatch, many_colours(colours=12))
    logs = []

    result = cli.make("no aseprite smoke", width=8, height=8, artist_name="fake",
                      aseprite_pass={"op": "quantize", "colors": 4}, log=logs.append)

    # The sprite is kept as drawn and the reason is logged.
    assert colours_of(Path(result["png"])) > 4
    assert any("skipped" in m for m in logs)


def test_make_without_a_pass_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    stub_artist(monkeypatch, many_colours(colours=12))

    result = cli.make("plain smoke", width=8, height=8, artist_name="fake")
    assert colours_of(Path(result["png"])) == 12
    meta = json.loads((Path(result["png"]).parent / "meta.json").read_text())
    assert meta["aseprite_pass"] is None
