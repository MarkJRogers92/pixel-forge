import asyncio
import json
import subprocess
import sys
import threading

from pathlib import Path


def test_authoring_tool_schemas_and_disabled_boundary():
    from forge.authoring_mcp import AuthoringMCP
    from forge.authoring_store import AuthoringStore
    from forge.readonly_mcp import store

    async def check():
        server = AuthoringMCP(AuthoringStore(store))
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert len(tools) == 8
        assert tools["forge_authoring_capabilities"].annotations.read_only_hint is True
        for name in ("forge_render_sprite", "forge_edit_sprite"):
            assert tools[name].annotations.read_only_hint is False
            assert tools[name].annotations.destructive_hint is False
            assert tools[name].annotations.open_world_hint is False
            assert tools[name].input_schema["additionalProperties"] is False
            result = await server.call_tool(name, {})
            assert result.is_error
            assert json.loads(result.content[0].text)["error"]["code"] == "AUTHORING_DISABLED"
        assert "forge_make" not in tools

    asyncio.run(check())


def test_strict_transport_rejects_duplicate_keys_before_parsing_tools():
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, "-m", "forge", "mcp-authoring"], cwd=root,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate(
        b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/call","params":{"name":"forge_render_sprite"}}\n',
        timeout=5)
    responses = [json.loads(line) for line in stdout.splitlines()]
    assert responses[0]["error"]["data"]["error"]["code"] == "INVALID_REQUEST"
    assert responses[0]["error"]["data"]["error"]["field"] == "duplicate_json_key"
    assert b"Traceback" not in stderr


def test_transport_and_parser_limits():
    from forge.authoring_schema import AuthoringError, strict_json
    for value in ('{"x":NaN}', '{"x":Infinity}', '{"x":1,"x":2}'):
        try:
            strict_json(value)
            assert False, "malformed JSON accepted"
        except AuthoringError as error:
            assert error.code == "INVALID_REQUEST"
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, "-m", "forge", "mcp-authoring"], cwd=root,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = process.communicate(b"x" * (1024 * 1024 + 8192) + b"\n", timeout=5)
    assert json.loads(stdout.splitlines()[0])["error"]["data"]["error"]["code"] == "LIMIT_EXCEEDED"


def test_mcp_busy_has_no_queued_writes_or_early_capacity_release():
    from forge.authoring_mcp import AuthoringMCP
    from forge.authoring_store import AuthoringStore
    from forge.readonly_mcp import store

    author = AuthoringStore(store, enabled=True)
    entered, release = threading.Event(), threading.Event()

    def held(_):
        entered.set()
        release.wait(3)
        return {"ok": True}

    author.render = held

    async def check():
        server = AuthoringMCP(author)
        first = asyncio.create_task(server.call_tool("forge_render_sprite", {}))
        await asyncio.to_thread(entered.wait, 3)
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        rejected = await server.call_tool("forge_render_sprite", {})
        assert json.loads(rejected.content[0].text)["error"]["code"] == "BUSY"
        release.set()
        while server._mutating:
            await asyncio.sleep(0)

    asyncio.run(check())
