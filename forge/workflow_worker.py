"""Fixed local job worker. No arbitrary executable, URL, or caller filesystem path."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from . import aseprite, editors, fixer_workflow, library, readonly_mcp
from .authoring_schema import validate_model
from .workflow_jobs import WorkflowJobs, JobError, write_json, public_result
from .workflow_mcp import MODELS


class WorkflowCancelled(Exception):
    code = 'JOB_CANCELLED'


def validate_work_files(jobs, library_id):
    """All inherited/layer images stay within this approved item's bounded files."""
    source = jobs.item_file(library_id)
    work = readonly_mcp._safe_path(source.parent, 'work')
    if work.exists():
        from .cleanup import load_image
        for candidate in work.glob('*.png'):
            path = jobs.item_file(library_id, 'work/' + candidate.name)
            if path.stat().st_size > 25 * 1024 * 1024:
                raise ValueError('layer too large')
            image = load_image(path.read_bytes())
            image.close()


def execute(jobs, tool, args):
    if tool == 'forge_clip':
        from .clip import import_clip
        paths = jobs.clip_sources(args)
        item_id = library.allocate_id(args['tag'])
        directory = readonly_mcp._safe_path(jobs.store.repo, f'library/{item_id}')
        report = import_clip(paths, directory, width=args['width'], height=args['height'],
            fps=args['fps'], loop=args['loop'], tag=args['tag'], palette=args['palette'])
        meta = readonly_mcp._json_file(directory, 'meta.json')
        meta.update({'kind': 'clip', 'prompt': args['tag'],
                     'source_library_ids': args['source_library_ids'], 'status': 'awaiting_visual_review'})
        write_json(directory / 'meta.json', meta)
        # Keep local provenance on disk, but return only IDs and relative artifacts.
        report.pop('folder')
        report['sources'] = [{key: value for key, value in entry.items() if key != 'path'}
                             for entry in report['sources']]
        return {**report, 'library_id': item_id, 'source_library_ids': args['source_library_ids'],
                'status': 'awaiting_visual_review'}
    if tool == 'forge_prepare':
        source = None
        if args['source_library_id']:
            asset = 'work/gpt_painting.png' if args['source_asset'] == 'gpt_painting.png' else 'sprite.png'
            source = str(jobs.item_file(args['source_library_id'], asset))
        return fixer_workflow.prepare(args['prompt'], args['width'], args['height'],
            project=args['project'], input_png=source, checks=args['checks'],
            progress=lambda stage, library_id=None: jobs.progress(args['request_id'], stage, library_id))
    if tool == 'forge_repair':
        jobs.item_file(args['library_id'])
        validate_work_files(jobs, args['library_id'])
        return fixer_workflow.repair(args['library_id'], args['instructions'], args['max_changed_pixels'])
    if tool == 'forge_open_aseprite':
        sprite = jobs.item_file(args['library_id'])
        validate_work_files(jobs, args['library_id'])
        directory = readonly_mcp._safe_path(sprite.parent, 'documents')
        directory.mkdir(exist_ok=True)
        doc = directory / f'{args["request_id"]}.aseprite'
        if doc.exists() or doc.is_symlink():
            raise ValueError('document exists')
        aseprite.build_document(editors.document_layers(args['library_id']), doc)
        aseprite.open_in_editor(doc)
        return {'library_id': args['library_id'], 'document_id': args['request_id'], 'status': 'open_in_aseprite'}
    if tool == 'forge_import_aseprite':
        doc = jobs.item_file(args['library_id'], f'documents/{args["document_id"]}.aseprite')
        if doc.stat().st_size > 50 * 1024 * 1024:
            raise ValueError('document too large')
        flat = aseprite.export_png(doc, jobs._folder(args['request_id']) / 'aseprite_export.png')
        from .cleanup import load_image
        from .sprite import Sprite
        from . import qa, rd_fixer
        import numpy as np
        image = load_image(flat.read_bytes())
        rd_fixer.validate_size(*image.size)
        rgba = np.asarray(image)
        if np.any((rgba[..., 3] != 0) & (rgba[..., 3] != 255)):
            raise JobError('NONBINARY_ALPHA')
        if len(np.unique(rgba[rgba[..., 3] != 0], axis=0)) > 32:
            raise JobError('PALETTE_LIMIT')
        sprite = Sprite.from_image(image)
        item_id, directory = library.new_dir(args['title'])
        review = fixer_workflow._review(directory, sprite, [])
        report = qa.check(sprite, max_colors=32)
        meta = {'kind': 'aseprite-edit', 'prompt': args['title'], 'parent_library_id': args['library_id'],
                'document_id': args['document_id'], 'review': review, 'qa': report,
                'size': [sprite.width, sprite.height], 'status': 'awaiting_visual_review'}
        library.save_result(directory, sprite, meta)
        return {'library_id': item_id, 'size': meta['size'], 'qa': report, 'review': review,
                'status': 'awaiting_visual_review'}
    raise ValueError('unsupported workflow')


