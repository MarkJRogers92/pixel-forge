"""Durable, deduplicated workflow jobs; no user-selected commands or filesystem roots."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

from . import readonly_mcp
from .authoring_schema import canonical

_popen = subprocess.Popen


class JobError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def write_json(path: Path, value: dict):
    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def public_result(value):
    omitted = {'png', 'preview', 'sheet', 'aseprite_command', 'exported', 'aseprite', 'opened', 'source', 'log'}
    if isinstance(value, dict):
        return {key: public_result(child) for key, child in value.items() if key not in omitted}
    if isinstance(value, list):
        return [public_result(child) for child in value]
    return value


class WorkflowJobs:
    PREVIEWS = {
        'gpt_reference.png': ('work/gpt_reference.png', 'GPT reference'),
        'rd_neural.png': ('work/rd_neural.png', 'Neural fixer'),
        'cleaned.png': ('work/cleaned.png', 'Cleaned sprite'),
        'sprite.png': ('sprite.png', 'Final sprite'),
        'review.png': ('review.png', 'Visual review'),
    }
    STAGES = {
        'starting': 'Starting workflow', 'gpt_image': 'Creating GPT reference',
        'rd_neural_fixer': 'Running free neural fixer', 'cleanup': 'Cleaning and checking sprite',
        'review': 'Preparing visual review', 'awaiting_visual_review': 'Ready for visual review',
    }

    def __init__(self, store, enabled=False):
        self.store, self.enabled = store, enabled
        self.root = store.repo / 'library' / '.workflow-jobs'
        self._processes = {}
        self._processes_lock = threading.Lock()

    @contextmanager
    def _locked(self, folder):
        lockpath = readonly_mcp._safe_path(folder, 'job.lock')
        with lockpath.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    @staticmethod
    def _owned_group_members(group_id):
        """List members of a worker's process group; None means ownership is unknown."""
        members = {}
        proc = Path('/proc')
        if proc.is_dir():
            for entry in proc.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                    state, group = fields[0], int(fields[2])
                    if group == group_id:
                        members[int(entry.name)] = state
                except (OSError, ValueError, IndexError):
                    continue
            return members
        try:
            result = subprocess.run(['ps', '-Ao', 'pid=,pgid=,stat='],
                capture_output=True, text=True, timeout=1, check=False)
            if result.returncode:
                return None
            for line in result.stdout.splitlines():
                fields = line.split(None, 2)
                if len(fields) == 3 and int(fields[1]) == group_id:
                    pid = int(fields[0])
                    try:
                        if os.getsid(pid) == group_id:
                            members[pid] = fields[2]
                    except ProcessLookupError:
                        continue
            return members
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    @classmethod
    def _signal_owned_group(cls, group_id, sig):
        members = cls._owned_group_members(group_id)
        # Callers retain the live, unreaped session leader or are running inside the group.
        if not members:
            return False
        try:
            os.killpg(group_id, sig)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    @staticmethod
    def _process_state(pid):
        """Read a child state without reaping it, so its group ID stays reserved."""
        proc_stat = Path(f'/proc/{pid}/stat')
        if proc_stat.exists():
            try:
                return proc_stat.read_text().rsplit(')', 1)[1].split()[0]
            except (OSError, IndexError):
                return None
        try:
            result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                capture_output=True, text=True, timeout=1, check=False)
            return result.stdout.strip().split()[0] if result.returncode == 0 and result.stdout.strip() else None
        except (OSError, subprocess.SubprocessError):
            return None

    def item_file(self, item_id, asset='sprite.png'):
        if not isinstance(item_id, str) or not readonly_mcp.ID_PATTERN.fullmatch(item_id):
            raise JobError('PATH_DENIED')
        library = readonly_mcp._safe_path(self.store.repo, 'library')
        path = readonly_mcp._safe_path(library, f'{item_id}/{asset}')
        if not path.is_file() or path.stat().st_nlink != 1:
            raise JobError('ASSET_NOT_FOUND')
        return path

    def _folder(self, request_id):
        if not isinstance(request_id, str) or not readonly_mcp.ID_PATTERN.fullmatch(request_id) or len(request_id) > 128:
            raise JobError('INVALID_REQUEST')
        return readonly_mcp._safe_path(self.store.repo, 'library/.workflow-jobs/' + hashlib.sha256(request_id.encode()).hexdigest())

    def status(self, request_id):
        folder = self._folder(request_id)
        if not folder.exists():
            raise JobError('JOB_NOT_FOUND')
        with self._locked(folder):
            filename = 'result.json' if (folder / 'result.json').exists() else 'state.json'
            data = readonly_mcp._json_file(folder, filename)
        if data.get('state') == 'starting' and time.time() - data.get('started_at', 0) > 30:
            data = {**data, 'state': 'interrupted', 'error': {'code': 'JOB_INTERRUPTED'}}
        if data.get('state') == 'running' and data.get('pid'):
            try:
                os.kill(data['pid'], 0)
            except ProcessLookupError:
                data = {**data, 'state': 'interrupted', 'error': {'code': 'JOB_INTERRUPTED'},
                        'instructions': 'Inspect retained library work before a new request; this request is never replayed.'}
        if data.get('state') == 'cancelling' and data.get('pid'):
            members = self._owned_group_members(data['pid'])
            if members is not None and (not members or all(state.startswith('Z') for state in members.values())):
                with self._locked(folder):
                    filename = 'result.json' if (folder / 'result.json').exists() else 'state.json'
                    current = readonly_mcp._json_file(folder, filename)
                    if current.get('state') == 'cancelling':
                        data = {'request_id': request_id, 'state': 'cancelled',
                            'stage': current.get('stage'), 'library_id': current.get('library_id'),
                            'log': current.get('log', []), 'previews': current.get('previews', [])}
                        write_json(folder / 'result.json', data)
                    else:
                        data = current
        return {key: value for key, value in data.items() if key not in ('pid', 'request_hash')}

    def _previews(self, library_id, request_id):
        if not library_id:
            return []
        found = []
        for asset, (relative, label) in self.PREVIEWS.items():
            try:
                path = self.item_file(library_id, relative)
                if path.stat().st_size > 10 * 1024 * 1024:
                    continue
                from PIL import Image
                with Image.open(path) as image:
                    if image.format != 'PNG' or image.width * image.height > 8 * 1024 * 1024:
                        continue
                found.append({'kind': label, 'png': f'/api/workflows/{request_id}/preview/{asset}'})
            except (JobError, OSError, ValueError):
                continue
        return found

    def progress(self, request_id, stage, library_id=None):
        """Persist only fixed, local progress text and bounded preview URLs."""
        if stage not in self.STAGES:
            raise JobError('INVALID_STAGE')
        folder = self._folder(request_id)
        with self._locked(folder):
            state = readonly_mcp._json_file(folder, 'state.json')
            if state.get('state') in ('cancelled', 'completed', 'failed', 'interrupted'):
                return
            cancelled = (folder / 'cancel.requested').exists() or state.get('state') == 'cancelling'
            library_id = library_id or state.get('library_id')
            if not cancelled:
                message = self.STAGES[stage]
                log = list(state.get('log', []))
                if not log or log[-1] != message:
                    log.append(message)
                state.update({'state': 'running', 'stage': message, 'log': log[-32:]})
            if library_id:
                state['library_id'] = library_id
            state['previews'] = self._previews(library_id, request_id)
            write_json(folder / 'state.json', state)
            if cancelled:
                raise JobError('JOB_CANCELLED')

    def preview(self, request_id, asset):
        if asset not in self.PREVIEWS:
            raise JobError('ASSET_NOT_FOUND')
        status = self.status(request_id)
        library_id = status.get('library_id') or (status.get('result') or {}).get('library_id')
        if not library_id:
            raise JobError('ASSET_NOT_FOUND')
        path = self.item_file(library_id, self.PREVIEWS[asset][0])
        if path.stat().st_size > 10 * 1024 * 1024:
            raise JobError('LIMIT_EXCEEDED')
        from PIL import Image
        with Image.open(path) as image:
            if image.format != 'PNG' or image.width * image.height > 8 * 1024 * 1024:
                raise JobError('LIMIT_EXCEEDED')
        return path.read_bytes()

    def _watch(self, request_id, process):
        folder = self._folder(request_id)
        marker = folder / 'cancel.requested'
        if not getattr(process, '_child_created', False):
            with self._processes_lock:
                process.wait()
                self._processes.pop(request_id, None)
        else:
            term_sent, deadline = False, None
            while True:
                cancelling = marker.exists()
                if cancelling and not term_sent:
                    self._signal_owned_group(process.pid, 15)
                    term_sent, deadline = True, time.monotonic() + 2
                process_state = self._process_state(process.pid)
                exited = process_state is None or process_state.startswith('Z')
                if cancelling and deadline is not None and time.monotonic() >= deadline:
                    # The child stays unreaped until its entire process group is stopped.
                    self._signal_owned_group(process.pid, 9)
                if exited and cancelling:
                    members = self._owned_group_members(process.pid)
                    if members is not None and all(state.startswith('Z') for state in members.values()):
                        break
                elif exited:
                    break
                time.sleep(0.05)
            with self._processes_lock:
                process.wait()
                self._processes.pop(request_id, None)
        with self._locked(folder):
            result_path = folder / 'result.json'
            state = readonly_mcp._json_file(folder, 'state.json')
            cancel = (folder / 'cancel.requested').exists() or state.get('state') == 'cancelling'
            if result_path.exists():
                result = readonly_mcp._json_file(folder, 'result.json')
                if cancel and result.get('state') not in ('cancelled', 'completed', 'failed'):
                    result.update({'state': 'cancelled', 'stage': state.get('stage'),
                                   'library_id': state.get('library_id'),
                                   'log': state.get('log', []), 'previews': state.get('previews', [])})
                    write_json(result_path, result)
            elif cancel:
                write_json(result_path, {'request_id': request_id, 'state': 'cancelled',
                    'stage': state.get('stage'), 'library_id': state.get('library_id'),
                    'log': state.get('log', []), 'previews': state.get('previews', [])})
            elif (folder / 'timeout.requested').exists():
                write_json(result_path, {'request_id': request_id, 'state': 'failed',
                    'stage': state.get('stage'), 'library_id': state.get('library_id'),
                    'log': state.get('log', []), 'previews': state.get('previews', []),
                    'error': {'code': 'JOB_TIMEOUT'}})
            else:
                write_json(result_path, {'request_id': request_id, 'state': 'interrupted',
                    'error': {'code': 'JOB_INTERRUPTED'},
                    'instructions': 'Inspect retained library work; this request is never replayed.'})

    def submit(self, tool, args):
        if not self.enabled:
            raise JobError('WORKFLOW_DISABLED')
        request_id = args['request_id']
        folder = self._folder(request_id)
        request = {'tool': tool, 'arguments': args}
        digest = hashlib.sha256(canonical(request)).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise JobError('PATH_DENIED')
        lockpath = readonly_mcp._safe_path(self.store.repo, 'library/.workflow-jobs/launch.lock')
        with lockpath.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if folder.exists():
                previous = readonly_mcp._json_file(folder, 'request.json')
                if previous.get('request_hash') != digest:
                    raise JobError('REQUEST_CONFLICT')
                return self.status(request_id)
            for other in self.root.iterdir():
                if not other.is_dir() or other.is_symlink() or (other / 'result.json').exists():
                    continue
                previous = readonly_mcp._json_file(other, 'state.json')
                if self.status(previous['request_id']).get('state') in ('starting', 'running', 'cancelling'):
                    raise JobError('BUSY')
            # Resolve approved source/document IDs before starting a provider or editor.
            source = args.get('source_library_id') or args.get('library_id')
            if source:
                asset = 'work/gpt_painting.png' if args.get('source_asset') == 'gpt_painting.png' else 'sprite.png'
                path = self.item_file(source, asset)
                readonly_mcp._image(path).close() if asset == 'sprite.png' else None
                if path.stat().st_size > 25 * 1024 * 1024:
                    raise JobError('LIMIT_EXCEEDED')
            if tool == 'forge_import_aseprite':
                self.item_file(source, f'documents/{args["document_id"]}.aseprite')
            folder.mkdir()
            write_json(folder / 'request.json', {**request, 'request_hash': digest})
            state = {'request_id': request_id, 'state': 'starting', 'tool': tool,
                     'started_at': time.time(), 'stage': self.STAGES['starting'],
                     'log': [self.STAGES['starting']], 'previews': [],
                     'next_tool': 'forge_workflow_status'}
            write_json(folder / 'state.json', state)
            env = dict(os.environ)
            for key in tuple(env):
                if ((key.endswith('API_KEY') and key != 'RETRODIFFUSION_API_KEY')
                    or key.startswith(('TUNNEL_', 'PIXELLAB_', 'ANTHROPIC_'))):
                    env.pop(key, None)
            try:
                process = _popen([sys.executable, '-m', 'forge.workflow_worker', request_id],
                    cwd=Path(__file__).resolve().parents[1], env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            except Exception:
                write_json(folder / 'result.json', {'request_id': request_id, 'state': 'failed',
                                                    'error': {'code': 'LAUNCH_FAILED'}})
                raise JobError('LAUNCH_FAILED') from None
            with self._locked(folder):
                current = readonly_mcp._json_file(folder, 'state.json')
                if getattr(process, 'pid', None) is not None:
                    write_json(folder / 'state.json', {**current, 'pid': process.pid})
            with self._processes_lock:
                self._processes[request_id] = process
            threading.Thread(target=self._watch, args=(request_id, process), daemon=True).start()
            return state

    def cancel(self, request_id):
        folder = self._folder(request_id)
        if not folder.exists():
            raise JobError('JOB_NOT_FOUND')
        with self._locked(folder):
            result_path = folder / 'result.json'
            current = readonly_mcp._json_file(folder, 'result.json' if result_path.exists() else 'state.json')
            if current.get('state') in ('completed', 'failed', 'interrupted', 'cancelled'):
                return {key: value for key, value in current.items() if key not in ('pid', 'request_hash')}
            (folder / 'cancel.requested').touch(exist_ok=True)
            current.update({'state': 'cancelling', 'stage': 'Stopping workflow'})
            write_json(folder / 'state.json', current)
        with self._processes_lock:
            process = self._processes.get(request_id)
            if (process is not None and getattr(process, 'pid', None) is not None
                    and getattr(process, 'returncode', None) is None):
                # Signalling and the watcher's final reap/removal share this lock.
                self._signal_owned_group(process.pid, 15)
        return self.status(request_id)

    def review_image(self, result):
        library_id = (result.get('result') or {}).get('library_id')
        if not library_id:
            return None
        try:
            path = self.item_file(library_id, 'review.png')
        except JobError as exc:
            if exc.code == 'ASSET_NOT_FOUND':
                return None
            raise
        if path.stat().st_size > 10 * 1024 * 1024:
            raise JobError('LIMIT_EXCEEDED')
        from PIL import Image
        with Image.open(path) as image:
            if image.format != 'PNG' or image.width * image.height > 8 * 1024 * 1024:
                raise JobError('LIMIT_EXCEEDED')
        return path.read_bytes()
