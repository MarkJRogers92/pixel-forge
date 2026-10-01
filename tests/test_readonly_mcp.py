import asyncio
import base64
import io
import json
import socket
import subprocess
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def store(tmp_path):
    from forge.readonly_mcp import ReadOnlyForge

    repo = tmp_path / "forge"
    art = tmp_path / "art"
    repo.mkdir()
    (art / "props").mkdir(parents=True)
    Image.new("RGBA", (3, 2), (255, 0, 255, 255)).save(art / "props" / "cabinet.png")
    (repo / "projects.json").write_text(json.dumps({
        "dead-mall": {"name": "DEAD MALL", "path": str(art)},
        "private": {"name": "Other", "path": str(tmp_path)},
    }))
    (repo / "profiles").mkdir()
    (repo / "profiles" / "dead-mall.json").write_text(json.dumps({
        "name": "Neon", "size": [32, 32], "palette": {"a": "#ff00ff"},
    }))
    item = repo / "library" / "20261001-cabinet-abcd"
    item.mkdir(parents=True)
    Image.new("RGBA", (3, 2), (255, 0, 255, 255)).save(item / "sprite.png")
    (item / "meta.json").write_text(json.dumps({
        "prompt": "Cabinet", "size": [3, 2], "kind": "generate", "private": "omit me",
    }))
    return ReadOnlyForge(repo, {"dead-mall": art})


def test_status_and_library_only_return_approved_metadata(store):
    status = store.status()
    assert status["read_only"] is True
    assert status["projects"] == [{"id": "dead-mall", "name": "DEAD MALL"}]
    assert status["profiles"][0]["colors"] == 1
    assert status["recent"] == [{
        "id": "20261001-cabinet-abcd", "prompt": "Cabinet", "size": [3, 2],
        "kind": "generate", "assets": ["sprite.png"],
    }]
    assert "artists" not in status and "routes" not in status


def test_project_listing_never_registers_folder_inputs(store, tmp_path):
    registry = (store.repo / "projects.json").read_bytes()
    with pytest.raises(ValueError):
        store.assets(str(tmp_path))
    with pytest.raises(ValueError):
        store.assets("private")
    assert (store.repo / "projects.json").read_bytes() == registry
    assert store.assets("dead-mall")["files"] == [
        {"path": "props/cabinet.png", "width": 3, "height": 2},
    ]


@pytest.mark.parametrize("limit", [0, -1, 101, True])
def test_list_limits_are_enforced(store, limit):
    with pytest.raises(ValueError):
        store.library(limit)
    with pytest.raises(ValueError):
        store.assets("dead-mall", limit=limit)


@pytest.mark.parametrize("source, item_id, asset", [
    ("library", "../art", "sprite.png"),
    ("library", "20261001-cabinet-abcd", "meta.json"),
    ("project", "dead-mall", "../projects.json"),
    ("project", "dead-mall", "/etc/passwd"),
    ("project", "private", "props/cabinet.png"),
    ("other", "dead-mall", "props/cabinet.png"),
])
def test_preview_rejects_unapproved_paths(store, source, item_id, asset):
    with pytest.raises(ValueError):
        store.preview(source, item_id, asset)


def test_preview_rejects_symlink_escape(store, tmp_path):
    outside = tmp_path / "outside.png"
    Image.new("RGB", (1, 1)).save(outside)
    root = store.project_roots["dead-mall"]
    (root / "escape.png").symlink_to(outside)
    with pytest.raises(ValueError):
        store.preview("project", "dead-mall", "escape.png")
    assert "escape.png" not in [x["path"] for x in store.assets("dead-mall")["files"]]


def test_library_metadata_cannot_follow_symlink(store, tmp_path):
    item = store.repo / "library" / "20261001-cabinet-abcd"
    (item / "meta.json").unlink()
    outside = tmp_path / "outside.json"
    outside.write_text('{"prompt": "secret"}')
    (item / "meta.json").symlink_to(outside)
    assert store.library() == []


def test_preview_is_nearest_neighbour_in_memory(store):
    info, png = store.preview("library", "20261001-cabinet-abcd", scale=8)
    assert info == {"source": "library", "id": "20261001-cabinet-abcd", "asset": "sprite.png",
                    "size": [3, 2], "preview_size": [24, 16], "scale": 8}
    im = Image.open(io.BytesIO(png))
    assert im.size == (24, 16)
    assert im.getpixel((23, 15)) == (255, 0, 255, 255)
    with pytest.raises(ValueError):
        store.preview("library", "20261001-cabinet-abcd", scale=3)


def test_large_preview_is_rejected_before_loading(store):
    root = store.project_roots["dead-mall"]
    Image.new("RGB", (1500, 1500)).save(root / "large.png")
    with pytest.raises(ValueError):
        store.preview("project", "dead-mall", "large.png")


def test_tools_do_not_write_spawn_or_connect(store, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("read-only tool attempted a side effect")

    before = {p: p.read_bytes() for p in store.repo.rglob("*") if p.is_file()}
    for name in ("write_text", "write_bytes", "mkdir", "unlink", "rename"):
        monkeypatch.setattr(Path, name, blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    store.status()
    store.projects()
    store.assets("dead-mall")
    store.library()
    store.preview("library", "20261001-cabinet-abcd")
    assert before == {p: p.read_bytes() for p in store.repo.rglob("*") if p.is_file()}


def test_mcp_only_advertises_readonly_tools_and_rejects_generation(store, monkeypatch):
    from forge import readonly_mcp

    monkeypatch.setattr(readonly_mcp, "store", store)

    async def check():
        listing = await readonly_mcp.server.list_tools()
        assert {t.name for t in listing} == {
            "forge_status", "forge_projects", "forge_project_assets", "forge_library", "forge_preview",
        }
        for tool in listing:
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
        with pytest.raises(Exception, match="[Uu]nknown tool"):
            await readonly_mcp.server.call_tool("forge_make", {"prompt": "never run"})
        result = await readonly_mcp.server.call_tool("forge_preview", {
            "source": "library", "id": "20261001-cabinet-abcd", "scale": 2,
        })
        assert [c.type for c in result.content] == ["text", "image"]
        assert Image.open(io.BytesIO(base64.b64decode(result.content[1].data))).size == (6, 4)

    asyncio.run(check())
