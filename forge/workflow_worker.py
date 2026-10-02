"""Fixed local job worker. No arbitrary executable, URL, or caller filesystem path."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys

from . import aseprite, editors, fixer_workflow, library, readonly_mcp
from .authoring_schema import validate_model
from .workflow_jobs import WorkflowJobs, JobError, write_json, public_result
from .workflow_mcp import MODELS


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
    if tool == 'forge_prepare':
        source = None
        if args['source_library_id']:
            asset = 'work/gpt_painting.png' if args['source_asset'] == 'gpt_painting.png' else 'sprite.png'
            source = str(jobs.item_file(args['source_library_id'], asset))
        return fixer_workflow.prepare(args['prompt'], args['width'], args['height'],
            project=args['project'], input_png=source, checks=args['checks'])
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
    # Bound a stuck workflow, including its child CLI process group.
    def timeout(*_):
        write_json(folder / 'result.json', {'request_id': request_id, 'state': 'failed',
                                           'error': {'code': 'JOB_TIMEOUT'}})
        os.killpg(os.getpgrp(), signal.SIGKILL)
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(1800)
    try:
        request = readonly_mcp._json_file(folder, 'request.json')
        tool = request['tool']
        args = validate_model(MODELS[tool], request['arguments']).model_dump()
        state = readonly_mcp._json_file(folder, 'state.json')
        write_json(folder / 'state.json', {**state, 'state': 'running', 'pid': os.getpid()})
        result = execute(jobs, tool, args)
        state = {'request_id': request_id, 'state': 'completed', 'result': public_result(result)}
    except Exception as exc:
        state = {'request_id': request_id, 'state': 'failed',
                 'error': {'code': getattr(exc, 'code', 'WORKFLOW_FAILED'), 'type': type(exc).__name__},
                 'instructions': 'Inspect retained library work; this request will not be replayed.'}
    write_json(folder / 'result.json', state)


if __name__ == '__main__':
    main()
