import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from forge import library, pixellab_style, profiles, routes


def png_bytes(color=(240, 30, 90, 255), size=(24, 18)):
    image = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def style_profile(tmp_path, monkeypatch):
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    refdir = profile_root / "refs"
    refdir.mkdir()
    (refdir / "style.png").write_bytes(png_bytes())
    monkeypatch.setattr(profiles, "ROOT", profile_root)
    profile = {
        "id": "demo", "style": "bold neon steel", "notes": "clean outline",
        "references": ["refs/style.png"],
        "palette": {"a": "#000000", "b": "#ff00aa", "c": "#ffffff"},
    }
    (profile_root / "demo.json").write_text(json.dumps(profile))
    return profile


def test_dry_run_is_safe_and_payload_matches_documented_bitforge_schema(style_profile, monkeypatch):
    def no_key(_):
        pytest.fail("dry-run must not resolve credentials")
    monkeypatch.setattr(routes, "api_key", no_key)
    report = pixellab_style.dry_run("a neon payphone", 32, 48, style_profile, seed=71)
    assert report["status"] == "dry_run"
    assert report["endpoint"] == "https://api.pixellab.ai/v2/create-image-bitforge"
    assert report["dimensions"] == {"width": 32, "height": 48}
    assert report["palette_count"] == 3
    assert report["reference_source_dimensions"] == [24, 18]
    assert report["reference_normalized_dimensions"] == [32, 48]
    assert report["provider_calls"] == 0
    assert set(report["payload_sha256"]) == {"text_baseline", "style_palette_guided"}
    baseline, guided, _ = pixellab_style.build_payloads("a neon payphone", 32, 48, style_profile, seed=71)
    assert baseline["description"] == guided["description"]
    assert "bold neon steel" in baseline["description"] and "clean outline" in baseline["description"]
    assert baseline["seed"] == guided["seed"] == 71
    assert "style_image" not in baseline and "color_image" not in baseline
    assert guided["style_image"].keys() == {"type", "base64", "format"}
    assert guided["style_image"]["type"] == "base64"
    assert guided["style_image"]["format"] == "png"
    assert guided["style_strength"] == 50
    assert guided["color_image"]["format"] == "png"
    reference = Image.open(io.BytesIO(base64.b64decode(guided["style_image"]["base64"])))
    palette = Image.open(io.BytesIO(base64.b64decode(guided["color_image"]["base64"])))
    assert reference.mode == "RGBA" and reference.size == (32, 48)
    assert reference.getpixel((0, 0))[3] == 0 and reference.getpixel((0, 24))[3] == 255
    assert palette.mode == "RGB"
    assert palette.size == (32, 48)
    assert {palette.getpixel((4, 4)), palette.getpixel((20, 4)), palette.getpixel((4, 30))} == {
        (0, 0, 0), (255, 0, 170), (255, 255, 255)}


@pytest.mark.parametrize("width,height", [(15, 32), (201, 32), (32, 0), (True, 32)])
def test_unsupported_dimensions_rejected_before_key_resolution(style_profile, monkeypatch, width, height):
    monkeypatch.setattr(routes, "api_key", lambda _: pytest.fail("invalid input must be rejected before key lookup"))
    with pytest.raises(pixellab_style.StyleTrialError, match="dimensions|whole numbers"):
        pixellab_style.run_trial("test", width, height, style_profile)


def test_missing_reference_or_palette_is_rejected_before_provider(style_profile, monkeypatch):
    monkeypatch.setattr(routes, "api_key", lambda _: pytest.fail("invalid profile must be rejected before key lookup"))
    with pytest.raises(pixellab_style.StyleTrialError, match="reference image not found"):
        pixellab_style.dry_run("test", 32, 32, {**style_profile, "references": ["missing.png"]})
    with pytest.raises(pixellab_style.StyleTrialError, match="palette"):
        pixellab_style.dry_run("test", 32, 32, {**style_profile, "palette": {}})


@pytest.mark.parametrize("count", [17, 18, 31, 32, 33, 255, 256])
def test_palette_png_contains_only_requested_colors(count):
    colors = [(i, (i * 3) % 256, (i * 7) % 256) for i in range(count)]
    encoded = pixellab_style._palette_png(colors, 16, 16)
    with Image.open(io.BytesIO(encoded)) as image:
        assert image.size == (16, 16)
        assert {color for _, color in image.getcolors(image.width * image.height)} == set(colors)


def test_style_reference_is_contained_in_native_canvas_without_mutating_source(style_profile, monkeypatch):
    source_path = profiles.ROOT / "refs" / "style.png"
    source_path.write_bytes(png_bytes(size=(31, 63)))
    before = source_path.read_bytes()
    _, guided, summary = pixellab_style.build_payloads("test", 32, 48, style_profile)
    normalized = Image.open(io.BytesIO(base64.b64decode(guided["style_image"]["base64"])))
    assert normalized.size == (32, 48)
    assert summary["reference_source_dimensions"] == [31, 63]
    assert summary["reference_normalized_dimensions"] == [32, 48]
    assert normalized.getpixel((0, 24))[3] == 0
    assert normalized.getpixel((4, 24))[3] == 255
    assert source_path.read_bytes() == before


