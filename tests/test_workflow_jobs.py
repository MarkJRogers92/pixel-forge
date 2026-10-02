import json
import time
from pathlib import Path

import pytest
from PIL import Image

from forge.readonly_mcp import ReadOnlyForge
from forge.workflow_jobs import WorkflowJobs, JobError, write_json
from forge.workflow_mcp import Prepare
from forge.workflow_worker import validate_work_files


@pytest.fixture
def jobs(tmp_path):
    (tmp_path / 'library').mkdir()
    return WorkflowJobs(ReadOnlyForge(tmp_path, {}), enabled=True)


def arguments(request_id='test-job'):
    return Prepare(request_id=request_id, prompt='test cabinet', width=32, height=48).model_dump()


def test_repeated_request_returns_same_job_without_relaunch(jobs, monkeypatch):
    starts = []

    class Process:
        def __init__(self, *args, **kwargs):
            starts.append(args)
        def wait(self):
            return 0

    monkeypatch.setattr('forge.workflow_jobs.subprocess.Popen', Process)
    first = jobs.submit('forge_prepare', arguments())
    second = jobs.submit('forge_prepare', arguments())
    assert first['request_id'] == second['request_id'] and len(starts) == 1
    with pytest.raises(JobError, match='REQUEST_CONFLICT'):
        jobs.submit('forge_prepare', {**arguments(), 'prompt': 'changed'})
    assert len(starts) == 1


def test_start_reservation_expires_without_replaying_it(jobs, monkeypatch):
    class Process:
        def __init__(self, *args, **kwargs):
            pass
        def wait(self):
            return 0

    monkeypatch.setattr('forge.workflow_jobs.subprocess.Popen', Process)
    jobs.submit('forge_prepare', arguments())
    folder = jobs._folder('test-job')
    state = json.loads((folder / 'state.json').read_text())
    write_json(folder / 'state.json', {**state, 'started_at': time.time() - 60})
    assert jobs.status('test-job')['state'] == 'interrupted'
    assert jobs.submit('forge_prepare', arguments())['state'] == 'interrupted'
    assert jobs.submit('forge_prepare', arguments('different-job'))['state'] == 'starting'


def test_live_job_returns_busy_without_starting_another(jobs, monkeypatch):
    class Process:
        def __init__(self, *args, **kwargs):
            pass
        def wait(self):
            return 0
    monkeypatch.setattr('forge.workflow_jobs.subprocess.Popen', Process)
    jobs.submit('forge_prepare', arguments())
    with pytest.raises(JobError, match='BUSY'):
        jobs.submit('forge_prepare', arguments('different-job'))


def test_no_review_image_is_valid_for_existing_direct_art(jobs):
    item = jobs.store.repo / 'library' / 'existing-art'
    item.mkdir()
    Image.new('RGBA', (16, 16)).save(item / 'sprite.png')
    assert jobs.review_image({'result': {'library_id': 'existing-art'}}) is None


def test_hidden_layer_symlink_is_rejected(jobs, tmp_path):
    item = jobs.store.repo / 'library' / 'existing-art'
    (item / 'work').mkdir(parents=True)
    Image.new('RGBA', (16, 16)).save(item / 'sprite.png')
    outside = tmp_path / 'private.png'
    Image.new('RGBA', (16, 16)).save(outside)
    (item / 'work' / 'cleaned.png').symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        validate_work_files(jobs, 'existing-art')
