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

    monkeypatch.setattr('forge.workflow_jobs._popen', Process)
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

    monkeypatch.setattr('forge.workflow_jobs._popen', Process)
    jobs.submit('forge_prepare', arguments())
    folder = jobs._folder('test-job')
    state = json.loads((folder / 'state.json').read_text())
    write_json(folder / 'state.json', {**state, 'started_at': time.time() - 60})
    assert jobs.status('test-job')['state'] == 'interrupted'
    assert jobs.submit('forge_prepare', arguments())['state'] == 'interrupted'
    assert jobs.submit('forge_prepare', arguments('different-job'))['state'] == 'starting'


def test_live_job_returns_busy_without_starting_another(jobs, monkeypatch):
    import threading
    release = threading.Event()
    class Process:
        def __init__(self, *args, **kwargs):
            self.pid = 123456
        def wait(self):
            release.wait(2)
            return 0
    monkeypatch.setattr('forge.workflow_jobs._popen', Process)
    jobs.submit('forge_prepare', arguments())
    with pytest.raises(JobError, match='BUSY'):
        jobs.submit('forge_prepare', arguments('different-job'))
    release.set()


def test_cancel_is_idempotent_and_waits_for_owned_worker_exit(jobs, monkeypatch):
    import threading
    released = threading.Event()

    class Process:
        pid = 123456
        def wait(self):
            released.wait(2)
            return 0
        def poll(self):
            return None if not released.is_set() else 0

    process = Process()
    monkeypatch.setattr('forge.workflow_jobs._popen', lambda *a, **kw: process)
    monkeypatch.setattr(jobs, '_signal_owned_group', lambda pid, sig: released.set())
    jobs.submit('forge_prepare', arguments())
    first = jobs.cancel('test-job')
    second = jobs.cancel('test-job')
    assert first['state'] in ('cancelling', 'cancelled')
    assert second['state'] in ('cancelling', 'cancelled')
    deadline = time.time() + 2
    while jobs.status('test-job')['state'] != 'cancelled' and time.time() < deadline:
        time.sleep(.01)
    assert jobs.status('test-job')['state'] == 'cancelled'
    assert jobs.submit('forge_prepare', arguments())['state'] == 'cancelled'


def test_cancel_stops_only_its_real_local_process_group_before_busy_releases(jobs, monkeypatch):
    import subprocess
    import sys
    original_popen = subprocess.Popen
    launched = []

    def dummy_worker(_argv, **kwargs):
        process = original_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr('forge.workflow_jobs._popen', dummy_worker)
    jobs.submit('forge_prepare', arguments())
    process = launched[0]
    result = jobs.cancel('test-job')
    assert result['state'] in ('cancelling', 'cancelled')
    deadline = time.time() + 2
    while jobs.status('test-job')['state'] != 'cancelled' and time.time() < deadline:
        time.sleep(.01)
    assert process.poll() is not None
    assert jobs.status('test-job')['state'] == 'cancelled'
    assert jobs.submit('forge_prepare', arguments('next-job'))['state'] == 'starting'
    jobs.cancel('next-job')


