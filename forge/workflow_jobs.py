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

from . import readonly_mcp
from .authoring_schema import canonical


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
    def __init__(self, store, enabled=False):
        self.store, self.enabled = store, enabled
        self.root = store.repo / 'library' / '.workflow-jobs'

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
        return {key: value for key, value in data.items() if key not in ('pid', 'request_hash')}

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
                if self.status(previous['request_id']).get('state') in ('starting', 'running'):
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
                     'started_at': time.time(), 'next_tool': 'forge_workflow_status'}
            write_json(folder / 'state.json', state)
            env = dict(os.environ)
            for key in tuple(env):
                if ((key.endswith('API_KEY') and key != 'RETRODIFFUSION_API_KEY')
                    or key.startswith(('TUNNEL_', 'PIXELLAB_', 'ANTHROPIC_'))):
                    env.pop(key, None)
            try:
                process = subprocess.Popen([sys.executable, '-m', 'forge.workflow_worker', request_id],
                    cwd=Path(__file__).resolve().parents[1], env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            except Exception:
                write_json(folder / 'result.json', {'request_id': request_id, 'state': 'failed',
                                                    'error': {'code': 'LAUNCH_FAILED'}})
                raise JobError('LAUNCH_FAILED') from None
            threading.Thread(target=process.wait, daemon=True).start()
            return state

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
