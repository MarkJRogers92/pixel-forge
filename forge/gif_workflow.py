"""Explicit paid-generation opt-in followed by the ordinary local GIF importer."""
from __future__ import annotations

import json
from pathlib import Path

from . import clip, gif_provider
from .gif_clip import import_gif


def generate_clip(prompt, out, *, execute_paid=False, model=gif_provider.MODELS[0],
                  frame_count=9, frame_duration_ms=200, size='auto', reference=None,
                  width=None, height=None, palette=None, profile=None, tag='clip',
                  save_project=False):
    """Prepare offline by default. A real invocation never retries generation.

    If pixel conversion fails, keep the expensive raw GIF and return its path.
    The user can adjust a palette/canvas and run gif-import without regenerating.
    """
    out = Path(out).expanduser()
    if out.exists() or out.is_symlink():
        raise ValueError(f'output already exists; choose a new folder: {out}')
    for name, value in (('width', width), ('height', height)):
        if value is not None and (type(value) is not int or not 1 <= value <= 1024):
            raise ValueError(f'{name} must be an integer in 1..1024')
    if not isinstance(tag, str) or not tag.strip():
        raise ValueError('tag must be nonempty')
    clip._fixed_palette(palette, profile)
    prepared = gif_provider.build_request(prompt, model=model, frame_count=frame_count,
        frame_duration_ms=frame_duration_ms, size=size, reference=reference)
    if execute_paid is not True:
        return {'status': 'preview_only', 'paid_request_sent': False,
                'endpoint': prepared['endpoint'],
                'request': {k: v for k, v in prepared['payload'].items() if k != 'images'},
                'reference': str(reference) if reference is not None else None,
                'output_dir': str(out),
                'requirements': ['Explicit --execute-paid approval; API charges may apply.',
                                 'OPENAI_API_KEY for an account with this model GIF preview access.',
                                 'No fixed per-GIF price or account access was verified.'],
                'alternative': 'Generate a GIF in normal ChatGPT, download it, then use gif-import. '
                               'Local import needs no API credentials.'}
    if save_project:
        from .aseprite import find_executable
        if not find_executable():
            raise ValueError('Aseprite not found; no generation request sent. Omit save_project or configure Aseprite.')
    generated = gif_provider.generate(prompt, out, allow_paid=True, model=model,
        frame_count=frame_count, frame_duration_ms=frame_duration_ms, size=size, reference=reference)
    try:
        report = import_gif(generated['gif_path'], out / 'clip', width=width, height=height,
                            palette=palette, profile=profile, tag=tag, save_project=save_project)
        manifest_path = out / 'clip' / 'manifest.json'
        exported = json.loads(manifest_path.read_text())
        exported['generation'] = generated['metadata']
        manifest_path.write_text(json.dumps(exported, indent=1))
    except (ValueError, OSError, RuntimeError) as exc:
        return {'status': 'needs_import_options', **generated, 'import_error': str(exc),
                'next_step': 'Raw GIF retained. Run gif-import with appropriate canvas/palette options; '
                             'do not regenerate or retry the paid request.'}
    return {'status': 'awaiting_visual_review', **generated, 'clip': report}