def main():
    jobs = WorkflowJobs(readonly_mcp.store, enabled=True)
    request_id = sys.argv[1]
    folder = jobs._folder(request_id)
    cancel_requested = threading.Event()
    def handle_cancel(*_):
        cancel_requested.set()
        raise WorkflowCancelled()
    def watch_cancel():
        marker = folder / 'cancel.requested'
        while not marker.exists():
            time.sleep(0.05)
        session_id = os.getpgrp()
        try:
            # This worker is running inside the session it created for its own children.
            jobs._signal_owned_group(session_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 2
        while True:
            members = jobs._owned_group_members(session_id)
            active_children = members is not None and any(
                pid != os.getpid() and not state.startswith('Z') for pid, state in members.items())
            if members is not None and not active_children:
                return
            if time.monotonic() >= deadline:
                try:
                    jobs._signal_owned_group(session_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2
            time.sleep(0.05)
    signal.signal(signal.SIGTERM, handle_cancel)
    watcher = threading.Thread(target=watch_cancel, daemon=True)
    # Bound a stuck workflow, including its child CLI process group.
    def timeout(*_):
        # Do not acquire the job lock from an alarm handler: the signal may have
        # interrupted this thread while it held that same lock.
        (folder / 'timeout.requested').touch(exist_ok=True)
        os.killpg(os.getpgrp(), signal.SIGKILL)
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(1800)
    try:
        request = readonly_mcp._json_file(folder, 'request.json')
        tool = request['tool']
        args = validate_model(MODELS[tool], request['arguments']).model_dump()
        with jobs._locked(folder):
            if (folder / 'cancel.requested').exists():
                raise WorkflowCancelled()
            state = readonly_mcp._json_file(folder, 'state.json')
            write_json(folder / 'state.json', {**state, 'state': 'running', 'pid': os.getpid()})
        watcher.start()
        result = execute(jobs, tool, args)
        with jobs._locked(folder):
            current = readonly_mcp._json_file(folder, 'state.json')
            if (folder / 'cancel.requested').exists() or current.get('state') == 'cancelling':
                state = {**current, 'request_id': request_id, 'state': 'cancelling',
                    'stage': current.get('stage'), 'library_id': current.get('library_id'),
                    'log': current.get('log', []), 'previews': current.get('previews', [])}
                write_json(folder / 'state.json', state)
            else:
                state = {'request_id': request_id, 'state': 'completed',
                    'stage': current.get('stage'), 'library_id': current.get('library_id'),
                    'log': current.get('log', []), 'previews': current.get('previews', []),
                    'result': public_result(result)}
                write_json(folder / 'result.json', state)
    except WorkflowCancelled:
        if watcher.is_alive():
            watcher.join(2.5)
        with jobs._locked(folder):
            current = readonly_mcp._json_file(folder, 'state.json')
            state = {**current, 'request_id': request_id, 'state': 'cancelling',
                'stage': current.get('stage'), 'library_id': current.get('library_id'),
                'log': current.get('log', []), 'previews': current.get('previews', [])}
            write_json(folder / 'state.json', state)
    except Exception as exc:
        with jobs._locked(folder):
            current = readonly_mcp._json_file(folder, 'state.json')
            if (folder / 'cancel.requested').exists() or current.get('state') == 'cancelling':
                state = {**current, 'request_id': request_id, 'state': 'cancelling',
                    'stage': current.get('stage'), 'library_id': current.get('library_id'),
                    'log': current.get('log', []), 'previews': current.get('previews', [])}
                write_json(folder / 'state.json', state)
            else:
                state = {'request_id': request_id, 'state': 'failed',
                    'stage': current.get('stage'), 'library_id': current.get('library_id'),
                    'log': current.get('log', []), 'previews': current.get('previews', []),
                    'error': {'code': getattr(exc, 'code', 'WORKFLOW_FAILED'), 'type': type(exc).__name__},
                    'instructions': 'Inspect retained library work; this request will not be replayed.'}
                write_json(folder / 'result.json', state)


if __name__ == '__main__':
    main()
