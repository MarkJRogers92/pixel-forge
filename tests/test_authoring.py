import copy
import hashlib
import io
import json
import os
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image


def crate_request(request_id="fixture-crate-v1"):
    return {
        "schema_version": 1, "request_id": request_id, "title": "Direct test crate",
        "profile_id": "dead-mall", "canvas": {"width": 16, "height": 16},
        "palette": [[20, 20, 28, 255], [146, 111, 63, 255], [251, 177, 89, 255]],
        "operations": [
            {"op": "rectangle", "x": 2, "y": 3, "width": 12, "height": 11, "color": 0},
            {"op": "rectangle", "x": 3, "y": 4, "width": 10, "height": 9, "color": 1},
            {"op": "line", "x1": 3, "y1": 4, "x2": 12, "y2": 12, "color": 2},
        ],
    }


@pytest.fixture
def author(tmp_path):
    from forge.authoring_store import AuthoringStore
    from forge.readonly_mcp import ReadOnlyForge

    repo, art = tmp_path / "forge", tmp_path / "art"
    (repo / "profiles").mkdir(parents=True)
    art.mkdir()
    (repo / "projects.json").write_text(json.dumps({"dead-mall": {"name": "Mall", "path": str(art)}}))
    (repo / "profiles" / "dead-mall.json").write_text(json.dumps({
        "name": "Mall", "size": [32, 32], "palette_mode": "prefer", "style": "Clean outlines",
        "palette": {"a": "#14141c", "b": "#926f3f", "c": "#fbb159"},
    }))
    return AuthoringStore(ReadOnlyForge(repo, {"dead-mall": art}), enabled=True)


def fails(code, function, *args):
    from forge.authoring_schema import AuthoringError
    with pytest.raises(AuthoringError) as error:
        function(*args)
    assert error.value.code == code
    assert error.value.committed is False


def test_disabled_and_capabilities(author):
    author.enabled = False
    cap = author.capabilities()
    assert cap["authoring_enabled"] is False
    assert cap["external_model_calls"] is False
    assert cap["profiles"][0]["palette_rgba"][0] == [20, 20, 28, 255]
    assert set(cap["operation_schemas"]) == {
        "pixel", "line", "rectangle", "ellipse", "polygon", "erase_rectangle", "replace_color",
    }
    fails("AUTHORING_DISABLED", author.render, crate_request())
    fails("AUTHORING_DISABLED", author.edit, {})
    assert not (author.reads.repo / "library").exists()


@pytest.mark.parametrize("patch,code", [
    ({"canvas": {"width": True, "height": 16}}, "INVALID_REQUEST"),
    ({"canvas": {"width": 513, "height": 16}}, "LIMIT_EXCEEDED"),
    ({"title": "x" * 121}, "LIMIT_EXCEEDED"),
    ({"route": "pixellab"}, "INVALID_REQUEST"),
    ({"operations": [{"op": "exec", "code": "__import__('os').system('oops')"}]}, "UNSUPPORTED_OPERATION"),
    ({"operations": [{"op": "pixel", "x": -1, "y": 0, "color": 0}]}, "INVALID_REQUEST"),
    ({"operations": [{"op": "pixel", "x": 1.5, "y": 0, "color": 0}]}, "INVALID_REQUEST"),
    ({"operations": [{"op": "rectangle", "x": 15, "y": 0, "width": 2, "height": 1, "color": 0}]}, "INVALID_REQUEST"),
    ({"operations": [{"op": "pixel", "x": 0, "y": 0, "color": 3}]}, "INVALID_REQUEST"),
    ({"palette": [[0, 0, 0, 256]]}, "LIMIT_EXCEEDED"),
    ({"schema_version": True}, "INVALID_REQUEST"),
    ({"operations": [{"op": "polygon", "points": [[0, 0], [1, 1], [2, 2]], "color": 0}]}, "INVALID_REQUEST"),
])
def test_invalid_requests_leave_no_output(author, patch, code):
    data = {**crate_request(), **patch}
    fails(code, author.render, data)
    assert author.reads.library() == []
    assert not (author.reads.repo / "library").exists()