def test_cancel_kills_sigterm_ignoring_child_before_marking_cancelled(jobs, monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    child_pid_file = tmp_path / 'child.pid'
    child_script = tmp_path / 'child.py'
    child_script.write_text(
        'import os, signal, sys, time\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        'open(sys.argv[1], "w").write(str(os.getpid()))\n'
        'time.sleep(30)\n')
    worker_script = tmp_path / 'dummy_worker.py'
    worker_script.write_text(
        'import subprocess, sys, time\n'
        'subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n'
        'time.sleep(30)\n')
    original_popen = subprocess.Popen
    launched = []

    def dummy_worker(_argv, **kwargs):
        process = original_popen([sys.executable, str(worker_script),
                                  str(child_script), str(child_pid_file)], **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr('forge.workflow_jobs._popen', dummy_worker)
    jobs.submit('forge_prepare', arguments())
    deadline = time.time() + 3
    while not child_pid_file.exists() and time.time() < deadline:
        time.sleep(.01)
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text())
    result = jobs.cancel('test-job')
    assert result['state'] == 'cancelling'
    assert jobs.status('test-job')['state'] == 'cancelling'

    def child_running():
        return WorkflowJobs._process_state(child_pid) not in (None, 'Z')

    deadline = time.time() + 5
    while (jobs.status('test-job')['state'] != 'cancelled' or child_running()) and time.time() < deadline:
        time.sleep(.02)
    assert not child_running()
    assert jobs.status('test-job')['state'] == 'cancelled'
    assert launched[0].poll() is not None


def test_cancel_racing_worker_start_keeps_reservation_and_never_replays(jobs, monkeypatch):
    import threading
    constructed = threading.Event()
    release_constructor = threading.Event()
    starts = []

    class Process:
        pid = 123456
        def __init__(self, *args, **kwargs):
            starts.append(1)
            constructed.set()
            release_constructor.wait(2)
        def wait(self):
            return 0
        def poll(self):
            return 0

    monkeypatch.setattr('forge.workflow_jobs._popen', Process)
    launch = threading.Thread(target=jobs.submit, args=('forge_prepare', arguments()))
    launch.start()
    assert constructed.wait(2)
    assert jobs.cancel('test-job')['state'] == 'cancelling'
    release_constructor.set()
    launch.join(2)
    deadline = time.time() + 2
    while jobs.status('test-job')['state'] != 'cancelled' and time.time() < deadline:
        time.sleep(.01)
    assert jobs.status('test-job')['state'] == 'cancelled'
    assert jobs.submit('forge_prepare', arguments())['state'] == 'cancelled'
    assert len(starts) == 1


def test_second_jobs_instance_cancels_worker_through_durable_marker(jobs, tmp_path):
    import hashlib
    import os
    import signal
    import subprocess
    import sys
    import time
    from forge.authoring_schema import canonical

    started = tmp_path / 'worker-started'
    request_id = 'cross-instance-cancel'
    args = arguments(request_id)
    request = {'tool': 'forge_prepare', 'arguments': args}
    folder = jobs._folder(request_id)
    folder.mkdir(parents=True)
    write_json(folder / 'request.json', {**request,
        'request_hash': hashlib.sha256(canonical(request)).hexdigest()})
    write_json(folder / 'state.json', {'request_id': request_id, 'state': 'starting',
        'stage': 'Starting workflow', 'log': ['Starting workflow'], 'previews': []})
    project_root = str(jobs.store.repo)
    started_path = str(started)
    code = f'''import sys, time
from forge import workflow_worker
from forge.readonly_mcp import ReadOnlyForge
workflow_worker.readonly_mcp.store = ReadOnlyForge(__import__('pathlib').Path({project_root!r}), {{}})
def fake_prepare(*args, progress=None, **kwargs):
    open({started_path!r}, 'w').write('ready')
    progress('starting', 'temporary-item')
    while True: time.sleep(.05)
workflow_worker.fixer_workflow.prepare = fake_prepare
sys.argv = ['forge.workflow_worker', {request_id!r}]
workflow_worker.main()
'''
    process = subprocess.Popen([sys.executable, '-c', code],
        cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, start_new_session=True)
    with jobs._locked(folder):
        current = json.loads((folder / 'state.json').read_text())
        write_json(folder / 'state.json', {**current, 'pid': process.pid})
    try:
        # Imports can take several seconds under a loaded full-suite run. Check
        # for an actual worker exit while waiting, rather than blocking on
        # communicate() when a live stub has not reached its readiness marker.
        deadline = time.monotonic() + 20
        while not started.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        if not started.exists():
            if process.poll() is not None:
                stderr = process.communicate(timeout=2)[1].decode(errors='replace')
                pytest.fail(f'worker test harness exited before its provider stub: {stderr}')
            pytest.fail('worker test harness did not reach its provider stub within 20 seconds')

        other_jobs_instance = WorkflowJobs(jobs.store, enabled=True)
        assert other_jobs_instance.cancel(request_id)['state'] == 'cancelling'
        deadline = time.monotonic() + 5
        while jobs.status(request_id)['state'] != 'cancelled' and time.monotonic() < deadline:
            time.sleep(.02)
        assert jobs.status(request_id)['state'] == 'cancelled'
        assert process.wait(timeout=2) is not None
    finally:
        # This Popen owns a fresh session. On failed assertions, clean only its
        # live process group so a blocked readiness wait cannot leak the stub.
        if process.poll() is None:
            try:
                if os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    if os.getpgid(process.pid) == process.pid:
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)


def test_completion_wins_cancel_race_and_preview_whitelist_denies_paths(jobs):
    folder = jobs._folder('test-job')
    folder.mkdir(parents=True)
    write_json(folder / 'state.json', {'request_id': 'test-job', 'state': 'completed'})
    write_json(folder / 'result.json', {'request_id': 'test-job', 'state': 'completed',
                                        'result': {'library_id': 'known'}})
    assert jobs.cancel('test-job')['state'] == 'completed'
    assert not (folder / 'cancel.requested').exists()
    with pytest.raises(JobError, match='ASSET_NOT_FOUND'):
        jobs.preview('test-job', '../outside.png')


def test_progress_persists_safe_stage_and_only_existing_previews(jobs):
    folder = jobs._folder('test-job')
    folder.mkdir(parents=True)
    write_json(folder / 'state.json', {'request_id': 'test-job', 'state': 'running'})
    item = jobs.store.repo / 'library' / 'known'
    (item / 'work').mkdir(parents=True)
    Image.new('RGBA', (16, 16)).save(item / 'work' / 'rd_neural.png')
    jobs.progress('test-job', 'rd_neural_fixer', 'known')
    status = jobs.status('test-job')
    assert status['stage'] == 'Running free neural fixer'
    assert status['library_id'] == 'known'
    assert status['previews'] == [{'kind': 'Neural fixer',
        'png': '/api/workflows/test-job/preview/rd_neural.png'}]


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
