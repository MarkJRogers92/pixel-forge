"""Local GIF-to-clip bridge. No provider, credentials, cleanup, or credit cost.

Pillow's sequential GIF seek composes delta rectangles and disposal modes on
one logical canvas. Exported PNGs preserve every decoded frame and its timing.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, GifImagePlugin

from . import clip
from .cleanup import MAX_INPUT_BYTES, MAX_INPUT_PIXELS

MAX_FRAMES = clip.MAX_FRAMES
MAX_SOURCE_PIXELS = clip.MAX_SOURCE_PIXELS


def decode_gif(data: bytes, *, default_duration_ms=None):
    """Bounded sequential decode, rejecting static images and unknown timings."""
    if default_duration_ms is not None and (type(default_duration_ms) is not int
            or not 10 <= default_duration_ms <= 655350 or default_duration_ms % 10):
        raise ValueError("default_duration_ms must be a multiple of 10 in 10..655350")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("GIF input byte limit exceeded")
    if not data.startswith((b'GIF87a', b'GIF89a')):
        raise ValueError("input must be an animated GIF")
    frames, durations, disposal, warnings = [], [], [], []
    try:
        with Image.open(io.BytesIO(data)) as image:
            size = image.size
            pixels = size[0] * size[1]
            if pixels > MAX_INPUT_PIXELS:
                raise ValueError("GIF source pixel limit exceeded")
            loop = image.info.get('loop')
            if loop is not None and (type(loop) is not int or not 0 <= loop <= 65535):
                raise ValueError("invalid GIF loop count")
            index = 0
            while True:
                try:
                    image.seek(index)
                except EOFError:
                    break
                if index >= MAX_FRAMES or pixels * (index + 1) > MAX_SOURCE_PIXELS:
                    raise ValueError("GIF frame/source pixel limit exceeded")
                if image.size != size:
                    raise ValueError("GIF frame exceeds the logical canvas")
                # Decoding must precede the next seek: Pillow applies disposal
                # to the previous frame and expands local palettes into RGBA.
                frame = image.convert('RGBA').copy()
                duration = image.info.get('duration', 0)
                if not duration:
                    if default_duration_ms is None:
                        raise ValueError(f"GIF frame {index} has missing/zero duration; "
                                         "set default_duration_ms explicitly")
                    duration = default_duration_ms
                    warnings.append({'code': 'duration_defaulted', 'frame': f'f{index}',
                                     'message': f'Missing/zero GIF delay replaced with {duration} ms as requested.'})
                frames.append(frame)
                durations.append(duration)
                disposal.append(getattr(image, 'disposal_method', 0))
                index += 1
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("cannot decode GIF; input may be corrupt or truncated") from exc
    if len(frames) < 2:
        raise ValueError("input must be an animated GIF with at least two frames")
    return frames, {'durations_ms': durations, 'gif_loop_count': loop,
                    'disposal': disposal, 'warnings': warnings}


def _write_preview(frames, durations, loop, path):
    """Encode each full canvas, without Pillow's save_all duplicate merging.

    Reuse the already constrained shared RGB palette. Index zero is transparent.
    getheader/getdata are Pillow's GIF sequence-writing helpers. Disposal=2
    clears the full canvas between frames, including intentionally blank ones.
    """
    colors = sorted({tuple(px[:3]) for f in frames for px in np.asarray(f).reshape(-1, 4) if px[3]})
    lookup = {rgb: i + 1 for i, rgb in enumerate(colors)}
    palette = [0, 0, 0] + [v for rgb in colors for v in rgb]
    palette += [0] * (768 - len(palette))
    indexed = []
    for frame in frames:
        arr = np.asarray(frame)
        indices = np.array([[lookup[tuple(px[:3])] if px[3] else 0 for px in row]
                            for row in arr], dtype=np.uint8)
        image = Image.fromarray(indices).convert('P')
        image.putpalette(palette)
        indexed.append(image)
    info = {'transparency': 0, 'background': 0, 'version': b'89a'}
    if loop is not None:
        info['loop'] = loop
    with path.open('xb') as file:
        header, _ = GifImagePlugin.getheader(indexed[0], info=info)
        for block in header:
            file.write(block)
        for image, duration in zip(indexed, durations):
            for block in GifImagePlugin.getdata(image, duration=duration, disposal=2, transparency=0):
                file.write(block)
        file.write(b';')
    # Compare decoded pixels/timings, not merely file existence or its header.
    actual, timing = decode_gif(path.read_bytes())
    if (len(actual) != len(frames) or timing['durations_ms'] != durations
            or timing['gif_loop_count'] != loop):
        raise ValueError('GIF preview timing verification failed')
    for expected, observed in zip(frames, actual):
        a, b = np.asarray(expected).copy(), np.asarray(observed).copy()
        a[a[..., 3] == 0] = 0
        b[b[..., 3] == 0] = 0
        if not np.array_equal(a, b):
            raise ValueError('GIF preview pixel verification failed')


def import_gif(source, out, *, width=None, height=None, palette=None, profile=None,
               tag='clip', default_duration_ms=None, save_project=False):
    """Import a downloaded GIF, retaining timings, looping and motion.

    Existing paths are refused. Completed outputs are published only after all
    local validation succeeds. Optional Aseprite output uses original durations
    and is separate from the source GIF's repeat-count metadata.
    """
    source, out = Path(source).expanduser(), Path(out).expanduser()
    if out.exists() or out.is_symlink():
        raise ValueError(f'output already exists; choose a new folder: {out}')
    if not source.is_file():
        raise ValueError(f'GIF is not a file: {source}')
    with source.open('rb') as file:
        data = file.read(MAX_INPUT_BYTES + 1)
    frames, decoded = decode_gif(data, default_duration_ms=default_duration_ms)
    durations, loop = decoded['durations_ms'], decoded['gif_loop_count']
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.forge-gif-', dir=out.parent) as temp:
        stage = Path(temp)
        paths = []
        for i, frame in enumerate(frames):
            path = stage / f'f{i}.png'
            frame.save(path)
            paths.append(path)
        output = stage / 'output'
        report = clip.import_clip(paths, output, width=width, height=height,
            palette=palette, profile=profile, tag=tag, durations_ms=durations,
            gif_loop_count=loop, allow_empty=True)
        normalized = []
        for i in range(len(frames)):
            with Image.open(output / 'frames' / f'f{i}.png') as frame:
                normalized.append(frame.convert('RGBA'))
        _write_preview(normalized, durations, loop, output / 'animation.gif')
        (output / 'source.gif').write_bytes(data)
        source_record = {'path': str(source), 'sha256': hashlib.sha256(data).hexdigest(),
                         'pixel_size': list(frames[0].size)}
        report.update(schema='pixel-forge/gif-clip@1', folder=str(out),
                      sources=[source_record], decoder={'library': 'Pillow',
                      'disposal': decoded['disposal'], 'composited_full_canvas': True})
        report['warnings'].extend(decoded['warnings'])
        report['review']['animation'] = 'animation.gif'
        report['review']['instructions'] += ' Play animation.gif to review timing and the loop seam.'
        report['verified']['gif_preview'] = True
        for i, record in enumerate(report['frames']):
            record['source_frame_index'] = i
        manifest_path = output / 'manifest.json'
        exported = json.loads(manifest_path.read_text())
        exported['reference'] = source_record
        manifest_path.write_text(json.dumps(exported, indent=1))
        if save_project:
            from . import animation
            from .sprite import Sprite
            sprites = [Sprite.from_image(frame) for frame in normalized]
            animation.animate(sprites, output / 'aseprite', tag=tag,
                durations_ms=durations, gif_name=None, project_name='animation.aseprite')
            report['aseprite'] = {'project': 'aseprite/animation.aseprite',
                                 'sheet': 'aseprite/sheet.png', 'atlas': 'aseprite/sheet.json',
                                 'note': 'Source repeat count is in manifest.json; Aseprite tag plays forward.'}
        for name in ('gif-report.json', 'clip-report.json'):
            (output / name).write_text(json.dumps(report, indent=2) + '\n')
        # Refuse a destination created since initial validation. mkdir, then move
        # children into our newly reserved directory (never replace user files).
        out.mkdir(exist_ok=False)
        for child in output.iterdir():
            child.rename(out / child.name)
    return report
