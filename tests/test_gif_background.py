"""Opt-in border keying never acts as global black deletion."""
import json

import numpy as np
import pytest
from PIL import Image

from forge.gif_clip import import_gif


def keyed_source(tmp_path):
    # Black surround, closed red wall and enclosed black detail. One near-black
    # pixel on the outer edge distinguishes exact keying from tolerance.
    frames = []
    for color in ['red', 'blue']:
        f = Image.new('RGB', (9, 9), 'black')
        for y in range(2, 7):
            for x in range(2, 7):
                f.putpixel((x, y), Image.new('RGB', (1, 1), color).getpixel((0, 0)))
        f.putpixel((4, 4), (0, 0, 0))
        f.putpixel((8, 8), (5, 5, 5))
        frames.append(f)
    path = tmp_path / 'key.gif'
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[80, 230], loop=2)
    return path


def pixels(path):
    with Image.open(path) as f:
        return np.array(f.convert('RGBA'))


def test_border_key_preserves_enclosed_black_details_source_and_timing(tmp_path):
    source = keyed_source(tmp_path)
    original = source.read_bytes()
    out = tmp_path / 'out'
    report = import_gif(source, out, background_color='#000000')
    arr = pixels(out / 'frames/f0.png')
    assert arr[0, 0, 3] == 0
    assert tuple(arr[4, 4]) == (0, 0, 0, 255)
    assert tuple(arr[2, 2]) == (255, 0, 0, 255)
    assert tuple(arr[8, 8]) == (5, 5, 5, 255)
    assert source.read_bytes() == original == (out / 'source.gif').read_bytes()
    assert report['durations_ms'] == [80, 230]
    assert report['gif_loop_count'] == 2
    assert report['background_removal']['removed_pixels'] == [55, 55]
    assert report['background_removal']['method'] == 'edge_connected'
    assert 'background_removal_review' in {w['code'] for w in report['warnings']}
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['gif_import']['background_removal'] == report['background_removal']


def test_no_key_preserves_opaque_background_and_near_black_tolerance_is_explicit(tmp_path):
    source = keyed_source(tmp_path)
    import_gif(source, tmp_path / 'original')
    assert pixels(tmp_path / 'original/frames/f0.png')[..., 3].min() == 255
    report = import_gif(source, tmp_path / 'keyed', background_color='#000', background_tolerance=5)
    arr = pixels(tmp_path / 'keyed/frames/f0.png')
    assert arr[8, 8, 3] == 0
    assert arr[4, 4, 3] == 255
    assert report['background_removal']['removed_pixels'] == [56, 56]


@pytest.mark.parametrize('options', [
    {'background_color': 'black'}, {'background_color': '#00000000'},
    {'background_color': '#zzzzzz'}, {'background_color': True},
    {'background_tolerance': 1}, {'background_color': '#000', 'background_tolerance': -1},
    {'background_color': '#000', 'background_tolerance': 33},
    {'background_color': '#000', 'background_tolerance': True},
    {'background_color': '#000', 'background_tolerance': 1.2},
])
def test_invalid_key_options_fail_without_publishing(tmp_path, options):
    source = keyed_source(tmp_path)
    with pytest.raises(ValueError, match='background'):
        import_gif(source, tmp_path / 'out', **options)
    assert not (tmp_path / 'out').exists()


def test_keying_keeps_blank_frames_and_warns_about_edge_connected_details(tmp_path):
    source = tmp_path / 'blank.gif'
    a, b = Image.new('RGB', (7, 7), 'black'), Image.new('RGB', (7, 7), 'black')
    a.putpixel((3, 3), (255, 0, 0))
    a.save(source, save_all=True, append_images=[b], duration=[80, 230], loop=0)
    report = import_gif(source, tmp_path / 'out', background_color='#000')
    assert report['frames'][1]['output_bbox'] is None
    assert report['durations_ms'] == [80, 230]
    assert 'background_emptied_frame' in {w['code'] for w in report['warnings']}
    warning = next(w for w in report['warnings'] if w['code'] == 'background_removal_review')
    assert 'outline' in warning['message'].lower()


def test_keying_runs_before_shared_palette_and_canvas_mapping(tmp_path):
    report = import_gif(keyed_source(tmp_path), tmp_path / 'out', width=18, height=18,
                        palette=['#000', '#fff'], background_color='#000')
    arr = pixels(tmp_path / 'out/frames/f0.png')
    assert arr[0, 0, 3] == 0
    assert arr[8, 8, 3] == 255
    assert report['transform']['offset'] == [0, 0]


def test_source_palette_reduction_is_shared_deterministic_and_preserves_alpha(tmp_path):
    source = tmp_path / 'many-colors.gif'
    a = Image.new('RGBA', (9, 8))
    for i in range(64):
        a.putpixel((i % 8, i // 8), (i * 4, 24, 220, 255))
    b = a.copy()
    b.putpixel((0, 0), (255, 255, 0, 255))
    from forge.gif_clip import _write_preview
    _write_preview([a, b], [80, 230], 0, source)
    with pytest.raises(ValueError, match='shared RGBA'):
        import_gif(source, tmp_path / 'exact')
    first = import_gif(source, tmp_path / 'reduced', colors=8)
    second = import_gif(source, tmp_path / 'again', colors=8)
    assert first['palette']['mode'] == 'source_reduced'
    assert first['palette']['requested_colors'] == 8
    assert len(first['palette']['rgba']) <= 8
    assert first['palette'] == second['palette']
    f0, f1 = pixels(tmp_path / 'reduced/frames/f0.png'), pixels(tmp_path / 'reduced/frames/f1.png')
    assert np.array_equal(f0[1:], f1[1:])  # unchanged source pixels share color mapping
    assert np.all(f0[:, 8, 3] == 0)
    assert np.all(f0[:, :8, 3] == 255)
    assert first['durations_ms'] == [80, 230]
    assert first['gif_loop_count'] == 0
    assert json.loads((tmp_path / 'reduced/manifest.json').read_text())['gif_import']['colors'] == 8


@pytest.mark.parametrize('options', [
    {'colors': 0}, {'colors': 63}, {'colors': True}, {'colors': 3.2},
    {'colors': 8, 'profile': 'dead-mall'}, {'colors': 8, 'palette': ['#000']},
])
def test_invalid_reduction_options_leave_no_output(tmp_path, options):
    with pytest.raises(ValueError, match='colors'):
        import_gif(keyed_source(tmp_path), tmp_path / 'out', **options)
    assert not (tmp_path / 'out').exists()
