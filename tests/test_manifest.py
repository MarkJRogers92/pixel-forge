"""The explicit asset manifest (forge/manifest.py).

Covers: the capable-backend table matching what the code actually sends, the
refusal to simulate unsupported controls or unwired PixelLab operations, and the
export round trip (canvas size, frame order, anchor, tight alignment).
"""
import base64
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from forge import manifest, routes
from forge import pixellab_style as style
from forge.sprite import Sprite


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def marker_frame(marker_x: int, w: int = 16, h: int = 16) -> Sprite:
    """A small sprite whose single marker pixel sits in a known column."""
    s = Sprite(w, h, {"k": "#101018", "n": "#ff00aa"})
    for y in range(h - 6, h - 2):
        for x in range(2, w - 2):
            s.set(x, y, "k")
    s.set(marker_x, 2, "n")
    return s


def small_png(color=(200, 90, 40, 255), size=(24, 24)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# --------------------------------------------------------------------------- #
# the capability table must not drift from the code
# --------------------------------------------------------------------------- #
def test_capability_table_matches_what_each_backend_sends(monkeypatch, tmp_path):
    sent = {}

    def capture(url, headers, body, timeout=180):
        sent["url"], sent["body"] = url, body
        # one shape that satisfies both routes' response readers
        return {"image": {"base64": b64(small_png())},
                "base64_images": [b64(small_png())]}

    monkeypatch.setattr(routes, "api_key", lambda var: "test-key")
    monkeypatch.setattr(routes, "_post_json", capture)

    routes.pixellab("a crate", 32, 32, None)
    assert set(sent["body"]) <= set(manifest.BACKENDS["pixellab-pixflux"]["parameters"]), sent["body"]
    assert sent["url"] == manifest.BACKENDS["pixellab-pixflux"]["endpoint"]

    routes.retro_diffusion("a crate", 32, 32, None)
    assert set(sent["body"]) <= set(manifest.BACKENDS["rd-rd_fast"]["parameters"]), sent["body"]
    assert sent["url"] == manifest.BACKENDS["rd-rd_fast"]["endpoint"]

    # BitForge payloads are built (not sent) by build_payloads.
    profile_root = tmp_path / "profiles"
    (profile_root / "refs").mkdir(parents=True)
    (profile_root / "refs" / "style.png").write_bytes(small_png())
    monkeypatch.setattr(style.profiles, "ROOT", profile_root)
    profile = {"id": "demo", "style": "neon", "notes": "clean", "references": ["refs/style.png"],
               "palette": {"a": "#000000", "b": "#ff00aa"}}
    baseline, guided, _ = style.build_payloads("a crate", 32, 32, profile, seed=3)
    for payload in (baseline, guided):
        assert set(payload) <= set(manifest.BACKENDS["pixellab-bitforge"]["parameters"]), payload


def test_generation_flags_unsupported_controls_and_never_sends_them():
    record = manifest.generation_record("pixellab-pixflux", parameters={
        "description": "a crate", "image_size": {"width": 32, "height": 32},
        "direction": "south", "noise": 3})
    assert record["supported"] is True
    assert set(record["parameters"]) == {"description", "image_size"}
    flagged = {u["name"] for u in record["unsupported"]}
    assert flagged == {"direction", "noise"}
    assert all("simulated" in u["reason"] or "not a control" in u["reason"] for u in record["unsupported"])
    # the direction control is ours, not pixflux's: it is flagged, not relabelled
    assert next(u for u in record["unsupported"] if u["name"] == "direction")["reason"].startswith("documented PixelLab")


def test_generation_refuses_an_unwired_pixellab_operation():
    record = manifest.generation_record("pixellab-pixflux", operation="inpaint", parameters={"description": "x"})
    assert record["supported"] is False
    assert record["parameters"] == {} and record["endpoint"] is None
    assert "no Pixel Forge backend is wired" in record["reason"]
    assert record["reference"] == manifest.PIXELLAB_API_MAP
    assert record["unsupported"][0]["documented_endpoint"] == "POST /v2/inpaint"
    with pytest.raises(manifest.ManifestError):
        manifest.generation_record("pixellab-pixflux", operation="not-a-real-operation")
    with pytest.raises(manifest.ManifestError):
        manifest.generation_record("nope")
    # every documented operation carries an endpoint and a bound
    assert all(o["endpoint"].startswith("POST /v2/") and o["bound"]
               for o in manifest.PIXELLAB_DOCUMENTED_OPERATIONS.values())


def test_pixflux_endpoint_gap_between_the_code_and_the_public_map_is_recorded():
    record = manifest.generation_record("pixellab-pixflux")
    assert record["endpoint"] == "https://api.pixellab.ai/v1/generate-image-pixflux"
    assert record["documented_endpoint"] == "POST /v2/create-image-pixflux"
    assert record["endpoint"] != record["documented_endpoint"]      # recorded, not hidden


# --------------------------------------------------------------------------- #
# export round trip
# --------------------------------------------------------------------------- #
def test_export_round_trip_keeps_canvas_size_and_frame_order(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, "sha256_file", manifest.sha256_file)  # no-op, keeps clarity
    ref = tmp_path / "ref.png"
    ref.write_bytes(small_png())
    frames = {"f0": marker_frame(3), "f1": marker_frame(7), "f2": marker_frame(11)}
    folder = tmp_path / "asset"
    m = manifest.export_manifest(folder, frames["f0"], frames=frames,
                                 reference=manifest.reference_record(ref),
                                 animation={"name": "clip", "fps": 12.0, "loop": "forward"})
    assert m["canvas"]["width"] == 16 and m["canvas"]["height"] == 16
    assert m["reference"]["pixel_size"] == [24, 24]
    report = manifest.verify_export(folder)
    assert report["ok"], report["errors"]
    assert report["frame_order"] == ["f0", "f1", "f2"]
    assert all(c["ok"] for c in report["checks"].values())

    # the recovered frames are byte-identical to the exported ones, in order
    recovered = manifest.read_sheet(folder)
    for fid, sprite in frames.items():
        assert np.array_equal(np.asarray(recovered[fid]), sprite.to_rgba())
        assert np.array_equal(np.asarray(recovered[fid]), np.asarray(Image.open(folder / "frames" / f"{fid}.png").convert("RGBA")))

    # ordering is load-bearing: swapping two cells breaks the round trip
    record_path = folder / "sheet.json"
    record = json.loads(record_path.read_text())
    record["frames"]["f0"], record["frames"]["f1"] = record["frames"]["f1"], record["frames"]["f0"]
    record_path.write_text(json.dumps(record))
    broken = manifest.verify_export(folder)
    assert broken["ok"] is False
    assert "frame_order_round_trip" in " ".join(broken["errors"])


def test_anchor_survives_export_for_every_preset(tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(small_png())
    sprite = marker_frame(3)
    for preset in manifest.ANCHOR_PRESETS:
        folder = tmp_path / preset
        m = manifest.export_manifest(folder, sprite, reference=manifest.reference_record(ref),
                                     anchor=preset)
        x, y = manifest.anchor_pixel(preset, 16, 16)
        assert (m["canvas"]["anchor"]["x"], m["canvas"]["anchor"]["y"]) == (x, y)
        report = manifest.verify_export(folder)
        assert report["ok"], (preset, report["errors"])
        assert report["checks"]["anchor_survives"]["ok"]


def test_tight_alignment_records_the_offset_that_restores_registration(tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(small_png())
    sprite = Sprite(16, 16, {"k": "#101018"})
    for y in range(5, 9):
        for x in range(4, 10):
            sprite.set(x, y, "k")
    folder = tmp_path / "tight"
    m = manifest.export_manifest(folder, sprite, reference=manifest.reference_record(ref),
                                alignment="tight")
    assert m["canvas"]["content_bbox"] == [4, 5, 9, 8]
    with Image.open(folder / "sprite.png") as im:
        assert im.size == (6, 4)                      # trimmed
    doc = json.loads((folder / "sprite.json").read_text())
    assert [doc["width"], doc["height"]] == [6, 4]
    report = manifest.verify_export(folder)
    assert report["ok"], report["errors"]
    for name in ("tight_alignment_round_trip", "content_bbox_covers_content",
                 "canvas_size_consistent", "anchor_survives"):
        assert report["checks"][name]["ok"], name
    # the canvas size is still the manifest's, and the anchor still points at the ground pixel
    assert [m["canvas"]["width"], m["canvas"]["height"]] == [16, 16]
    assert (m["canvas"]["anchor"]["x"], m["canvas"]["anchor"]["y"]) == (8, 15)


def test_export_uses_the_existing_item_shape_and_can_attach_to_a_library_meta(tmp_path):
    folder = tmp_path / "asset"
    sprite = marker_frame(3)
    meta = {"kind": "generate", "prompt": "a crate", "size": [16, 16], "backend": "pixellab"}
    manifest.export_manifest(folder, sprite, prompt="a crate", profile="dead-mall")
    doc = json.loads((folder / "sprite.json").read_text())
    assert set(doc) == {"width", "height", "palette", "rows"}
    written_meta = json.loads((folder / "meta.json").read_text())
    assert written_meta["kind"] == "manifest" and written_meta["size"] == [16, 16]
    merged = manifest.attach_manifest(meta, json.loads((folder / "manifest.json").read_text()))
    assert merged["backend"] == "pixellab"                       # untouched
    assert merged["manifest"] == {"schema": manifest.SCHEMA, "file": "manifest.json"}


def test_canvas_outside_the_operation_bound_is_flagged_not_resized(tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(small_png())
    generation = manifest.generation_record("pixellab-pixflux")
    folder = tmp_path / "asset"
    m = manifest.export_manifest(folder, marker_frame(3), reference=manifest.reference_record(ref),
                                 generation=generation)
    assert m["generation"]["canvas_fits_operation_bound"] is False
    assert "outside this operation's documented bound" in m["generation"]["notes"][0]
    assert m["canvas"]["width"] == 16                            # not silently bumped to 32


def test_state_and_animation_records_point_at_exported_frames(tmp_path):
    frames = {"f0": marker_frame(3), "f1": marker_frame(7)}
    folder = tmp_path / "asset"
    m = manifest.export_manifest(folder, frames["f0"], frames=frames, direction="east",
                                 animation={"name": "glint", "fps": 8.0, "loop": "pingpong"})
    assert m["states"]["idle"]["east"] == "frames/f0.png"
    assert (folder / m["states"]["idle"]["east"]).is_file()
    anim = m["animations"]["glint"]
    assert anim["fps"] == 8.0 and anim["loop"] == "pingpong"
    assert [f["id"] for f in anim["frames"]] == ["f0", "f1"]
    assert anim["per_direction"]["east"] == ["f0", "f1"]
    assert all((folder / f["path"]).is_file() for f in anim["frames"])


def test_bad_inputs_are_rejected():
    with pytest.raises(manifest.ManifestError):
        manifest.export_manifest("/tmp/never-used-pf", "not a sprite")
    s = Sprite(8, 8, {"k": "#000000"})
    with pytest.raises(manifest.ManifestError):
        manifest.build_manifest(s, frames={"f0": s, "f1": Sprite(8, 9, {"k": "#000000"})})
    with pytest.raises(manifest.ManifestError):
        manifest.build_manifest(s, anchor="middle-ish")
    with pytest.raises(manifest.ManifestError):
        manifest.build_manifest(s, alignment="centre")
    with pytest.raises(manifest.ManifestError):
        manifest.build_manifest(s, direction="west", directions=["south"])
