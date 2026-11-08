"""Portable local GIF review, with no server, network access or approval state."""
from collections import defaultdict
from html import escape
import json
from pathlib import Path
from string import Template


def write_review(folder, report):
    """Write an escaped review page whose relative links survive moving folders."""
    grouped = defaultdict(list)
    for warning in report['warnings']:
        grouped[(warning['code'], warning['message'])].append(warning.get('frame'))
    warnings = []
    for (code, message), frames in grouped.items():
        ids = [f for f in frames if f]
        suffix = ' Frames: ' + ', '.join(ids) + '.' if ids else ''
        warnings.append(f'<li><strong>{escape(code)}</strong>: {escape(message + suffix)}</li>')
    background = report['background_removal']
    removal = ('Background kept as decoded.' if background is None else
               f"Edge-connected {background['color']} removal, tolerance {background['tolerance']}; "
               f"removed {sum(background['removed_pixels']):,} source pixels across all frames. "
               'Inspect matching outlines and gaps against the original.')
    loop = report['gif_loop_count']
    repeats = 'Play once' if loop is None else ('Infinite repeat' if loop == 0 else f'{loop} repeats after first play')
    palette_note = {'exact': 'Exact source colors.',
                    'source_reduced': 'Reduced to one palette sampled across all source frames. Compare fine accents and colors.',
                    'fixed': 'Mapped to one fixed palette. Compare source colors, especially neon accents.'}
    size = report['transform']['target_canvas']
    frames = ''.join(
        f'<figure><div class="surface frame"><img src="frames/f{i}.png" width="{size[0]}" height="{size[1]}" '
        f'loading="lazy" alt="Frame {i + 1}"></div><figcaption>{i + 1} / {report["frame_count"]} · {delay} ms'
        f'</figcaption></figure>' for i, delay in enumerate(report['durations_ms']))
    data = json.dumps({'frames': [f'frames/f{i}.png' for i in range(report['frame_count'])],
                       'durations_ms': report['durations_ms'], 'gif_loop_count': loop, 'size': size})
    # A JSON script body must not let source strings terminate its element.
    data = data.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    links = [('source.gif', 'Original GIF'), ('animation.gif', 'Converted GIF'),
             ('sheet.png', 'Sheet 1x'), ('sheet@2x.png', 'Sheet 2x'),
             ('manifest.json', 'Manifest'), ('gif-report.json', 'Full QA report')]
    if report.get('aseprite'):
        links.append(('aseprite/animation.aseprite', 'Editable Aseprite'))
    html = Template((Path(__file__).parent / 'templates/gif-review.html').read_text()).substitute(
        title=escape(report['tag']), summary=escape(f'{report["frame_count"]} frames · {size[0]} × {size[1]} px · '
        f'{report["total_duration_ms"] / 1000:g} seconds · {repeats}'),
        palette=escape(palette_note[report['palette']['mode']]),
        removal=escape(removal), warnings=''.join(warnings) or '<li>No numeric warnings. Visual review is still required.</li>',
        links=' · '.join(f'<a href="{p}">{label}</a>' for p, label in links),
        frames=frames, data=data, width=size[0], height=size[1])
    (Path(folder) / 'review.html').write_text(html, encoding='utf-8')
