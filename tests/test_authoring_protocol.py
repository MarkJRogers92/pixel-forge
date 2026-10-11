"""Real MCP client/server protocol test against an isolated fixture library."""
import asyncio
import copy
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_authoring import author, crate_request


def test_protocol_create_edit_preview_and_rollback(author):
    root = Path(__file__).resolve().parents[1]
    # Fixed test-only bootstrap; production exposes no configurable root argument.
    bootstrap = "\n".join([
        "import sys",
        "from pathlib import Path",
        "from forge import readonly_mcp",
        "from forge.authoring_mcp import main",
        "readonly_mcp.store = readonly_mcp.ReadOnlyForge(Path(sys.argv[1]), {'dead-mall': Path(sys.argv[2])})",
        "main(sys.argv[3:])",
    ])

    async def connect(enabled):
        params = StdioServerParameters(command=sys.executable, cwd=root,
            args=["-c", bootstrap, str(author.reads.repo), str(author.reads.project_roots["dead-mall"])] +
                 (["--enable-authoring"] if enabled else []))
        async with stdio_client(params) as streams, ClientSession(*streams, read_timeout_seconds=15) as client:
            await client.initialize()
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert len(names) == 8 and "forge_make" not in names
            cap = await client.call_tool("forge_authoring_capabilities", {})
            assert json.loads(cap.content[0].text)["authoring_enabled"] == enabled
            if not enabled:
                rejected = await client.call_tool("forge_render_sprite", crate_request("rollback-protocol"))
                assert rejected.is_error
                assert json.loads(rejected.content[0].text)["error"]["code"] == "AUTHORING_DISABLED"
                listing = await client.call_tool("forge_library", {"limit": 2})
                assert len(json.loads(listing.content[0].text)["items"]) == 2
                return
            created = await client.call_tool("forge_render_sprite", crate_request())
            assert not created.is_error
            result = json.loads(created.content[0].text)
            ref = {"source": "library", "id": result["asset_id"], "asset": "sprite.png"}
            metadata = await client.call_tool("forge_authoring_capabilities", {"base_asset": ref})
            source = json.loads(metadata.content[0].text)["source"]
            index = source["palette_rgba"].index([251, 177, 89, 255])
            edited = await client.call_tool("forge_edit_sprite", {
                "schema_version": 1, "request_id": "protocol-edit-v1", "title": "Protocol revision",
                "base_asset": ref, "expected_source_sha256": source["sha256"],
                "operations": [{"op": "pixel", "x": 4, "y": 4, "color": index}],
            })
            assert not edited.is_error
            revision = json.loads(edited.content[0].text)
            assert revision["parent_asset_id"] == result["asset_id"]
            for item in (result, revision):
                preview = await client.call_tool("forge_preview", {
                    "source": "library", "id": item["asset_id"], "asset": "sprite.png", "scale": 2})
                assert [block.type for block in preview.content] == ["text", "image"]
                assert item["external_model_calls"] == 0
            status = await client.call_tool("forge_status", {})
            assert json.loads(status.content[0].text)["read_only"] is True

    asyncio.run(connect(True))
    asyncio.run(connect(False))