def test_render_edit_idempotency_and_read_compatibility(author):
    result = author.render(crate_request())
    root = author.reads.repo / "library" / result["asset_id"]
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    png = before["sprite.png"]
    assert result["output_sha256"] == hashlib.sha256(png).hexdigest()
    assert result["external_model_calls"] == 0
    assert result["parent_asset_id"] is None
    image = Image.open(io.BytesIO(png))
    assert image.size == (16, 16)
    assert image.getpixel((0, 0)) == (0, 0, 0, 0)
    assert image.getpixel((2, 3)) == (20, 20, 28, 255)
    assert image.getpixel((3, 4)) == (251, 177, 89, 255)
    assert hashlib.sha256(image.tobytes()).hexdigest() == "dc507441e0b523e490e25d67f0e857fe11e2668f09954f8a52f4291e2af30fe4"
    assert author.render(copy.deepcopy(crate_request())) == result
    changed = {**crate_request(), "title": "Other"}
    fails("REQUEST_CONFLICT", author.render, changed)
    source = {"source": "library", "id": result["asset_id"], "asset": "sprite.png"}
    source_info = author.capabilities(source)["source"]
    index = source_info["palette_rgba"].index([251, 177, 89, 255])
    edit = {"schema_version": 1, "request_id": "fixture-edit-v1", "title": "Crate revision",
            "base_asset": source, "expected_source_sha256": result["output_sha256"],
            "operations": [{"op": "pixel", "x": 4, "y": 4, "color": index}]}
    revision = author.edit(edit)
    assert revision["parent_asset_id"] == result["asset_id"]
    assert revision["source_sha256"] == result["output_sha256"]
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    assert {r["id"] for r in author.reads.library()} == {result["asset_id"], revision["asset_id"]}
    assert author.reads.preview("library", revision["asset_id"], scale=2)[0]["size"] == [16, 16]
    edit["request_id"] = "stale-edit-v1"
    edit["expected_source_sha256"] = "0" * 64
    fails("SOURCE_CHANGED", author.edit, edit)
    assert len(author.reads.library()) == 2


def test_alpha_and_required_operations():
    from forge.authoring_render import render_pixels
    from forge.authoring_schema import validate_request
    req = crate_request()
    req["palette"] = [[255, 0, 0, 128], [0, 0, 255, 128], [0, 255, 0, 255]]
    req["operations"] = [
        {"op": "pixel", "x": 0, "y": 0, "color": 0},
        {"op": "pixel", "x": 0, "y": 0, "color": 1},
        {"op": "ellipse", "x": 3, "y": 3, "width": 3, "height": 3, "color": 2},
        {"op": "polygon", "points": [[8, 3], [11, 3], [8, 6]], "color": 2},
        {"op": "erase_rectangle", "x": 3, "y": 3, "width": 1, "height": 1},
        {"op": "replace_color", "from_color": 2, "to_color": 0},
    ]
    image = render_pixels(validate_request("render", req), None)
    assert image.getpixel((0, 0)) == (85, 0, 170, 192)
    assert image.getpixel((4, 4)) == (255, 0, 0, 128)
    assert image.getpixel((8, 3)) == (255, 0, 0, 128)
    assert image.getpixel((3, 3)) == (0, 0, 0, 0)
    from forge.authoring_render import source_over
    assert source_over((1, 2, 3, 0), (4, 5, 6, 0)) == (4, 5, 6, 0)