def test_trial_makes_exactly_two_same_seed_calls_and_saves_review_evidence(style_profile, monkeypatch, tmp_path):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(routes, "api_key", lambda _: "test-secret")
    requests = []

    def post(url, headers, body):
        requests.append((url, headers, body))
        color = (230, 30, 180, 255) if len(requests) == 1 else (10, 30, 190, 255)
        raw_png = png_bytes(color)
        return {"image": {"base64": base64.b64encode(raw_png).decode(), "format": "png"},
                "usage": {"type": "generations", "generations": 1}}

    monkeypatch.setattr(routes, "_post_json", post)
    result = pixellab_style.run_trial("test lamp", 32, 32, style_profile, seed=11, log=lambda _: None)
    assert len(requests) == result["provider_calls"] == 2
    assert all(url == pixellab_style.ENDPOINT for url, _, _ in requests)
    assert all(headers == {"Authorization": "Bearer test-secret"} for _, headers, _ in requests)
    assert [body["seed"] for _, _, body in requests] == [11, 11]
    assert requests[0][2]["description"] == requests[1][2]["description"]
    assert result["status"] == "awaiting_visual_review"
    folder = tmp_path / "library" / result["library_id"]
    assert (folder / "meta.json").is_file() and (folder / "review.png").is_file()
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["status"] == "awaiting_visual_review"
    assert len(meta["provider_calls"]) == 2
    assert all(meta["variants"][n]["usage"]["generations"] == 1 for n in meta["variants"])
    assert all((folder / "work" / f"{n}_raw.png").is_file() for n in meta["variants"])
    assert all((folder / "work" / f"{n}_raw.png").read_bytes() == png_bytes(
        (230, 30, 180, 255) if n == "text_baseline" else (10, 30, 190, 255)) for n in meta["variants"])
    assert all((folder / "work" / f"{n}@2x.png").is_file() for n in meta["variants"])
    assert all((folder / "work" / f"{n}@8x.png").is_file() for n in meta["variants"])
    assert all("within_delta_e_10_percent" in meta["variants"][n]["palette_match"] for n in meta["variants"])


def test_partial_failure_keeps_first_result_and_never_retries_or_falls_back(style_profile, monkeypatch, tmp_path):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(routes, "api_key", lambda _: "test-secret")
    calls = []

    def post(url, headers, body):
        calls.append(body)
        if len(calls) == 2:
            raise routes.RouteError("HTTP 402: insufficient credits")
        return {"image": {"base64": base64.b64encode(png_bytes()).decode()}}

    monkeypatch.setattr(routes, "_post_json", post)
    result = pixellab_style.run_trial("test lamp", 32, 32, style_profile, log=lambda _: None)
    meta = json.loads((tmp_path / "library" / result["library_id"] / "meta.json").read_text())
    assert len(calls) == len(meta["provider_calls"]) == 2
    assert result["status"] == "awaiting_visual_review"
    assert meta["variants"]["text_baseline"]["status"] == "completed"
    assert meta["variants"]["style_palette_guided"]["status"] == "failed"
    assert meta["variants"]["style_palette_guided"]["error"].startswith("RouteError: HTTP 402")
    assert meta["review_sheet"] and (tmp_path / "library" / result["library_id"] / "review.png").is_file()


def test_resume_reuses_baseline_preserves_failed_history_and_is_idempotent(style_profile, monkeypatch, tmp_path):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(routes, "api_key", lambda _: "test-secret")
    requests = []

    def post(url, headers, body):
        requests.append(body)
        if len(requests) == 2:
            raise routes.RouteError("HTTP 500: style_image must match image_size")
        raw_png = png_bytes((80, 160, 220, 255), size=(32, 48))
        return {"image": {"base64": base64.b64encode(raw_png).decode(), "format": "png"},
                "usage": {"type": "generations", "generations": 1}}

    monkeypatch.setattr(routes, "_post_json", post)
    first = pixellab_style.run_trial("security console", 32, 48, style_profile, seed=72311,
                                     log=lambda _: None)
    folder = tmp_path / "library" / first["library_id"]
    meta_before = json.loads((folder / "meta.json").read_text())
    # Model the already-existing trial created before the canvas-size correction.
    meta_before["payload_sha256"]["style_palette_guided"] = hashlib.sha256(b"old native-size payload").hexdigest()
    (folder / "meta.json").write_text(json.dumps(meta_before, indent=2))
    baseline_path = Path(meta_before["variants"]["text_baseline"]["raw_image"])
    original_baseline = baseline_path.read_bytes()
    assert meta_before["variants"]["style_palette_guided"]["status"] == "failed"

    resumed = pixellab_style.resume_guided_trial(first["library_id"], log=lambda _: None)
    meta_after = json.loads((folder / "meta.json").read_text())
    assert len(requests) == 3 and resumed["provider_calls"] == 1
    assert requests[0]["seed"] == requests[2]["seed"] == 72311
    guided_ref = Image.open(io.BytesIO(base64.b64decode(requests[2]["style_image"]["base64"])))
    assert guided_ref.size == (32, 48)
    assert baseline_path.read_bytes() == original_baseline
    assert meta_after["variants"]["text_baseline"] == meta_before["variants"]["text_baseline"]
    history = meta_after["variant_attempts"]["style_palette_guided"]
    assert [item["status"] for item in history] == ["failed", "completed"]
    assert history[0]["payload_sha256"] == meta_before["payload_sha256"]["style_palette_guided"]
    assert history[1]["payload_sha256"] != history[0]["payload_sha256"]
    assert meta_after["provider_calls"][-1]["payload_sha256"] == meta_after["payload_sha256"]["style_palette_guided"]
    assert resumed["status"] == "awaiting_visual_review"
    assert (folder / "review.png").is_file()

    again = pixellab_style.resume_guided_trial(first["library_id"], log=lambda _: None)
    assert again["already_complete"] is True and again["provider_calls"] == 0
    assert len(requests) == 3


