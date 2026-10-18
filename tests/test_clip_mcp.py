"""Clip tools preserve ordering and the restricted workflow's library boundary."""
import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import time

import numpy as np
import pytest
from PIL import Image

from forge import library
from forge.authoring_store import AuthoringStore
from forge.readonly_mcp import ReadOnlyForge
from forge.workflow_jobs import WorkflowJobs, JobError, public_result
from forge.workflow_mcp import MODELS, WorkflowMCP
from forge.workflow_worker import execute


def source(root, item_id, xy=(3, 6), color=(240, 20, 80, 255), size=(12, 10)):
    folder = root / 'library' / item_id
    folder.mkdir(parents=True)
    image = Image.new('RGBA', size)
    for y in range(xy[1], xy[1] + 2):
        for x in range(xy[0], xy[0] + 2):
            image.putpixel((x, y), color)
    path = folder / 'sprite.png'
    image.save(path)
    return path


def args(**changes):
    return {'request_id': 'clip-job', 'source_library_ids': ['z-rest', 'a-jump', 'z-rest'],
            'width': 36, 'height': 20, 'fps': 12.0, 'loop': 'pingpong', 'tag': 'recoil', **changes}


def pixels(path):
    with Image.open(path) as image:
        return np.asarray(image.convert('RGBA'))


def server(root, enabled=True):
    store = ReadOnlyForge(root, {})
    return WorkflowMCP(AuthoringStore(store), WorkflowJobs(store, enabled=enabled))


def validated(**changes):
    assert 'forge_clip' in MODELS
    return MODELS['forge_clip'](**args(**changes)).model_dump()


def test_full_mcp_clip_preserves_order_transform_and_both_review_scales(tmp_path):
    from forge.mcp_server import server as full
    a = source(tmp_path, 'z-rest')
    b = source(tmp_path, 'a-jump', xy=(5, 2))
    out = tmp_path / 'output'

    async def check():
        tools = {tool.name: tool for tool in await full.list_tools()}
        assert 'forge_clip' in tools
        annotation = tools['forge_clip'].annotations
        assert annotation.read_only_hint is False
        assert annotation.destructive_hint is False
        assert annotation.idempotent_hint is False
        assert annotation.open_world_hint is False
        result = await full.call_tool('forge_clip', {
            'frames': [str(a), str(b), str(a)], 'out_dir': str(out),
            'width': 36, 'height': 20, 'fps': 12, 'loop': 'pingpong', 'tag': 'recoil'})
        assert not result.is_error
        assert [part.type for part in result.content] == ['text', 'image', 'image']
        report = json.loads(result.content[0].text)
        assert report['verified']['ok']
        assert report['frame_count'] == 3
        assert report['endpoints']['equal']
        assert report['frames'][1]['output_bbox'] == [16, 4, 19, 7]
        assert report['review']['status'] == 'awaiting_visual_review'
        for part, filename in zip(result.content[1:], ['sheet.png', 'sheet@2x.png']):
            assert base64.b64decode(part.data) == (out / filename).read_bytes()
        assert np.array_equal(pixels(out / 'frames/f0.png'), pixels(out / 'frames/f2.png'))

    asyncio.run(check())


@pytest.mark.parametrize('changes', [
    {'source_library_ids': []}, {'source_library_ids': ['a'] * 257},
    {'source_library_ids': ['../secret']}, {'source_library_ids': ['/tmp/private.png']},
    {'source_library_ids': 'z-rest'}, {'source_library_ids': [True]},
    {'frames': ['/tmp/private.png']}, {'out_dir': '/tmp/output'},
    {'profile': '../../unapproved'}, {'model': 'anything'}, {'routes': ['rd']},
    {'width': True}, {'height': '20'}, {'width': 513}, {'height': 0},
    {'fps': True}, {'fps': float('nan')}, {'fps': float('inf')}, {'fps': 0},
    {'loop': 'unknown'}, {'tag': '   '}, {'tag': 'a' * 121},
    {'palette': []}, {'palette': ['#fff'] * 63},
    {'palette': ['#ffffff00']}, {'palette': ['not-a-colour']},
])
def test_restricted_clip_rejects_invalid_arguments_before_launch(tmp_path, monkeypatch, changes):
    def forbidden(*a, **kw):
        pytest.fail('invalid arguments launched a worker')
    monkeypatch.setattr('forge.workflow_jobs._popen', forbidden)

    async def check():
        result = await server(tmp_path).call_tool('forge_clip', args(**changes))
        assert result.is_error
        expected = 'LIMIT_EXCEEDED' if (changes.get('width') == 513
            or len(changes.get('source_library_ids', [])) > 256
            or len(changes.get('tag', '')) > 120 or len(changes.get('palette', [])) > 62) else 'INVALID_REQUEST'
        assert json.loads(result.content[0].text)['error']['code'] == expected
    asyncio.run(check())