def test_single_transaction_duplicate_concurrency(author, monkeypatch):
    from forge.authoring_schema import AuthoringError
    entered, release = threading.Event(), threading.Event()
    original = author._worker

    def held(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(author, "_worker", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(author.render, crate_request())
        assert entered.wait(3)
        fails("BUSY", author.render, crate_request())
        release.set()
        result = first.result(timeout=5)
    assert author.render(crate_request()) == result
    assert len(author.reads.library()) == 1


@pytest.mark.parametrize("phase", ["render", "publish", "after_publish", "record_commit"])
def test_durable_interruption_recovery(author, monkeypatch, phase):
    from forge.authoring_schema import AuthoringError
    from forge.authoring_store import Directory
    original_worker, original_publish, original_record = author._worker, author._publish, Directory.atomic_json
    saved = {}

    def render_failed(*args):
        raise AuthoringError("RENDER_TIMEOUT", "render")

    def publish_failed(stages, key, lib, asset_id):
        saved["asset_id"] = asset_id
        if phase == "after_publish":
            original_publish(stages, key, lib, asset_id)
        raise OSError("simulated crash")

    def record_failed(self, name, data):
        if data.get("state") == "committed":
            raise OSError("simulated record storage failure")
        return original_record(self, name, data)

    monkeypatch.setattr(author, "_worker", render_failed if phase == "render" else original_worker)
    monkeypatch.setattr(author, "_publish", publish_failed if phase in ("publish", "after_publish") else original_publish)
    monkeypatch.setattr(Directory, "atomic_json", record_failed if phase == "record_commit" else original_record)
    with pytest.raises(AuthoringError) as failure:
        author.render(crate_request())
    expected_visible = 1 if phase in ("after_publish", "record_commit") else 0
    assert failure.value.committed is bool(expected_visible)
    assert len(author.reads.library()) == expected_visible
    records = list((author.reads.repo / "library" / ".authoring" / "requests").glob("*.json"))
    pending = json.loads(records[0].read_bytes())
    assert pending["state"] == "pending"
    monkeypatch.setattr(author, "_worker", original_worker)
    monkeypatch.setattr(author, "_publish", original_publish)
    monkeypatch.setattr(Directory, "atomic_json", original_record)
    result = author.render(crate_request())
    assert result["asset_id"] == pending["asset_id"]
    assert json.loads(records[0].read_bytes())["state"] == "committed"
    assert len(author.reads.library()) == 1


def test_hard_deadline_kills_and_reaps_only_fixed_worker(author, monkeypatch):
    author.worker_timeout = 0.001
    original, pids = subprocess.Popen, []

    def observed(command, *args, **kwargs):
        assert command[1:] == ["-m", "forge.authoring_worker"]
        assert not any("KEY" in key or "TOKEN" in key for key in kwargs["env"])
        process = original(command, *args, **kwargs)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(subprocess, "Popen", observed)
    fails("RENDER_TIMEOUT", author.render, crate_request())
    assert len(pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)
    assert author.reads.library() == []


@pytest.mark.parametrize("asset", ["../private.png", "/private.png", "props/../../x.png", "props\\x.png", "meta.json"])
def test_authoring_source_paths_denied(author, asset):
    fails("PATH_DENIED", author.capabilities, {"source": "project", "id": "dead-mall", "asset": asset})


def test_symlinks_hardlinks_and_bad_images_denied(author, tmp_path):
    art = author.reads.project_roots["dead-mall"]
    outside = tmp_path / "outside.png"
    Image.new("RGBA", (1, 1)).save(outside)
    (art / "escape.png").symlink_to(outside)
    os.link(outside, art / "alias.png")
    for asset in ("escape.png", "alias.png"):
        fails("PATH_DENIED", author.capabilities, {"source": "project", "id": "dead-mall", "asset": asset})
    (art / "bad.png").write_bytes(b"not an image")
    fails("INVALID_REQUEST", author.capabilities, {"source": "project", "id": "dead-mall", "asset": "bad.png"})
    Image.new("RGBA", (513, 1)).save(art / "big.png")
    fails("LIMIT_EXCEEDED", author.capabilities, {"source": "project", "id": "dead-mall", "asset": "big.png"})
    colored = Image.new("RGBA", (65, 1))
    colored.putdata([(x, 0, 0, 255) for x in range(65)])
    colored.save(art / "many.png")
    fails("LIMIT_EXCEEDED", author.capabilities, {"source": "project", "id": "dead-mall", "asset": "many.png"})


def test_project_revision_preserves_exact_source_and_unrelated_files(author):
    art = author.reads.project_roots["dead-mall"]
    Image.new("RGBA", (2, 1), (123, 23, 3, 127)).save(art / "source.png")
    marker = author.reads.repo / "private.txt"
    marker.write_bytes(b"unrelated")
    before = {p: p.read_bytes() for p in author.reads.repo.rglob("*") if p.is_file()}
    source_bytes = (art / "source.png").read_bytes()
    ref = {"source": "project", "id": "dead-mall", "asset": "source.png"}
    info = author.capabilities(ref)["source"]
    request = {"schema_version": 1, "request_id": "project-edit-v1", "title": "Project test revision",
               "base_asset": ref, "expected_source_sha256": info["sha256"],
               "operations": [{"op": "erase_rectangle", "x": 0, "y": 0, "width": 1, "height": 1}]}
    result = author.edit(request)
    assert result["parent_asset_id"] is None
    assert result["source_asset"] == ref
    assert (art / "source.png").read_bytes() == source_bytes
    assert all(p.read_bytes() == data for p, data in before.items())
    image = Image.open(io.BytesIO(author.reads.preview("library", result["asset_id"])[1]))
    assert image.getpixel((1, 0)) == (123, 23, 3, 127)
    assert image.getpixel((0, 0)) == (0, 0, 0, 0)
    item = author.reads.repo / "library" / result["asset_id"]
    assert (item / "source.png").read_bytes() == source_bytes
    # A committed retry remains valid even after the external project changes.
    (art / "source.png").write_bytes(b"changed later")
    assert author.edit(request) == result


def test_existing_empty_directory_cannot_be_replaced(author, monkeypatch):
    from forge import library
    destination = author.reads.repo / "library" / "20261001-collision-abcd"
    destination.mkdir(parents=True)
    monkeypatch.setattr(library, "allocate_id", lambda _: destination.name)
    fails("STORAGE_FAILURE", author.render, crate_request())
    assert list(destination.iterdir()) == []


def test_publication_race_cannot_replace_an_empty_directory(author, monkeypatch):
    from forge.authoring_schema import AuthoringError
    original = author._publish
    collisions = []

    def collide(stages, key, lib, asset_id):
        os.mkdir(asset_id, dir_fd=lib.fd)
        collisions.append(asset_id)
        return original(stages, key, lib, asset_id)

    monkeypatch.setattr(author, "_publish", collide)
    fails("STORAGE_FAILURE", author.render, crate_request())
    assert list((author.reads.repo / "library" / collisions[0]).iterdir()) == []


def test_no_provider_network_or_model_launches(author, monkeypatch):
    original = subprocess.Popen

    def allowed(command, *args, **kwargs):
        assert command[1:] == ["-m", "forge.authoring_worker"]
        return original(command, *args, **kwargs)

    def blocked(*args, **kwargs):
        raise AssertionError("unexpected provider/network dependency")

    monkeypatch.setattr(subprocess, "Popen", allowed)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    result = author.render(crate_request())
    assert result["external_model_calls"] == 0


def test_payload_operation_and_color_limits(author):
    request = crate_request()
    request["operations"] = [{"op": "pixel", "x": 0, "y": 0, "color": 0}] * 2001
    fails("LIMIT_EXCEEDED", author.render, request)
    request = crate_request()
    request["palette"] = [[x, 0, 0, 255] for x in range(65)]
    fails("LIMIT_EXCEEDED", author.render, request)
    request = crate_request()
    request["description"] = "x" * (1024 * 1024)
    fails("LIMIT_EXCEEDED", author.render, request)
    request = crate_request()
    request["operations"] = [{"op": "polygon", "points": [[0, 0]] * 129, "color": 0}]
    fails("LIMIT_EXCEEDED", author.render, request)


def test_disabled_rollback_keeps_assets_and_reads(author):
    result = author.render(crate_request())
    author.enabled = False
    fails("AUTHORING_DISABLED", author.render, crate_request("after-rollback"))
    assert author.reads.library()[0]["id"] == result["asset_id"]
    assert author.reads.preview("library", result["asset_id"])[0]["size"] == [16, 16]


def test_worker_runs_with_model_network_and_process_dependencies_blocked():
    root = Path(__file__).resolve().parents[1]
    script = "\n".join([
        "import builtins, socket, subprocess, sys",
        "original_import = builtins.__import__",
        "def guarded(name, *args, **kwargs):",
        "    if name in ('forge.artist', 'forge.routes', 'forge.editors', 'forge.cli', 'forge.mcp_server'):",
        "        raise AssertionError('generation dependency imported')",
        "    return original_import(name, *args, **kwargs)",
        "builtins.__import__ = guarded",
        "def blocked(*args, **kwargs): raise AssertionError('external call')",
        "socket.socket.connect = blocked",
        "socket.create_connection = blocked",
        "subprocess.Popen = blocked",
        "from forge.authoring_worker import main",
        "main()",
        "assert not any(name in sys.modules for name in ('forge.artist','forge.routes','forge.cli','forge.editors','forge.mcp_server'))",
    ])
    packet = {"kind": "render", "request": crate_request(), "profile": None, "source_png": None}
    output = subprocess.run([str(root / ".venv" / "bin" / "python"), "-c", script],
                            input=json.dumps(packet), cwd=root, text=True, capture_output=True, timeout=5)
    assert output.returncode == 0
    assert "error" not in json.loads(output.stdout)


def test_rgba_limit_and_no_silent_quantization(author):
    request = crate_request()
    request["canvas"] = {"width": 64, "height": 1}
    request["palette"] = [[x, 0, 0, 255] for x in range(64)]
    request["operations"] = [{"op": "pixel", "x": x, "y": 0, "color": x} for x in range(64)]
    result = author.render(request)
    assert result["palette_count"] == 64
    request["request_id"] = "too-many-output-colors"
    request["canvas"] = {"width": 65, "height": 1}
    fails("LIMIT_EXCEEDED", author.render, request)  # Transparency is a 65th RGBA tuple.


def test_direct_png_decoder_preserves_hidden_rgb_and_rejects_animation(author):
    root = author.reads.project_roots["dead-mall"]
    image = Image.new("RGBA", (2, 1), (1, 2, 3, 0))
    image.save(root / "hidden.png")
    ref = {"source": "project", "id": "dead-mall", "asset": "hidden.png"}
    source = author.capabilities(ref)["source"]
    result = author.edit({"schema_version": 1, "request_id": "hidden-rgb", "title": "Exact hidden RGB",
                          "base_asset": ref, "expected_source_sha256": source["sha256"], "operations": []})
    output = author.reads.repo / "library" / result["asset_id"] / "sprite.png"
    assert Image.open(output).getpixel((0, 0)) == (1, 2, 3, 0)
    image.save(root / "animation.png", save_all=True, append_images=[Image.new("RGBA", (2, 1), "red")], duration=100)
    fails("INVALID_REQUEST", author.capabilities, {**ref, "asset": "animation.png"})
