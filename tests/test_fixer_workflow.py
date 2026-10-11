"""The preferred image workflow preserves art and pauses for real visual review."""
import base64
import asyncio
import importlib
import io
import json
from pathlib import Path
import urllib.error

import numpy as np
import pytest
from PIL import Image

from forge import artist, aseprite, cli, editors, library


def png_bytes(image):
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def sample_image(size=(32, 48)):
    im = Image.new("RGBA", size)
    for y in range(4, size[1] - 4):
        for x in range(4, size[0] - 4):
            im.putpixel((x, y), (28, 20, 40, 255) if x in (4, size[0] - 5) or y in (4, size[1] - 5)
                        else (224, 64, 138, 255))
    return im


def install_http(monkeypatch, output, requests):
    import urllib.request
    monkeypatch.setenv("RETRODIFFUSION_API_KEY", "test-token")

    def respond(req, **kwargs):
        requests.append(req)
        return io.BytesIO(json.dumps({"base64_images": [base64.b64encode(png_bytes(output)).decode()]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", respond)


def forbid_models(*args, **kwargs):
    pytest.fail("prepare must not invoke a Claude/GPT drawing or review backend")


def test_prepare_reuses_image_preserves_pixels_and_hands_off_review(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(artist, "make_backend", forbid_models)
    monkeypatch.setattr(artist, "generate", forbid_models)
    monkeypatch.setattr(artist, "paint_with_gpt_image", forbid_models)
    source = tmp_path / "source.png"
    image = sample_image()
    original = png_bytes(image)
    source.write_bytes(original)
    requests = []
    install_http(monkeypatch, image, requests)
    result = cli.prepare("pink cabinet", 32, 48, input_png=str(source), checks=["clear silhouette"])
    assert source.read_bytes() == original
    assert np.array_equal(np.asarray(Image.open(result["png"])), np.asarray(image))
    assert result["status"] == "awaiting_visual_review"
    assert result["review"]["reviewer"] == "dot"
    assert result["review"]["checks"] == ["clear silhouette"]
    assert result["review"]["scales"] == [1, 2]
    assert Path(result["review"]["sheet"]).is_file()
    meta = json.loads((library.ROOT / result["library_id"] / "meta.json").read_text())
    assert meta["workflow"] == "gpt-rd-neural"
    assert meta["provider_calls"] == {"gpt_image": 0, "rd_neural_fixer": 1, "claude": 0}
    assert meta["cleanup"]["grid"] is None
    assert result["qa"]["lint"]["passed"] is True
    assert len(requests) == 1
    assert requests[0].full_url.endswith("/v1/pixel-fixer/neural")


def test_prepare_generates_one_gpt_reference_with_checks_then_fixes(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(artist, "make_backend", forbid_models)
    seen = []

    def paint(prompt, width, height, profile, workdir, log, **kwargs):
        assert kwargs["strict_output"] is True
        seen.append(prompt)
        path = workdir / "gpt_painting.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        sample_image().save(path)
        return path

    monkeypatch.setattr(artist, "paint_with_gpt_image", paint)
    install_http(monkeypatch, sample_image(), [])
    result = cli.prepare("pink cabinet", 32, 48, checks=["no lettering"])
    assert len(seen) == 1 and "no lettering" in seen[0]
    meta = json.loads((library.ROOT / result["library_id"] / "meta.json").read_text())
    assert meta["provider_calls"]["gpt_image"] == 1


def test_neural_fixer_bounds_upload_without_losing_alpha(monkeypatch):
    fixer = importlib.import_module("forge.rd_fixer")
    requests = []
    install_http(monkeypatch, sample_image(), requests)
    data = np.random.default_rng(5).integers(0, 256, (800, 800, 4), dtype=np.uint8)
    output, report = fixer.neural_fixer(Image.fromarray(data), 32, 48)
    assert output.size == (32, 48)
    body = json.loads(requests[0].data)
    assert len(requests[0].data) <= 900_000
    uploaded = Image.open(io.BytesIO(base64.b64decode(body["input_image"])))
    assert uploaded.mode == "RGBA"
    assert uploaded.width < 800 and uploaded.height < 800
    assert report["upload_size"] == list(uploaded.size)
    assert body["width"] == 32 and body["height"] == 48
    assert set(body) == {"input_image", "width", "height"}


def test_neural_fixer_fails_on_wrong_output_size(monkeypatch):
    fixer = importlib.import_module("forge.rd_fixer")
    install_http(monkeypatch, sample_image((16, 16)), [])
    with pytest.raises(ValueError, match="dimensions"):
        fixer.neural_fixer(sample_image(), 32, 48)


def test_fixer_http_failure_is_sanitized_without_generation_fallback(monkeypatch):
    fixer = importlib.import_module("forge.rd_fixer")
    monkeypatch.setenv("RETRODIFFUSION_API_KEY", "test-token")
    monkeypatch.setattr(artist, "make_backend", forbid_models)

    def fail(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 429, "test-token", {}, io.BytesIO(b"test-token"))

    monkeypatch.setattr(fixer.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="HTTP 429") as error:
        fixer.neural_fixer(sample_image(), 32, 48)
    assert "test-token" not in str(error.value)


def test_missing_rd_key_stops_before_gpt_generation(tmp_path, monkeypatch):
    fixer = importlib.import_module("forge.rd_fixer")
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(fixer.routes, "api_key", lambda var: "")
    monkeypatch.setattr(artist, "paint_with_gpt_image", forbid_models)
    with pytest.raises(RuntimeError, match="RETRODIFFUSION_API_KEY"):
        cli.prepare("cabinet", 32, 48)
    assert not library.ROOT.exists()


def test_prepare_records_failed_stage_and_keeps_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    source = tmp_path / "input.png"
    original = png_bytes(sample_image())
    source.write_bytes(original)
    monkeypatch.setenv("RETRODIFFUSION_API_KEY", "test-token")

    def fail(*args, **kwargs):
        raise urllib.error.URLError("test-token")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(RuntimeError, match="connection failed"):
        cli.prepare("cabinet", 32, 48, input_png=str(source))
    item = next(library.ROOT.iterdir())
    meta = json.loads((item / "meta.json").read_text())
    assert meta["status"] == "failed"
    assert meta["failed_stage"] == "rd_neural_fixer"
    assert meta["provider_calls"]["rd_neural_fixer"] == 1
    assert "test-token" not in json.dumps(meta)
    assert (item / "work" / "gpt_painting.png").read_bytes() == original
    assert source.read_bytes() == original
    assert library.list_items() == []


@pytest.mark.parametrize("symlink", [False, True])
def test_prepare_existing_output_stops_before_provider_calls(tmp_path, monkeypatch, symlink):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(artist, "paint_with_gpt_image", forbid_models)
    monkeypatch.setattr("forge.rd_fixer.require_key", forbid_models)
    destination = tmp_path / "existing.png"
    if symlink:
        destination.symlink_to(tmp_path / "missing.png")
    else:
        destination.write_bytes(b"keep this")
    with pytest.raises(FileExistsError):
        cli.prepare("cabinet", 32, 48, out=str(destination))
    assert not library.ROOT.exists()


def test_workflow_aseprite_layers_keep_the_target_canvas(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    source = tmp_path / "reference.png"
    sample_image((256, 384)).save(source)
    install_http(monkeypatch, sample_image(), [])
    result = cli.prepare("cabinet", 32, 48, input_png=str(source))
    layers = editors.document_layers(result["library_id"])
    assert [name for name, _, _ in layers] == ["final", "forge cleanup", "RD neural fixer", "GPT reference (scaled)"]
    assert [visible for _, _, visible in layers] == [True, False, False, False]
    assert all(Image.open(path).size == (32, 48) for _, path, _ in layers)
    if aseprite.find_executable():
        doc = editors.aseprite_document(result["library_id"])
        exported = aseprite.export_png(doc, tmp_path / "roundtrip.png")
        assert np.array_equal(np.asarray(Image.open(exported)), np.asarray(Image.open(result["png"])))


def test_repair_preserves_source_and_changes_only_the_requested_pixel(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    source = tmp_path / "input.png"
    sample_image().save(source)
    install_http(monkeypatch, sample_image(), [])
    prepared = cli.prepare("cabinet", 32, 48, input_png=str(source))
    original = Path(prepared["png"]).read_bytes()
    sprite = json.loads((library.ROOT / prepared["library_id"] / "sprite.json").read_text())
    color = sprite["rows"][4][4]

    class Repairer:
        name = "claude"
        model = "opus"

        def ask(self, system, text, images, workdir):
            assert all((workdir / path.name).is_file() for path in images)
            return json.dumps({"base": "current", "ops": [{"op": "points", "c": color, "points": [[8, 8]]}]})

    monkeypatch.setattr(artist, "make_backend", lambda name, model: Repairer())
    result = cli.repair(prepared["library_id"], "darken the pixel at 8,8")
    before = np.asarray(Image.open(prepared["png"]))
    after = np.asarray(Image.open(result["png"]))
    assert np.count_nonzero(np.any(before != after, axis=2)) == 1
    assert Path(prepared["png"]).read_bytes() == original
    assert result["library_id"] != prepared["library_id"]
    assert result["status"] == "awaiting_visual_review"
    assert result["changed_pixels"] == 1
    assert result["qa"]["lint"]["passed"] is True


@pytest.mark.parametrize("program", [
    {"base": "blank", "ops": []},
    {"base": "current", "ops": [{"op": "rect", "x": 0, "y": 0, "w": 32, "h": 48, "c": "z"}], "palette": {"z": "#ffffff"}},
    {"base": "current", "ops": [{"op": "not-a-real-operation"}]},
])
def test_repair_rejects_redraws_excessive_changes_and_render_errors(tmp_path, monkeypatch, program):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    source = tmp_path / "input.png"
    sample_image().save(source)
    install_http(monkeypatch, sample_image(), [])
    prepared = cli.prepare("cabinet", 32, 48, input_png=str(source))

    class Repairer:
        name = "claude"
        model = "opus"

        def ask(self, *args):
            return json.dumps(program)

    monkeypatch.setattr(artist, "make_backend", lambda name, model: Repairer())
    with pytest.raises(ValueError):
        cli.repair(prepared["library_id"], "small patch")
    assert len(library.list_items()) == 1


def test_repair_rejects_unbounded_operations_before_render(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    source = tmp_path / "input.png"
    sample_image().save(source)
    install_http(monkeypatch, sample_image(), [])
    prepared = cli.prepare("cabinet", 32, 48, input_png=str(source))

    class Repairer:
        def ask(self, *args):
            return json.dumps({"base": "current", "ops": [
                {"op": "line", "x1": 0, "y1": 0, "x2": 10**12, "y2": 0, "c": "a"}]})

    monkeypatch.setattr(artist, "make_backend", lambda name, model: Repairer())
    monkeypatch.setattr("forge.fixer_workflow.dsl.render", forbid_models)
    with pytest.raises(ValueError, match="bounded"):
        cli.repair(prepared["library_id"], "small patch")


def test_workflow_mcp_returns_visual_review_and_honest_annotations(tmp_path, monkeypatch):
    from forge.mcp_server import server
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(artist, "make_backend", forbid_models)
    source = tmp_path / "input.png"
    sample_image().save(source)
    install_http(monkeypatch, sample_image(), [])

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        for name in ("forge_prepare", "forge_repair"):
            annotation = tools[name].annotations
            assert annotation.read_only_hint is False
            assert annotation.destructive_hint is False
            assert annotation.idempotent_hint is False
            assert annotation.open_world_hint is True
        result = await server.call_tool("forge_prepare", {
            "prompt": "cabinet", "width": 32, "height": 48, "input_png": str(source)})
        assert not result.is_error
        assert [part.type for part in result.content] == ["text", "image"]
        data = json.loads(result.content[0].text)
        assert data["status"] == "awaiting_visual_review"
        assert "visual approval" in data["review"]["instructions"]

    asyncio.run(check())


def test_strict_gpt_generation_does_not_adopt_an_unrelated_png(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(artist, "GENERATED_DIR", tmp_path / "generated_images")
    work = tmp_path / "work"

    def run(*args, **kwargs):
        unrelated = artist.GENERATED_DIR / "other-session" / "image.png"
        unrelated.parent.mkdir(parents=True)
        sample_image().save(unrelated)
        return subprocess.CompletedProcess(args[0], 0, stdout="DONE", stderr="")

    monkeypatch.setattr(artist.subprocess, "run", run)
    with pytest.raises(artist.ArtistError, match="output path"):
        artist.paint_with_gpt_image("cabinet", 32, 48, None, work, strict_output=True)
    assert not (work / "gpt_painting.png").exists()


def test_strict_gpt_generation_uses_its_reported_file(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(artist, "GENERATED_DIR", tmp_path / "generated_images")

    def run(*args, **kwargs):
        generated = artist.GENERATED_DIR / "this-session" / "image.png"
        generated.parent.mkdir(parents=True)
        sample_image().save(generated)
        return subprocess.CompletedProcess(args[0], 0, stdout=f"Saved to {generated}\nDONE", stderr="")

    monkeypatch.setattr(artist.subprocess, "run", run)
    result = artist.paint_with_gpt_image("cabinet", 32, 48, None, tmp_path / "work", strict_output=True)
    assert Image.open(result).size == (32, 48)


def test_repair_rejects_partial_alpha_without_a_model_call(tmp_path, monkeypatch):
    from forge.sprite import Sprite
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(artist, "make_backend", forbid_models)
    item_id, folder = library.new_dir("exact-alpha fixture")
    image = sample_image()
    image.putpixel((8, 8), (20, 40, 60, 120))
    library.save_result(folder, Sprite.from_image(sample_image()), {"prompt": "fixture"})
    image.save(folder / "sprite.png")
    before = (folder / "sprite.png").read_bytes()
    with pytest.raises(ValueError, match="binary transparency"):
        cli.repair(item_id, "small patch")
    assert (folder / "sprite.png").read_bytes() == before
    assert len(list(library.ROOT.iterdir())) == 1


def test_failed_opus_call_has_sanitized_metadata_and_preserves_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    source = tmp_path / "input.png"
    sample_image().save(source)
    install_http(monkeypatch, sample_image(), [])
    prepared = cli.prepare("cabinet", 32, 48, input_png=str(source))
    original = Path(prepared["png"]).read_bytes()

    class Repairer:
        def ask(self, *args):
            raise artist.ArtistError("private authentication details")

    monkeypatch.setattr(artist, "make_backend", lambda *args: Repairer())
    with pytest.raises(artist.ArtistError):
        cli.repair(prepared["library_id"], "small patch")
    failed = next(folder for folder in library.ROOT.iterdir() if folder.name != prepared["library_id"])
    meta = json.loads((failed / "meta.json").read_text())
    assert meta["status"] == "failed" and meta["failed_stage"] == "claude_repair"
    assert meta["provider_calls"]["claude"] == 1
    assert "private authentication details" not in json.dumps(meta)
    assert Path(prepared["png"]).read_bytes() == original
