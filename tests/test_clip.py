"""Ordered PNG clips retain animation motion, alpha, and one shared palette."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def frame(tmp_path, name, *, xy=(3, 5), size=(12, 10), color=(230, 25, 80, 255)):
    image = Image.new("RGBA", size)
    for y in range(xy[1], min(xy[1] + 2, size[1])):
        for x in range(xy[0], min(xy[0] + 2, size[0])):
            image.putpixel((x, y), color)
    path = tmp_path / name
    image.save(path)
    return path


def pixels(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"))


def run(paths, out, **kwargs):
    # Import in the test body so the red phase fails on the missing feature.
    from forge.clip import import_clip
    return import_clip(paths, out, **kwargs)


def test_keeps_explicit_order_count_duplicates_source_hashes_and_canvas(tmp_path):
    a = frame(tmp_path, "z.png")
    b = frame(tmp_path, "a.png", xy=(6, 3))
    sources = [b, a, b]
    before = [p.read_bytes() for p in sources]
    out = tmp_path / "clip"
    report = run(sources, out, fps=12, loop="pingpong", tag="recoil")
    assert report["frame_count"] == 3
    assert report["verified"]["ok"]
    assert report["verified"]["frame_order"] == ["f0", "f1", "f2"]
    assert report["transform"]["source_canvas"] == [12, 10]
    assert report["transform"]["target_canvas"] == [12, 10]
    assert report["transform"]["offset"] == [0, 0]
    for i, (source, data) in enumerate(zip(sources, before)):
        assert np.array_equal(pixels(out / "frames" / f"f{i}.png"), pixels(source))
        assert report["sources"][i]["sha256"] == hashlib.sha256(data).hexdigest()
        assert source.read_bytes() == data
    exported = json.loads((out / "manifest.json").read_text())
    assert exported["generation"] is None
    assert exported["canvas"]["alignment"] == "canvas"
    anim = exported["animations"]["recoil"]
    assert anim["fps"] == 12 and anim["loop"] == "pingpong"
    assert [f["id"] for f in anim["frames"]] == ["f0", "f1", "f2"]
    assert np.array_equal(pixels(out / "sprite.png"), pixels(b))
    saved = json.loads((out / "clip-report.json").read_text())
    assert saved == report


def test_shared_canvas_transform_keeps_jump_and_recoil_displacement(tmp_path):
    a = frame(tmp_path, "rest.png", xy=(3, 6))
    b = frame(tmp_path, "jump.png", xy=(5, 2))
    out = tmp_path / "scaled"
    report = run([a, b], out, width=36, height=20)
    transform = report["transform"]
    assert transform["resized_canvas"] == [24, 20]
    assert transform["offset"] == [6, 0]
    assert transform["resampling"] == "nearest"
    # Full-canvas fit: both the vertical jump and horizontal recoil survive.
    assert report["frames"][0]["output_bbox"] == [12, 12, 15, 15]
    assert report["frames"][1]["output_bbox"] == [16, 4, 19, 7]
    for i, path in enumerate([a, b]):
        expected = Image.new("RGBA", (36, 20))
        with Image.open(path) as source:
            expected.paste(source.resize((24, 20), Image.Resampling.NEAREST), (6, 0))
        assert np.array_equal(pixels(out / "frames" / f"f{i}.png"), np.asarray(expected))


@pytest.mark.parametrize("size_args,expected", [({"width": 24}, [24, 20]), ({"height": 5}, [6, 5])])
def test_one_target_dimension_keeps_source_aspect(tmp_path, size_args, expected):
    report = run([frame(tmp_path, "a.png")], tmp_path / "out", **size_args)
    assert report["transform"]["target_canvas"] == expected


def test_fixed_palette_is_shared_deterministic_and_preserves_alpha(tmp_path):
    a = frame(tmp_path, "a.png", color=(246, 4, 7, 96))
    b = frame(tmp_path, "b.png", xy=(5, 2), color=(231, 15, 18, 255))
    kwargs = {"palette": ["#ff0000", "#0000ff"]}
    first = run([a, b], tmp_path / "one", **kwargs)
    second = run([a, b], tmp_path / "two", **kwargs)
    assert first["palette"] == second["palette"]
    assert first["palette"]["mode"] == "fixed"
    assert set(first["palette"]["rgba"].values()) == {"#ff000060", "#ff0000"}
    for i, source in enumerate([a, b]):
        result = pixels(tmp_path / "one" / "frames" / f"f{i}.png")
        assert np.array_equal(result[..., 3], pixels(source)[..., 3])
        assert set(map(tuple, result[result[..., 3] > 0, :3])) == {(255, 0, 0)}
        assert (tmp_path / "one" / "frames" / f"f{i}.png").read_bytes() == (
            tmp_path / "two" / "frames" / f"f{i}.png").read_bytes()


def test_default_exact_palette_preserves_soft_alpha_without_cleanup(tmp_path):
    a = frame(tmp_path, "a.png", color=(99, 31, 7, 1))
    report = run([a], tmp_path / "out")
    assert np.array_equal(pixels(a), pixels(tmp_path / "out" / "frames/f0.png"))
    assert report["palette"]["mode"] == "exact"
    assert "soft_alpha" in {w["code"] for w in report["warnings"]}


def test_existing_profile_supplies_one_palette_without_generation(tmp_path, monkeypatch):
    from forge import profiles
    folder = tmp_path / "profiles"
    folder.mkdir()
    (folder / "demo.json").write_text(json.dumps({"palette": {"p": "#ff0000"}}))
    monkeypatch.setattr(profiles, "ROOT", folder)
    a = frame(tmp_path, "a.png")
    report = run([a], tmp_path / "out", profile="demo")
    assert report["palette"]["profile"] == "demo"
    assert set(report["palette"]["rgba"].values()) == {"#ff0000"}


def test_diagnostics_warn_without_pinning_motion_or_replacing_endpoints(tmp_path):
    a = frame(tmp_path, "edge.png", xy=(0, 0))
    b = frame(tmp_path, "jump.png", xy=(5, 2))
    report = run([a, b], tmp_path / "out")
    assert {"possible_clipping", "endpoint_mismatch"} <= {w["code"] for w in report["warnings"]}
    assert report["endpoints"]["equal"] is False
    assert np.array_equal(pixels(a), pixels(tmp_path / "out" / "frames/f0.png"))
    assert np.array_equal(pixels(b), pixels(tmp_path / "out" / "frames/f1.png"))
    assert report["review"]["status"] == "awaiting_visual_review"
    with Image.open(tmp_path / "out" / "sheet.png") as sheet:
        with Image.open(tmp_path / "out" / "sheet@2x.png") as zoom:
            assert zoom.size == (sheet.width * 2, sheet.height * 2)
            assert np.array_equal(np.asarray(zoom), np.asarray(sheet.resize(zoom.size, Image.Resampling.NEAREST)))


def test_fully_opaque_background_is_warned_and_retained(tmp_path):
    path = tmp_path / "solid.png"
    Image.new("RGB", (4, 4), "white").save(path)
    report = run([path], tmp_path / "out")
    assert "opaque_frame" in {w["code"] for w in report["warnings"]}
    assert np.array_equal(pixels(path), pixels(tmp_path / "out" / "frames/f0.png"))


@pytest.mark.parametrize("kwargs", [
    {"fps": 0}, {"fps": -1}, {"fps": float("nan")}, {"fps": float("inf")},
    {"width": 0}, {"height": -1}, {"width": 1025}, {"width": 1.5}, {"height": True},
    {"loop": "unknown"}, {"palette": []}, {"palette": ["bad-color"]},
    {"palette": ["#ff000080"]}, {"palette": ["#ff0000"], "profile": "dead-mall"},
])
def test_rejects_invalid_options_before_writing(tmp_path, kwargs):
    a = frame(tmp_path, "a.png")
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        run([a], out, **kwargs)
    assert not out.exists()


def test_rejects_empty_list_empty_frame_and_mismatched_dimensions(tmp_path):
    a = frame(tmp_path, "a.png")
    empty = tmp_path / "empty.png"
    Image.new("RGBA", (12, 10)).save(empty)
    other = frame(tmp_path, "other.png", size=(10, 10))
    for sources in ([], [empty], [a, empty], [a, other]):
        with pytest.raises(ValueError):
            run(sources, tmp_path / "out")
        assert not (tmp_path / "out").exists()


def test_rejects_invalid_png_missing_path_and_animated_png(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_text("not an image")
    jpeg = tmp_path / "jpeg.png"
    Image.new("RGB", (12, 10)).save(jpeg, format="JPEG")
    animated = tmp_path / "animated.png"
    Image.new("RGBA", (12, 10), "red").save(animated, save_all=True,
        append_images=[Image.new("RGBA", (12, 10), "blue")], duration=100)
    for path in (bad, jpeg, animated, tmp_path / "missing.png", tmp_path):
        with pytest.raises(ValueError):
            run([path], tmp_path / "out")
        assert not (tmp_path / "out").exists()


def test_refuses_to_overwrite_existing_folder_or_sources(tmp_path):
    a = frame(tmp_path, "a.png")
    sentinel = tmp_path / "out"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="exists"):
        run([a], sentinel)
    assert (sentinel / "keep.txt").read_text() == "keep"
    assert sorted(p.name for p in sentinel.iterdir()) == ["keep.txt"]
    with pytest.raises(ValueError, match="exists"):
        run([a], a)


def test_rejects_too_many_exact_colors_but_fixed_palette_handles_it(tmp_path):
    path = tmp_path / "many.png"
    data = np.array([[(i, 255 - i, i * 2, 255) for i in range(63)]], dtype=np.uint8)
    Image.fromarray(data).save(path)
    with pytest.raises(ValueError, match="62"):
        run([path], tmp_path / "fail")
    assert not (tmp_path / "fail").exists()
    report = run([path], tmp_path / "out", palette=["#ff0000", "#00ff00"])
    assert len(report["palette"]["rgba"]) <= 2


def test_rejects_rgba_palette_overflow_instead_of_discarding_alpha(tmp_path):
    path = tmp_path / "alpha.png"
    data = np.array([[(255, 0, 0, i) for i in range(1, 64)]], dtype=np.uint8)
    Image.fromarray(data).save(path)
    with pytest.raises(ValueError, match="62"):
        run([path], tmp_path / "out", palette=["#ff0000"])


def test_rejects_frame_that_disappears_after_downscaling(tmp_path):
    path = frame(tmp_path, "small.png", xy=(0, 0))
    with pytest.raises(ValueError, match="empty"):
        run([path], tmp_path / "out", width=1, height=1)
    assert not (tmp_path / "out").exists()


def test_cli_runs_real_export_with_supplied_palette(tmp_path, capsys):
    from forge.cli import main
    a = frame(tmp_path, "a.png")
    b = frame(tmp_path, "b.png", xy=(5, 2))
    main(["clip", str(b), str(a), "-o", str(tmp_path / "out"),
          "-W", "24", "--fps", "12", "--loop", "reverse", "--palette", "#ff0000,#000000"])
    report = json.loads(capsys.readouterr().out)
    assert report["verified"]["ok"]
    assert report["sources"][0]["path"] == str(b)
    assert report["fps"] == 12 and report["loop"] == "reverse"


def test_rejects_strings_and_excess_frames_before_reading(tmp_path):
    from forge import clip
    for paths in ("one.png", Path("one.png"), [tmp_path / "missing.png"] * (clip.MAX_FRAMES + 1)):
        with pytest.raises(ValueError):
            clip.import_clip(paths, tmp_path / "out")
        assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("limit,value", [
    ("MAX_INPUT_BYTES", 1), ("MAX_CLIP_BYTES", 1), ("MAX_INPUT_PIXELS", 1),
    ("MAX_SOURCE_PIXELS", 1), ("MAX_OUTPUT_PIXELS", 1),
])
def test_resource_limits_reject_before_writing(tmp_path, monkeypatch, limit, value):
    from forge import clip
    a = frame(tmp_path, "a.png")
    monkeypatch.setattr(clip, limit, value)
    with pytest.raises(ValueError):
        clip.import_clip([a], tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_palette_limit_is_across_whole_clip_not_each_frame(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    Image.fromarray(np.array([[(i, 0, 0, 255) for i in range(32)]], dtype=np.uint8)).save(a)
    Image.fromarray(np.array([[(i, 0, 0, 255) for i in range(32, 64)]], dtype=np.uint8)).save(b)
    with pytest.raises(ValueError, match="62"):
        run([a, b], tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_api_needs_no_network_or_processes(tmp_path, monkeypatch):
    import socket
    import subprocess

    def blocked(*args, **kwargs):
        raise AssertionError("clip import must be local and deterministic")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    report = run([frame(tmp_path, "a.png")], tmp_path / "out")
    assert report["verified"]["ok"]


def test_cli_errors_are_actionable_without_tracebacks(tmp_path):
    from forge.cli import main
    a = frame(tmp_path, "a.png")
    with pytest.raises(SystemExit, match="fps must be positive and finite"):
        main(["clip", str(a), "-o", str(tmp_path / "out"), "--fps", "nan"])
    assert not (tmp_path / "out").exists()


def test_cli_rejects_negative_hex_channel_without_overflow(tmp_path):
    from forge.cli import main
    a = frame(tmp_path, "a.png")
    with pytest.raises(SystemExit, match="palette entries must be hex colours"):
        main(["clip", str(a), "-o", str(tmp_path / "out"), "--palette", "#-10000"])
    assert not (tmp_path / "out").exists()


def test_cli_normalizes_pillow_decompression_bomb_error(tmp_path):
    import struct
    import zlib
    from forge.cli import main
    a = frame(tmp_path, "huge.png")
    data = bytearray(a.read_bytes())
    data[16:24] = struct.pack(">II", 20000, 20000)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
    a.write_bytes(data)
    with pytest.raises(SystemExit, match="cannot read PNG frame"):
        main(["clip", str(a), "-o", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("palette", [None, [], "#ff0000", 42])
def test_cli_rejects_profile_without_palette_mapping(tmp_path, monkeypatch, palette):
    from forge import profiles
    from forge.cli import main
    folder = tmp_path / "profiles"
    folder.mkdir()
    (folder / "bad.json").write_text(json.dumps({"palette": palette}))
    monkeypatch.setattr(profiles, "ROOT", folder)
    a = frame(tmp_path, "a.png")
    with pytest.raises(SystemExit, match="profile must contain a nonempty palette mapping"):
        main(["clip", str(a), "-o", str(tmp_path / "out"), "--profile", "bad"])
    assert not (tmp_path / "out").exists()