def test_restricted_clip_export_is_new_bounded_and_path_free(tmp_path, monkeypatch):
    from forge import artist, aseprite, rd_fixer
    def forbidden(*a, **kw):
        pytest.fail('local clip import called a provider or editor')
    monkeypatch.setattr(artist, 'paint_with_gpt_image', forbidden)
    monkeypatch.setattr(artist, 'make_backend', forbidden)
    monkeypatch.setattr(rd_fixer, 'neural_fixer', forbidden)
    monkeypatch.setattr(aseprite, 'build_document', forbidden)
    a = source(tmp_path, 'z-rest', color=(240, 20, 80, 96))
    b = source(tmp_path, 'a-jump', xy=(5, 2))
    originals = [a.read_bytes(), b.read_bytes()]
    jobs = server(tmp_path).jobs
    result = execute(jobs, 'forge_clip', validated(palette=['#ff0000', '#0000ff']))
    out = tmp_path / 'library' / result['library_id']
    assert result['library_id'] not in ('z-rest', 'a-jump')
    assert result['source_library_ids'] == ['z-rest', 'a-jump', 'z-rest']
    assert result['verified']['ok']
    assert result['transform']['offset'] == [6, 0]
    assert result['frames'][1]['output_bbox'] == [16, 4, 19, 7]
    assert result['palette']['mode'] == 'fixed'
    assert result['status'] == 'awaiting_visual_review'
    assert result['review']['status'] == 'awaiting_visual_review'
    assert [a.read_bytes(), b.read_bytes()] == originals
    assert pixels(out / 'frames/f0.png')[12, 12].tolist() == [255, 0, 0, 96]
    assert np.array_equal(pixels(out / 'frames/f0.png'), pixels(out / 'frames/f2.png'))
    assert str(tmp_path) not in json.dumps(public_result(result))
    assert 'folder' not in result
    assert all('path' not in entry for entry in result['sources'])
    assert jobs.review_image({'result': result}) == (out / 'sheet@2x.png').read_bytes()
    meta = json.loads((out / 'meta.json').read_text())
    assert meta['kind'] == 'clip' and meta['status'] == 'awaiting_visual_review'
    assert any(item['id'] == result['library_id'] for item in jobs.store.library())


@pytest.mark.parametrize('kind', ['missing', 'symlink', 'hardlink', 'directory-symlink', 'root-symlink'])
def test_restricted_clip_rejects_unapproved_source_before_spawning(tmp_path, monkeypatch, kind):
    source(tmp_path, 'z-rest')
    target = source(tmp_path, 'a-jump')
    outside = tmp_path / 'private.png'
    outside.write_bytes(target.read_bytes())
    if kind == 'directory-symlink':
        target.parent.rename(tmp_path / 'private-dir')
        target.parent.symlink_to(tmp_path / 'private-dir')
    elif kind == 'root-symlink':
        (tmp_path / 'library').rename(tmp_path / 'private-library')
        (tmp_path / 'library').symlink_to(tmp_path / 'private-library')
    else:
        target.unlink()
        if kind == 'symlink':
            target.symlink_to(outside)
        elif kind == 'hardlink':
            os.link(outside, target)
    def forbidden(*a, **kw):
        pytest.fail('unapproved source launched a worker')
    monkeypatch.setattr('forge.workflow_jobs._popen', forbidden)
    jobs = server(tmp_path).jobs
    with pytest.raises((JobError, ValueError)):
        jobs.submit('forge_clip', validated())
    assert not list((tmp_path / 'library').glob('.workflow-jobs/*/request.json'))


def test_worker_rechecks_sources_and_refuses_output_collision(tmp_path, monkeypatch):
    source(tmp_path, 'z-rest')
    target = source(tmp_path, 'a-jump')
    jobs = server(tmp_path).jobs
    request = validated()
    outside = tmp_path / 'private.png'
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        execute(jobs, 'forge_clip', request)
    target.unlink()
    target.write_bytes(outside.read_bytes())
    monkeypatch.setattr(library, 'allocate_id', lambda _: 'z-rest')
    original = (tmp_path / 'library/z-rest/sprite.png').read_bytes()
    with pytest.raises(ValueError, match='exists'):
        execute(jobs, 'forge_clip', request)
    assert (tmp_path / 'library/z-rest/sprite.png').read_bytes() == original


