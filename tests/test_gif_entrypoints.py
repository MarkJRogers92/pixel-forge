import asyncio
import json
import subprocess
import sys

import pytest
from PIL import Image


def source(tmp_path):
    path = tmp_path / 'input.gif'
    a, b = Image.new('RGB', (12, 10), 'red'), Image.new('RGB', (12, 10), 'blue')
    a.save(path, save_all=True, append_images=[b], duration=[80, 230], loop=2)
    return path


def test_cli_import_accepts_downloaded_chatgpt_gif_without_credentials(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'forge', 'gif-import', str(source(tmp_path)),
                             '-o', str(tmp_path / 'clip')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['durations_ms'] == [80, 230]
    assert report['gif_loop_count'] == 2
    assert (tmp_path / 'clip/animation.gif').exists()


def test_cli_generate_defaults_to_offline_plan(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'forge', 'gif-generate', 'sprite recoils',
                             '-o', str(tmp_path / 'generated')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan['status'] == 'preview_only'
    assert plan['request']['output_format'] == 'gif'
    assert plan['paid_request_sent'] is False
    assert not (tmp_path / 'generated').exists()


def test_full_mcp_import_exposes_local_tool_without_restricted_scope_expansion(tmp_path):
    from forge.mcp_server import server
    from forge.workflow_mcp import MODELS
    async def check():
        tools = {t.name: t for t in await server.list_tools()}
        assert 'forge_gif_import' in tools
        assert tools['forge_gif_import'].annotations.open_world_hint is False
        result = await server.call_tool('forge_gif_import', {'source': str(source(tmp_path)),
                                       'out_dir': str(tmp_path / 'out')})
        assert not result.is_error
        assert [c.type for c in result.content] == ['text', 'image', 'image']
        assert json.loads(result.content[0].text)['verified']['gif_preview']
    asyncio.run(check())
    assert 'forge_gif_import' not in MODELS
    assert 'forge_gif_generate' not in MODELS


def test_generation_bridge_keeps_raw_result_on_import_failure(tmp_path, monkeypatch):
    from forge import gif_workflow, gif_provider
    def generate(prompt, out, **kwargs):
        assert kwargs['allow_paid'] is True
        out.mkdir()
        path = out / 'source.gif'
        path.write_bytes(b'not a valid gif')
        return {'gif_path': str(path), 'metadata': {'provider': 'openai'}, 'metadata_path': str(out/'provider.json')}
    monkeypatch.setattr(gif_provider, 'generate', generate)
    result = gif_workflow.generate_clip('recoil', tmp_path / 'generated', execute_paid=True)
    assert result['status'] == 'needs_import_options'
    assert 'cannot' in result['import_error'] or 'GIF' in result['import_error']
    assert (tmp_path / 'generated/source.gif').exists()


def test_successful_generation_bridge_records_provenance_without_more_provider_calls(tmp_path, monkeypatch):
    from forge import gif_workflow, gif_provider
    path = source(tmp_path)
    calls = []
    def generate(prompt, out, **kwargs):
        calls.append(prompt)
        out.mkdir()
        (out / 'source.gif').write_bytes(path.read_bytes())
        return {'gif_path': str(out / 'source.gif'), 'metadata': {'provider': 'openai', 'model': 'test'},
                'metadata_path': str(out/'provider.json')}
    monkeypatch.setattr(gif_provider, 'generate', generate)
    result = gif_workflow.generate_clip('recoil', tmp_path / 'generated', execute_paid=True)
    assert calls == ['recoil']
    assert result['status'] == 'awaiting_visual_review'
    exported = json.loads((tmp_path / 'generated/clip/manifest.json').read_text())
    assert exported['generation']['provider'] == 'openai'


@pytest.mark.parametrize('kwargs', [{'width': 0}, {'height': True}, {'palette': []},
                                  {'tag': ''}, {'palette': ['#fff'], 'profile': 'dead-mall'}])
def test_known_local_options_fail_before_generation(tmp_path, monkeypatch, kwargs):
    from forge import gif_workflow, gif_provider
    def forbidden(*a, **kw):
        pytest.fail('provider must not be invoked')
    monkeypatch.setattr(gif_provider, 'generate', forbidden)
    with pytest.raises(ValueError):
        gif_workflow.generate_clip('recoil', tmp_path / 'generated', execute_paid=True, **kwargs)


def test_direct_mcp_module_registers_gif_import_before_serving():
    from pathlib import Path
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def check():
        params = StdioServerParameters(command=sys.executable, cwd=Path(__file__).resolve().parents[1],
                                       args=['-m', 'forge.mcp_server'])
        async with stdio_client(params) as streams, ClientSession(*streams, read_timeout_seconds=15) as client:
            await client.initialize()
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert 'forge_gif_import' in names
    asyncio.run(check())


def test_cli_background_removal_is_opt_in_and_reported(tmp_path):
    path = source(tmp_path)
    result = subprocess.run([sys.executable, '-m', 'forge', 'gif-import', str(path),
                             '-o', str(tmp_path / 'keyed'), '--background-color', '#ff0000'],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['background_removal']['removed_pixels'] == [120, 0]
    assert report['frames'][0]['output_bbox'] is None
    assert report['durations_ms'] == [80, 230]


def test_mcp_background_options_reach_local_importer(tmp_path):
    from forge.mcp_server import server
    async def check():
        result = await server.call_tool('forge_gif_import', {
            'source': str(source(tmp_path)), 'out_dir': str(tmp_path / 'keyed'),
            'background_color': '#ff0000', 'background_tolerance': 0})
        assert not result.is_error
        report = json.loads(result.content[0].text)
        assert report['background_removal']['removed_pixels'] == [120, 0]
    asyncio.run(check())


def test_cli_source_palette_reduction_does_not_require_game_profile(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'forge', 'gif-import', str(source(tmp_path)),
                             '-o', str(tmp_path / 'reduced'), '--colors', '1'],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['palette']['mode'] == 'source_reduced'
    assert len(report['palette']['rgba']) == 1


def test_mcp_source_palette_reduction_reaches_importer(tmp_path):
    from forge.mcp_server import server
    async def check():
        result = await server.call_tool('forge_gif_import', {
            'source': str(source(tmp_path)), 'out_dir': str(tmp_path / 'reduced'), 'colors': 1})
        assert not result.is_error
        report = json.loads(result.content[0].text)
        assert report['palette']['mode'] == 'source_reduced'
        assert len(report['palette']['rgba']) == 1
    asyncio.run(check())
