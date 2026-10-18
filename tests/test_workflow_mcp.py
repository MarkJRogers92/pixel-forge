"""The provider workflow exposes a narrow, explicitly enabled MCP boundary."""
import asyncio
import json

import pytest


EXISTING_TOOLS = {
    "forge_status", "forge_projects", "forge_project_assets", "forge_library", "forge_preview",
    "forge_authoring_capabilities", "forge_render_sprite", "forge_edit_sprite",
}
WORKFLOW_WRITES = {
    "forge_prepare": True,
    "forge_repair": True,
    "forge_open_aseprite": False,
    "forge_import_aseprite": False,
}
WORKFLOW_TOOLS = {*WORKFLOW_WRITES, "forge_workflow_status"}
FORBIDDEN_FIELDS = {
    "input_png", "input_path", "reference_png", "out", "output", "output_path", "path", "doc",
    "open_in", "routes", "route", "shell", "command", "script", "backend", "model",
    "artist", "refiner", "api_key", "credential",
}


def make_server(tmp_path, *, enabled=False):
    from forge.authoring_store import AuthoringStore
    from forge.readonly_mcp import ReadOnlyForge
    from forge.workflow_jobs import WorkflowJobs
    from forge.workflow_mcp import WorkflowMCP

    reads = ReadOnlyForge(tmp_path, {})
    jobs = WorkflowJobs(reads, enabled=enabled)
    return WorkflowMCP(AuthoringStore(reads), jobs)


def error_code(result):
    assert result.is_error is True
    content = json.loads(result.content[0].text)
    return content["error"]["code"]


def assert_closed_objects(schema):
    """A strict wrapper must also close every nested object it advertises."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False
            assert not FORBIDDEN_FIELDS.intersection(schema.get("properties", {}))
        for value in schema.values():
            assert_closed_objects(value)
    elif isinstance(schema, list):
        for value in schema:
            assert_closed_objects(value)


def test_workflow_tool_set_and_permissions_preserve_existing_authoring(tmp_path):
    async def check():
        tools = {tool.name: tool for tool in await make_server(tmp_path).list_tools()}
        assert set(tools) == EXISTING_TOOLS | WORKFLOW_TOOLS
        assert len(tools) == 13
        for name, open_world in WORKFLOW_WRITES.items():
            annotations = tools[name].annotations
            assert annotations.read_only_hint is False
            assert annotations.destructive_hint is False
            assert annotations.idempotent_hint is True
            assert annotations.open_world_hint is open_world
        assert tools["forge_workflow_status"].annotations.read_only_hint is True
        assert tools["forge_authoring_capabilities"].annotations.read_only_hint is True
        for name in ("forge_render_sprite", "forge_edit_sprite"):
            annotations = tools[name].annotations
            assert annotations.read_only_hint is False
            assert annotations.idempotent_hint is True
            assert annotations.open_world_hint is False
        assert "forge_make" not in tools
        assert "forge_open" not in tools
        assert "forge_from_aseprite" not in tools

    asyncio.run(check())


def test_workflow_schemas_are_closed_and_have_no_arbitrary_paths_or_provider_routes(tmp_path):
    async def check():
        tools = {tool.name: tool for tool in await make_server(tmp_path).list_tools()}
        for name in WORKFLOW_TOOLS:
            schema = tools[name].input_schema
            assert schema["type"] == "object"
            assert "request_id" in schema["properties"]
            assert "request_id" in schema["required"]
            assert_closed_objects(schema)

    asyncio.run(check())


@pytest.mark.parametrize("name", list(WORKFLOW_WRITES))
@pytest.mark.parametrize("arguments", [
    {},
    {"request_id": "disabled-test", "unexpected": True},
    {"request_id": "../../escape", "input_png": "/tmp/unapproved.png", "routes": ["rd"]},
])
def test_disabled_workflow_rejects_before_validation_or_external_execution(tmp_path, monkeypatch, name, arguments):
    from forge import artist
    import subprocess
    import urllib.request

    def forbidden(*args, **kwargs):
        pytest.fail("disabled workflow attempted to execute a provider or editor")

    monkeypatch.setattr(artist, "paint_with_gpt_image", forbidden)
    monkeypatch.setattr(artist, "make_backend", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

    async def check():
        server = make_server(tmp_path)
        assert error_code(await server.call_tool(name, arguments)) == "WORKFLOW_DISABLED"

    asyncio.run(check())


def test_existing_authoring_disabled_boundary_still_applies(tmp_path):
    async def check():
        server = make_server(tmp_path)
        for name in ("forge_render_sprite", "forge_edit_sprite"):
            assert error_code(await server.call_tool(name, {})) == "AUTHORING_DISABLED"

    asyncio.run(check())


@pytest.mark.parametrize("name,arguments", [
    ("forge_prepare", {"request_id": "schema-test", "prompt": "cabinet", "width": 32,
                       "height": 48, "routes": ["rd"]}),
    ("forge_prepare", {"request_id": "schema-test", "prompt": "cabinet", "width": "32",
                       "height": 48}),
    ("forge_prepare", {"request_id": "schema-test", "prompt": "cabinet", "width": True,
                       "height": 48}),
    ("forge_prepare", {"request_id": "schema-test", "prompt": "cabinet", "width": 32,
                       "height": 48, "project": "unapproved-game"}),
    ("forge_prepare", {"request_id": "schema-test", "prompt": "cabinet", "width": 32,
                       "height": 48, "source_asset": "../../outside.png"}),
    ("forge_prepare", {"request_id": "schema-test", "prompt": "cabinet", "width": 32,
                       "height": 48, "checks": "a string is not a checklist"}),
    ("forge_repair", {"request_id": "schema-test", "library_id": "cabinet",
                      "instructions": "fix one pixel", "command": "anything"}),
    ("forge_repair", {"request_id": "schema-test", "library_id": "../../outside",
                      "instructions": "fix one pixel"}),
    ("forge_repair", {"request_id": "schema-test", "library_id": "cabinet",
                      "instructions": "fix one pixel", "max_changed_pixels": True}),
    ("forge_open_aseprite", {"request_id": "schema-test", "library_id": "cabinet",
                            "path": "/tmp/unapproved.aseprite"}),
    ("forge_import_aseprite", {"request_id": "schema-test", "library_id": "cabinet",
                              "document_id": "document", "out": "/tmp/export.png"}),
    ("forge_import_aseprite", {"request_id": "schema-test", "library_id": "cabinet",
                              "document_id": "../../unapproved.aseprite"}),
])
def test_enabled_workflow_validates_strictly_before_spawning(tmp_path, monkeypatch, name, arguments):
    import threading
    from forge import artist

    def forbidden(*args, **kwargs):
        pytest.fail("invalid workflow arguments spawned a job or invoked a model")

    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(artist, "paint_with_gpt_image", forbidden)
    monkeypatch.setattr(artist, "make_backend", forbidden)

    async def check():
        server = make_server(tmp_path, enabled=True)
        assert error_code(await server.call_tool(name, arguments)) == "INVALID_REQUEST"

    asyncio.run(check())


@pytest.mark.parametrize("arguments", [{}, {"request_id": "schema-test", "unexpected": True},
                                       {"request_id": "../../outside"}])
def test_status_requires_a_strict_request_identifier_even_when_writes_disabled(tmp_path, arguments):
    async def check():
        server = make_server(tmp_path)
        assert error_code(await server.call_tool("forge_workflow_status", arguments)) == "INVALID_REQUEST"

    asyncio.run(check())