def test_resume_rejects_non_trial_or_invalid_pair_without_key(style_profile, monkeypatch, tmp_path):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    item_id, folder = library.new_dir("not trial")
    (folder / "meta.json").write_text(json.dumps({"kind": "generate"}))
    monkeypatch.setattr(routes, "api_key", lambda _: pytest.fail("invalid item must precede key lookup"))
    with pytest.raises(pixellab_style.StyleTrialError, match="not a PixelLab style trial"):
        pixellab_style.resume_guided_trial(item_id)


def test_resume_rejects_changed_profile_before_key_and_preserves_trial(style_profile, monkeypatch, tmp_path):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(routes, "api_key", lambda _: "test-secret")
    requests = []

    def post(url, headers, body):
        requests.append(body)
        if len(requests) == 2:
            raise routes.RouteError("HTTP 500: style image rejected")
        return {"image": {"base64": base64.b64encode(png_bytes(size=(32, 48))).decode(), "format": "png"}}

    monkeypatch.setattr(routes, "_post_json", post)
    trial = pixellab_style.run_trial("security console", 32, 48, style_profile, seed=72311,
                                     log=lambda _: None)
    folder = tmp_path / "library" / trial["library_id"]
    metadata_path = folder / "meta.json"
    before_meta = metadata_path.read_bytes()
    before_raw = Path(json.loads(before_meta)["variants"]["text_baseline"]["raw_image"]).read_bytes()
    profile_path = profiles.ROOT / "demo.json"
    changed_profile = json.loads(profile_path.read_text())
    changed_profile["style"] = "different art direction"
    profile_path.write_text(json.dumps(changed_profile))
    monkeypatch.setattr(routes, "api_key", lambda _: pytest.fail("changed profile must fail before key lookup"))

    with pytest.raises(pixellab_style.StyleTrialError, match="no longer matches"):
        pixellab_style.resume_guided_trial(trial["library_id"], log=lambda _: None)
    assert len(requests) == 2
    assert metadata_path.read_bytes() == before_meta
    assert Path(json.loads(before_meta)["variants"]["text_baseline"]["raw_image"]).read_bytes() == before_raw


def test_errors_redact_api_key_and_long_base64():
    message = "HTTP 500 Bearer top-secret " + ("a" * 200)
    safe = pixellab_style._safe_error(RuntimeError(message), "top-secret")
    assert "top-secret" not in safe and "a" * 160 not in safe
    assert "[REDACTED]" in safe and "[REDACTED_IMAGE_DATA]" in safe


def test_optional_style_route_makes_one_guided_call_and_reuses_cleanup(style_profile, monkeypatch):
    monkeypatch.setattr(routes, "api_key", lambda _: "test-secret")
    calls = []

    def post(url, headers, payload):
        calls.append((url, headers, payload))
        assert "style_image" in payload and "color_image" in payload
        image = png_bytes((30, 90, 220, 255), size=(32, 32))
        return {"image": {"base64": base64.b64encode(image).decode(), "format": "png"}}

    monkeypatch.setattr(routes, "_post_json", post)
    sprite = routes.ROUTES["pixellab-style"]("neon console", 32, 32, style_profile)
    assert len(calls) == 1
    assert calls[0][0] == "https://api.pixellab.ai/v2/create-image-bitforge"
    assert sprite.width == sprite.height == 32


def test_optional_style_route_rejects_missing_inputs_before_key(style_profile, monkeypatch):
    monkeypatch.setattr(routes, "api_key", lambda _: pytest.fail("invalid route input must precede key lookup"))
    with pytest.raises(routes.RouteError, match="reference image not found"):
        routes.ROUTES["pixellab-style"]("test", 32, 32,
                                         {**style_profile, "references": ["missing.png"]})
