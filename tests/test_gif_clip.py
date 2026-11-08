"""Animated GIF inputs retain composited canvases, frame timing and repeat policy."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def gif(tmp_path, durations=(80, 230, 110), loop=0, disposal=2):
    frames = []
    for x, color in [(1, (255, 0, 0, 255)), (5, (0, 255, 0, 255)), (3, (0, 0, 255, 255))]:
        im = Image.new('RGBA', (12, 10))
        for y in range(3, 6):
            for xx in range(x, x + 2):
                im.putpixel((xx, y), color)
        frames.append(im)
    path = tmp_path / 'input.gif'
    options = {} if loop is None else {'loop': loop}
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations,
                   disposal=disposal, optimize=False, **options)
    return path, frames


def run(source, out, **kwargs):
    from forge.gif_clip import import_gif
    return import_gif(source, out, **kwargs)


def rgba(path):
    with Image.open(path) as im:
        return np.asarray(im.convert('RGBA'))


@pytest.mark.parametrize('loop', [None, 0, 3])
def test_import_preserves_canvas_timing_repeat_policy_and_source(tmp_path, loop):
    source, frames = gif(tmp_path, loop=loop)
    before = source.read_bytes()
    out = tmp_path / 'clip'
    report = run(source, out, tag='recoil')
    assert report['frame_count'] == 3
    assert report['durations_ms'] == [80, 230, 110]
    assert report['gif_loop_count'] == loop
    assert report['fps'] is None
    assert report['verified']['ok']
    assert report['review']['status'] == 'awaiting_visual_review'
    assert source.read_bytes() == before == (out / 'source.gif').read_bytes()
    assert report['sources'][0]['path'] == str(source)
    for index, expected in enumerate(frames):
        assert np.array_equal(rgba(out / 'frames' / f'f{index}.png'), np.asarray(expected))
    anim = json.loads((out / 'manifest.json').read_text())['animations']['recoil']
    assert anim['durations_ms'] == [80, 230, 110]
    assert anim['gif_loop_count'] == loop
    assert anim['fps'] is None
    assert json.loads((out / 'gif-report.json').read_text()) == report
    with Image.open(out / 'animation.gif') as preview:
        assert preview.info.get('loop') == loop
        for i, expected in enumerate(frames):
            preview.seek(i)
            assert preview.info['duration'] == [80, 230, 110][i]
            assert np.array_equal(np.asarray(preview.convert('RGBA')), np.asarray(expected))


def test_restore_previous_disposal_is_composited_not_independently_decoded(tmp_path):
    source, _ = gif(tmp_path, disposal=[1, 3, 1])
    out = tmp_path / 'clip'
    report = run(source, out)
    a, b, c = [rgba(out / 'frames' / f'f{i}.png') for i in range(3)]
    assert (a[3, 1] == (255, 0, 0, 255)).all()
    assert (b[3, 1] == (255, 0, 0, 255)).all()
    assert (b[3, 5] == (0, 255, 0, 255)).all()
    assert (c[3, 1] == (255, 0, 0, 255)).all()
    assert c[3, 5, 3] == 0
    assert (c[3, 3] == (0, 0, 255, 255)).all()
    assert report['decoder']['disposal'] == [1, 3, 1]


def test_fixed_palette_and_shared_transform_preserve_motion(tmp_path):
    source, _ = gif(tmp_path)
    report = run(source, tmp_path / 'out', width=36, height=20, palette=['#fff'])
    assert report['transform']['offset'] == [6, 0]
    assert [f['output_bbox'] for f in report['frames']] == [[8, 6, 11, 11], [16, 6, 19, 11], [12, 6, 15, 11]]
    assert set(report['palette']['rgba'].values()) == {'#ffffff'}


def test_zero_delay_requires_explicit_default_not_silent_browser_clamp(tmp_path):
    source, _ = gif(tmp_path, durations=[80, 0, 110])
    with pytest.raises(ValueError, match='duration'):
        run(source, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
    report = run(source, tmp_path / 'out', default_duration_ms=90)
    assert report['durations_ms'] == [80, 90, 110]
    assert 'duration_defaulted' in {w['code'] for w in report['warnings']}


@pytest.mark.parametrize('kind', ['static', 'png', 'corrupt', 'truncated'])
def test_invalid_inputs_write_nothing(tmp_path, kind):
    source = tmp_path / 'bad.gif'
    if kind == 'static':
        Image.new('RGB', (12, 10), 'red').save(source)
    elif kind == 'png':
        Image.new('RGB', (12, 10), 'red').save(source, format='PNG')
    elif kind == 'corrupt':
        source.write_bytes(b'GIF89a garbage')
    else:
        source, _ = gif(tmp_path)
        source.write_bytes(source.read_bytes()[:90])
    with pytest.raises(ValueError):
        run(source, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_frame_limit_and_existing_directory_fail_without_overwrite(tmp_path, monkeypatch):
    from forge import gif_clip
    source, _ = gif(tmp_path)
    monkeypatch.setattr(gif_clip, 'MAX_FRAMES', 2)
    with pytest.raises(ValueError, match='frame'):
        run(source, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
    out = tmp_path / 'occupied'
    out.mkdir()
    (out / 'keep').write_text('original')
    with pytest.raises(ValueError, match='exists'):
        run(source, out)
    assert list(out.iterdir()) == [out / 'keep']
    assert (out / 'keep').read_text() == 'original'


def test_blank_frames_remain_blank_without_deleting_timing(tmp_path):
    source, _ = gif(tmp_path)
    with Image.open(source) as im:
        im.seek(0)
        first = im.convert('RGBA')
    first.save(source, save_all=True, append_images=[Image.new('RGBA', first.size), first],
               duration=[80, 170, 90], disposal=2)
    report = run(source, tmp_path / 'out')
    assert report['durations_ms'] == [80, 170, 90]
    assert report['frames'][1]['output_bbox'] is None
    assert not rgba(tmp_path / 'out/frames/f1.png')[..., 3].any()


def test_preview_retains_identical_frames_and_visible_black(tmp_path):
    from forge.gif_clip import _write_preview
    black = Image.new('RGBA', (6, 6))
    black.putpixel((2, 3), (0, 0, 0, 255))
    _write_preview([black, black, black], [50, 90, 140], 4, tmp_path / 'repeated.gif')
    report = run(tmp_path / 'repeated.gif', tmp_path / 'out')
    assert report['frame_count'] == 3
    assert report['durations_ms'] == [50, 90, 140]
    for i in range(3):
        assert np.array_equal(rgba(tmp_path / 'out/frames' / f'f{i}.png'), np.asarray(black))


def test_import_with_aseprite_retains_blank_frame_and_variable_durations(tmp_path):
    from forge import aseprite_ops
    if not aseprite_ops.available():
        pytest.skip('Aseprite not installed')
    first = Image.new('RGBA', (6, 6))
    first.putpixel((2, 3), (255, 0, 0, 255))
    source = tmp_path / 'blank.gif'
    first.save(source, save_all=True, append_images=[Image.new('RGBA', first.size), first],
               duration=[80, 170, 90], disposal=2)
    report = run(source, tmp_path / 'out', save_project=True)
    atlas = json.loads((tmp_path / 'out/aseprite/sheet.json').read_text())
    assert [f['duration'] for f in atlas['frames'].values()] == [80, 170, 90]
    assert len(atlas['frames']) == 3
    assert (tmp_path / 'out' / report['aseprite']['project']).is_file()
