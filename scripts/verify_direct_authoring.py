"""Local, model-free MCP smoke check. Keeps two labeled canonical fixture items.

Run only as part of approved local verification. This never changes tunnel/config.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "direct-crate-v1.json"
REPORT = ROOT / "artifacts" / "direct-authoring-verification.json"


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def result(value):
    parsed = json.loads(value.content[0].text)
    if value.is_error:
        raise RuntimeError(json.dumps(parsed))
    return parsed


async def main():
    REPORT.parent.mkdir(exist_ok=True)
    library_before = snapshot(ROOT / "library")
    project_root = ROOT.parent / "90s" / "public" / "assets" / "neon"
    project_before = snapshot(project_root)
    finals_root = Path.home() / "Desktop" / "art" / "finals"
    finals_before = snapshot(finals_root)
    env = {key: os.environ[key] for key in ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    request = json.loads(FIXTURE.read_text())
    request["request_id"] = "local-release1-crate-smoke-v1"
    local = StdioServerParameters(command=str(ROOT / "forge.sh"),
                                args=["mcp-authoring", "--enable-authoring"], cwd=ROOT, env=env)
    async with stdio_client(local) as streams, ClientSession(*streams, read_timeout_seconds=15) as client:
        await client.initialize()
        tools = (await client.list_tools()).tools
        assert len(tools) == 8
        (REPORT.parent / "direct-authoring-schemas.json").write_text(json.dumps(
            {tool.name: tool.model_dump(by_alias=True, exclude_none=True) for tool in tools}, indent=2))
        capabilities = result(await client.call_tool("forge_authoring_capabilities", {}))
        assert capabilities["authoring_enabled"] is True
        started = time.monotonic()
        created = result(await client.call_tool("forge_render_sprite", request))
        render_seconds = round(time.monotonic() - started, 3)
        assert result(await client.call_tool("forge_render_sprite", request)) == created
        source_path = ROOT / "library" / created["asset_id"]
        source_before = snapshot(source_path)
        source_ref = {"source": "library", "id": created["asset_id"], "asset": "sprite.png"}
        source = result(await client.call_tool("forge_authoring_capabilities", {"base_asset": source_ref}))["source"]
        edit = {"schema_version": 1, "request_id": "local-release1-crate-edit-smoke-v1",
                "title": "Direct test crate revision", "base_asset": source_ref,
                "expected_source_sha256": source["sha256"],
                "operations": [{"op": "pixel", "x": 4, "y": 4,
                                "color": source["palette_rgba"].index([251, 177, 89, 255])}]}
        started = time.monotonic()
        edited = result(await client.call_tool("forge_edit_sprite", edit))
        edit_seconds = round(time.monotonic() - started, 3)
        assert snapshot(source_path) == source_before
        assert edited["parent_asset_id"] == created["asset_id"]
        for label, item in (("create", created), ("edit", edited)):
            preview = await client.call_tool("forge_preview", {
                "source": "library", "id": item["asset_id"], "asset": "sprite.png", "scale": 8})
            assert [block.type for block in preview.content] == ["text", "image"]
            image_bytes = base64.b64decode(preview.content[1].data)
            (REPORT.parent / f"direct-{label}-preview.png").write_bytes(image_bytes)
            with Image.open(io.BytesIO(image_bytes)) as image:
                assert image.size == (128, 128)
        status = result(await client.call_tool("forge_status", {}))
        assert {created["asset_id"], edited["asset_id"]} <= {r["id"] for r in status["recent"]}
        assert created["external_model_calls"] == edited["external_model_calls"] == 0
    # Verify rollback plus the original launch interfaces through normal MCP protocol.
    modes = {}
    for arguments in (["mcp-authoring"], ["mcp-readonly"], ["mcp"]):
        params = StdioServerParameters(command=str(ROOT / "forge.sh"), args=arguments, cwd=ROOT, env=env)
        async with stdio_client(params) as streams, ClientSession(*streams, read_timeout_seconds=15) as client:
            await client.initialize()
            names = [tool.name for tool in (await client.list_tools()).tools]
            modes[arguments[0]] = names
            if arguments == ["mcp-authoring"]:
                disabled = result(await client.call_tool("forge_authoring_capabilities", {}))
                assert disabled["authoring_enabled"] is False
                rejected = await client.call_tool("forge_render_sprite", request)
                assert rejected.is_error and json.loads(rejected.content[0].text)["error"]["code"] == "AUTHORING_DISABLED"
            if arguments != ["mcp"]:
                assert result(await client.call_tool("forge_status", {}))["read_only"] is True
                preview = await client.call_tool("forge_preview", {**source_ref, "scale": 2})
                assert preview.content[1].type == "image"
            # Full legacy mode: discovery only; status could probe provider credentials.
    library_after = snapshot(ROOT / "library")
    assert all(library_after[name] == digest for name, digest in library_before.items())
    assert snapshot(project_root) == project_before
    assert snapshot(finals_root) == finals_before
    report = {"local_protocol_passed": True, "remote_access_enabled": False, "direct_dot_test_passed": False,
              "create": created, "edit": edited, "render_seconds": render_seconds, "edit_seconds": edit_seconds,
              "rollback_read_checks_passed": True, "launch_modes": modes,
              "preserved": {"existing_library_files": len(library_before),
                            "approved_project_files": len(project_before), "final_files": len(finals_before)}}
    REPORT.write_text(json.dumps(report, indent=2))
    print(json.dumps({"report": str(REPORT), "render_seconds": render_seconds, "edit_seconds": edit_seconds,
                      "create_id": created["asset_id"], "edit_id": edited["asset_id"], "preserved": report["preserved"]}))


if __name__ == "__main__":
    asyncio.run(main())
