"""Frame timing preservation without requiring an Aseprite installation."""
import json
from pathlib import Path

import pytest
from PIL import Image

from forge import animation, manifest
from forge.sprite import Sprite


def frames():
    return [Sprite.from_image(Image.new('RGBA', (2, 2), color))
            for color in ('red', 'blue')]


def test_manifest_preserves_timing_and_loop_count(tmp_path):
    sprites = frames()
    result = manifest.export_manifest(tmp_path, sprites[0], frames=sprites,
        animation={'name': 'clip', 'fps': 10, 'durations_ms': [40, 170], 'gif_loop_count': 3})
    clip = result['animations']['clip']
    assert clip['fps'] is None
    assert clip['durations_ms'] == [40, 170]
    assert clip['total_duration_ms'] == 210
    assert clip['gif_loop_count'] == 3
    assert [f['duration_ms'] for f in clip['frames']] == [40, 170]
    sheet = json.loads((tmp_path / 'sheet.json').read_text())
    assert sheet['frames'] == {'f0': [0, 0], 'f1': [2, 0]}
    assert sheet['frame_durations_ms'] == {'f0': 40, 'f1': 170}
    assert sheet['durations_ms'] == [40, 170]
    assert sheet['total_duration_ms'] == 210
    assert sheet['gif_loop_count'] == 3
    assert manifest.verify_export(tmp_path)['ok']


@pytest.mark.parametrize('durations', [[], [40], [40, 0], [True, 40], [40.0, 40], [40, 655351], '40,40'])
def test_manifest_rejects_invalid_timing(durations):
    sprites = frames()
    with pytest.raises(ValueError, match='durations_ms'):
        manifest.build_manifest(sprites[0], frames=sprites, animation={'durations_ms': durations})


@pytest.mark.parametrize('loop_count', [None, 0, 1, 65535])
def test_manifest_keeps_gif_loop_semantics(loop_count):
    sprites = frames()
    result = manifest.build_manifest(sprites[0], frames=sprites,
        animation={'durations_ms': [40, 655350], 'gif_loop_count': loop_count})
    assert result['animations']['animation']['gif_loop_count'] == loop_count


@pytest.mark.parametrize('loop_count', [-1, True, 1.5, 65536])
def test_manifest_rejects_invalid_gif_loop(loop_count):
    with pytest.raises(ValueError, match='gif_loop_count'):
        manifest.build_manifest(frames()[0], animation={'gif_loop_count': loop_count})


def test_animate_preserves_per_frame_durations(monkeypatch, tmp_path):
    captured = {}
    def batch(lua, params, timeout):
        captured['lua'] = lua
        captured['spec'] = json.loads(Path(params['frames']).read_text())
        Image.new('RGBA', (4, 2)).save(params['sheet'])
    monkeypatch.setattr(animation.aseprite_ops, 'run_batch', batch)
    result = animation.animate(frames(), tmp_path, durations_ms=[40, 170],
                               gif_name=None, data_name=None, game='phaser')
    assert [f['duration_ms'] for f in captured['spec']] == [40, 170]
    assert 'frames[1].duration_ms' in captured['lua']
    assert 'frames[i].duration_ms' in captured['lua']
    assert result['fps'] is None and result['duration'] is None
    assert result['durations_ms'] == [40, 170]
    assert result['total_duration_ms'] == 210
    desc = json.loads(Path(result['descriptor']).read_text())
    assert desc['fps'] is None and desc['durationMs'] is None
    assert [f['durationMs'] for f in desc['frames']] == [40, 170]
    # Phaser frame durations are extra delays on top of its base duration.
    assert 'frameRate: 25.0' in desc['phaser']['createAnim']
    assert 'f.durationMs - 40' in desc['phaser']['createAnim']


@pytest.mark.parametrize('durations', [[], [40], [40, 0], [True, 40], [40.0, 40], [40, 65536], '40,40'])
def test_animate_rejects_invalid_timing_before_export(tmp_path, durations):
    with pytest.raises(ValueError, match='durations_ms'):
        animation.animate(frames(), tmp_path, durations_ms=durations)


@pytest.mark.parametrize('durations, expected_fps, expected_duration',
                         [(None, 8, 0.125), ([40, 40], 25, 0.04), ([1, 65535], None, None)])
def test_animate_uniform_defaults_and_explicit_boundaries(monkeypatch, tmp_path,
                                                        durations, expected_fps, expected_duration):
    def batch(lua, params, timeout):
        assert params['duration'] == '0.125'
        Image.new('RGBA', (4, 2)).save(params['sheet'])
    monkeypatch.setattr(animation.aseprite_ops, 'run_batch', batch)
    result = animation.animate(frames(), tmp_path, fps=8, durations_ms=durations,
                               gif_name=None, data_name=None)
    assert result['fps'] == expected_fps
    assert result['duration'] == expected_duration
    if durations is None:
        assert 'durations_ms' not in result


def test_manifest_uniform_defaults_unchanged():
    sprites = frames()
    result = manifest.build_manifest(sprites[0], frames=sprites, animation={'fps': 8})
    assert result['animations']['animation'] == {
        'fps': 8.0, 'loop': 'forward',
        'frames': [{'id': 'f0', 'path': 'frames/f0.png'}, {'id': 'f1', 'path': 'frames/f1.png'}],
        'per_direction': {'south': ['f0', 'f1']}}


@pytest.mark.skipif(not animation.aseprite_ops.available(), reason='Aseprite not installed')
def test_real_aseprite_preserves_variable_timing_in_atlas_and_reopened_project(tmp_path):
    sprites = frames() + [Sprite.from_image(Image.new('RGBA', (2, 2), 'green'))]
    durations = [80, 230, 110]
    result = animation.animate(sprites, tmp_path, durations_ms=durations,
                               gif_name=None, project_name='animation.aseprite')
    atlas = json.loads(Path(result['data']).read_text())
    assert [entry['duration'] for entry in atlas['frames'].values()] == durations
    reopened = tmp_path / 'reopened.json'
    animation.aseprite_ops.run_batch('''
local spr = app.open(app.params.project)
local durations = {}
local pixels = {}
for i, frame in ipairs(spr.frames) do
  durations[i] = math.floor(frame.duration * 1000 + 0.5)
  pixels[i] = spr.cels[i].image:getPixel(0, 0)
end
local out = io.open(app.params.output, "w")
out:write(json.encode({durations = durations, pixels = pixels}))
out:close()
''', {'project': result['project'], 'output': str(reopened)})
    record = json.loads(reopened.read_text())
    assert record['durations'] == durations
    # Three distinct pixel values prove that reopening kept frame content/order too.
    expected_pixels = [int.from_bytes(sprite.to_image().getpixel((0, 0)), 'little')
                       for sprite in sprites]
    assert record['pixels'] == expected_pixels


def test_explicit_subcentisecond_timing_refuses_lossy_gif_before_export(monkeypatch, tmp_path):
    def no_export(*args, **kwargs):
        pytest.fail('invalid GIF timing must fail before Aseprite runs')
    monkeypatch.setattr(animation.aseprite_ops, 'run_batch', no_export)
    with pytest.raises(ValueError, match='gif_name=None'):
        animation.animate(frames(), tmp_path, durations_ms=[15, 25])
    assert not (tmp_path / 'sheet.png').exists()
