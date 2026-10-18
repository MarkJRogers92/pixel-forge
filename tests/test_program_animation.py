import json

import pytest
from PIL import Image

from forge import animation, cli, dsl
from forge.sprite import Sprite
from forge.cleanup import load_image


PALETTE = {"k": "#111111", "b": "#336699", "w": "#ffffff"}


def test_part_rows_preserve_skip_erase_and_clip_edges():
    program = {
        "width": 4, "height": 3, "palette": PALETTE,
        "parts": {"lamp": ["k.b", "b~ "]},
        "ops": [
            {"op": "rect", "x": 0, "y": 0, "w": 4, "h": 3, "c": "w"},
            {"op": "part", "name": "lamp", "x": 2, "y": 0},
            {"op": "part", "name": "lamp", "x": -1, "y": 0},
        ],
    }
    result = dsl.render(program)
    assert result.errors == []
    assert result.sprite.rows() == ["wbkw", ".wb.", "wwww"]


def test_part_missing_or_malformed_rows_are_collected_as_op_errors():
    for parts, name in [({}, "lamp"), ({"lamp": ["kk", "b"]}, "lamp"),
                        ({"lamp": ["kx"]}, "lamp")]:
        result = dsl.render({
            "width": 2, "height": 1, "palette": PALETTE,
            "parts": parts, "ops": [{"op": "part", "name": name}],
        })
        assert len(result.errors) == 1
        assert result.errors[0].startswith("op[0] part:")


def test_render_frames_use_independent_copies_and_keep_common_pixels():
    base = Sprite(3, 2, PALETTE, [["k", ".", "."], [".", ".", "."]])
    original_grid = [row[:] for row in base.grid]
    program = {
        "width": 3, "height": 2, "palette": PALETTE, "base": "current",
        "ops": [{"op": "pixels", "x": 1, "y": 0, "rows": ["b"]}],
        "frames": [[], [{"op": "pixels", "x": 1, "y": 0, "rows": ["w"]}], []],
    }
    frames = animation.render_frames(program, base)
    assert [f.rows() for f in frames] == [["kb.", "..."], ["kw.", "..."], ["kb.", "..."]]
    assert base.grid == original_grid
    assert program["ops"] == [{"op": "pixels", "x": 1, "y": 0, "rows": ["b"]}]
    assert program["frames"][0] == []
    frames[0].set(2, 1, "w")
    assert frames[1].get(2, 1) == "."


def test_render_frames_reject_bad_frames_and_render_errors():
    for program in (
        {"width": 1, "height": 1, "frames": []},
        {"width": 1, "height": 1, "frames": [None]},
        {"width": 1, "height": 1, "ops": [{"op": "wat"}], "frames": [[]]},
        {"width": 1, "height": 1, "frames": [[{"op": "wat"}]]},
    ):
        with pytest.raises(ValueError):
            animation.render_frames(program)


def test_render_frames_reject_palette_redefinition_that_recolors_base():
    base_path = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
    base = Sprite.from_image(load_image(_png_bytes(base_path)))
    assert base.get(0, 0) == "a"
    program = {
        "width": 1, "height": 1, "palette": {"a": "#0000ff"},
        "base": "current", "frames": [[]],
    }
    with pytest.raises(ValueError, match="palette key 'a'.*conflicts"):
        animation.render_frames(program, base)
    assert base.palette["a"] == "#ff0000"
    equivalent = {**program, "palette": {"a": "#f00"}}
    rendered = animation.render_frames(equivalent, base)
    assert tuple(rendered[0].to_rgba()[0, 0]) == (255, 0, 0, 255)


def test_animate_program_cli_forwards_rendered_frames_to_exporter(tmp_path, monkeypatch):
    program_path = tmp_path / "program.json"
    program_path.write_text(json.dumps({
        "width": 2, "height": 1, "palette": PALETTE,
        "ops": [{"op": "pixels", "rows": ["kk"]}],
        "frames": [[], [{"op": "pixels", "x": 1, "y": 0, "rows": ["w"]}]],
    }))
    out = tmp_path / "clip"
    captured = {}

    def export(frames, output, **kwargs):
        captured["frames"] = frames
        captured["output"] = output
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(animation, "animate", export)
    cli.main(["animate", "--program", str(program_path), "-o", str(out), "--fps", "8"])
    assert [f.rows() for f in captured["frames"]] == [["kk"], ["kw"]]
    assert captured["output"] == str(out)
    assert captured["kwargs"]["fps"] == 8


def test_program_cli_rejects_mixed_sources_and_ignored_base_before_export(tmp_path, monkeypatch):
    program_path = tmp_path / "program.json"
    program_path.write_text(json.dumps({"width": 1, "height": 1, "frames": [[]]}))
    base_path = tmp_path / "base.png"
    Sprite(1, 1, PALETTE).save(base_path)
    calls = []
    monkeypatch.setattr(animation, "animate", lambda *args, **kwargs: calls.append(args))
    out = tmp_path / "clip"

    with pytest.raises(SystemExit, match="cannot be combined"):
        cli.main(["animate", "--program", str(program_path), "frame.png", "-o", str(out)])
    assert not out.exists()
    with pytest.raises(SystemExit, match="base.*current"):
        cli.main(["animate", "--program", str(program_path), "--base", str(base_path),
                  "-o", str(out)])
    assert not out.exists()
    assert calls == []


def test_program_cli_applies_explicit_current_base_and_rejects_bad_frames(tmp_path, monkeypatch):
    base_path = tmp_path / "approved.png"
    Sprite(2, 1, PALETTE, [["b", "b"]]).save(base_path)
    program_path = tmp_path / "program.json"
    program_path.write_text(json.dumps({
        "width": 2, "height": 1, "palette": PALETTE, "base": "current",
        "ops": [{"op": "pixels", "x": 1, "rows": ["w"]}], "frames": [[]],
    }))
    captured = []
    monkeypatch.setattr(animation, "animate", lambda frames, *args, **kwargs: captured.extend(frames))
    cli.main(["animate", "--program", str(program_path), "--base", str(base_path),
              "-o", str(tmp_path / "valid")])
    assert len(captured) == 1
    assert captured[0].palette[captured[0].get(0, 0)] == PALETTE["b"]
    assert captured[0].palette[captured[0].get(1, 0)] == PALETTE["w"]

    program_path.write_text(json.dumps({"width": 1, "height": 1, "frames": [None]}))
    out = tmp_path / "invalid"
    with pytest.raises(SystemExit, match="each animation frame"):
        cli.main(["animate", "--program", str(program_path), "-o", str(out)])
    assert not out.exists()
    assert len(captured) == 1


def test_program_cli_rejects_partial_alpha_base_before_export(tmp_path, monkeypatch):
    base_path = tmp_path / "soft-alpha.png"
    Image.new("RGBA", (3, 1), (255, 0, 0, 255)).save(base_path)
    with Image.open(base_path) as image:
        image.putpixel((0, 0), (0, 0, 0, 0))
        image.putpixel((2, 0), (0, 255, 0, 128))
        image.save(base_path)
    program_path = tmp_path / "program.json"
    program_path.write_text(json.dumps({
        "width": 3, "height": 1, "palette": {}, "base": "current", "frames": [[]],
    }))
    calls = []
    monkeypatch.setattr(animation, "animate", lambda *args, **kwargs: calls.append(args))
    out = tmp_path / "clip"
    with pytest.raises(SystemExit, match="binary alpha"):
        cli.main(["animate", "--program", str(program_path), "--base", str(base_path),
                  "-o", str(out)])
    assert not out.exists()
    assert calls == []


def _png_bytes(image):
    import io

    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()