def test_restricted_clip_rejects_sheet_exceeding_existing_review_budget(tmp_path, monkeypatch):
    source(tmp_path, 'z-rest')
    jobs = server(tmp_path).jobs
    def forbidden(*a, **kw):
        pytest.fail('oversized review launched a worker')
    monkeypatch.setattr('forge.workflow_jobs._popen', forbidden)
    with pytest.raises(JobError, match='LIMIT_EXCEEDED'):
        jobs.submit('forge_clip', validated(source_library_ids=['z-rest'] * 9, width=512, height=512))
    assert not jobs._folder('clip-job').exists()


def test_real_clip_workflow_job_status_deduplication_and_review(tmp_path, monkeypatch):
    source(tmp_path, 'z-rest')
    source(tmp_path, 'a-jump', xy=(5, 2))
    original_popen = subprocess.Popen
    launched = []

    def local_worker(_argv, **kwargs):
        code = (
            'import sys\nfrom pathlib import Path\n'
            'from forge import workflow_worker, readonly_mcp\n'
            f'readonly_mcp.store = readonly_mcp.ReadOnlyForge(Path({str(tmp_path)!r}), {{}})\n'
            f'sys.argv = ["forge.workflow_worker", {_argv[-1]!r}]\n'
            'workflow_worker.main()\n')
        process = original_popen([sys.executable, '-c', code], **kwargs)
        launched.append(process)
        return process
    monkeypatch.setattr('forge.workflow_jobs._popen', local_worker)

    async def check():
        service = server(tmp_path)
        tools = {tool.name: tool for tool in await service.list_tools()}
        assert 'forge_clip' in tools
        assert tools['forge_clip'].annotations.open_world_hint is False
        first = await service.call_tool('forge_clip', args())
        assert not first.is_error
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = await service.call_tool('forge_workflow_status', {'request_id': 'clip-job'})
            data = json.loads(result.content[0].text)
            if data['state'] in ('completed', 'failed', 'interrupted'):
                break
            await asyncio.sleep(.02)
        assert data['state'] == 'completed', data
        assert [part.type for part in result.content] == ['text', 'image']
        assert data['result']['frame_count'] == 3
        assert data['result']['verified']['ok']
        assert str(tmp_path) not in json.dumps(data)
        repeated = await server(tmp_path).call_tool('forge_clip', args())
        assert json.loads(repeated.content[0].text) == data
        assert len(launched) == 1
        conflict = await service.call_tool('forge_clip', args(source_library_ids=['a-jump', 'z-rest']))
        assert json.loads(conflict.content[0].text)['error']['code'] == 'REQUEST_CONFLICT'
        with Image.open(io.BytesIO(base64.b64decode(result.content[1].data))) as review:
            assert review.size == (144, 80)
    try:
        asyncio.run(check())
    finally:
        for process in launched:
            process.wait(timeout=5)


@pytest.mark.parametrize('changes', [
    {'width': True}, {'height': '20'}, {'fps': True}, {'fps': '12'},
    {'fps': 0}, {'loop': 'unknown'}, {'frames': []}, {'palette': ['#ffffff00']},
])
def test_full_mcp_clip_rejects_invalid_arguments_without_outputs(tmp_path, changes):
    from forge.mcp_server import server as full
    a = source(tmp_path, 'frame', size=(2, 2), xy=(0, 0))
    out = tmp_path / 'output'
    async def check():
        from mcp.server.mcpserver.exceptions import ToolError
        with pytest.raises(ToolError):
            await full.call_tool('forge_clip', {
                'frames': [str(a)], 'out_dir': str(out), **changes})
        assert not out.exists()
    asyncio.run(check())


def test_restricted_clip_checks_equal_canvas_before_launch(tmp_path, monkeypatch):
    source(tmp_path, 'z-rest')
    source(tmp_path, 'a-jump', size=(16, 16))
    def forbidden(*a, **kw):
        pytest.fail('different canvas sizes launched a worker')
    monkeypatch.setattr('forge.workflow_jobs._popen', forbidden)
    with pytest.raises(JobError, match='CANVAS_MISMATCH'):
        server(tmp_path).jobs.submit('forge_clip', validated())
