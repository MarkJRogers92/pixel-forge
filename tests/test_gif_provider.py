import base64
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image


def provider():
    from forge import gif_provider
    return gif_provider


def gif_bytes(animated=True):
    out = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(out, format='GIF', save_all=animated,
        append_images=[Image.new('RGB', (8, 8), 'blue')] if animated else [], duration=200)
    return out.getvalue()


@pytest.fixture
def network(monkeypatch):
    p = provider()
    response = io.BytesIO(json.dumps({'created': 123, 'data': [{'b64_json': base64.b64encode(gif_bytes()).decode()}],
        'usage': {'total_tokens': 8, 'input_tokens_details': {'text_tokens': 3}, 'secret': 'omit'}, 'secret': 'omit'}).encode())
    call = Mock(return_value=response)
    monkeypatch.setattr(p, 'urlopen', call)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret')
    return call


def test_offline_defaults_and_rounding(monkeypatch):
    p = provider()
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    req = p.build_request('neon lamp', frame_duration_ms=205)
    assert req == {'endpoint': 'https://api.openai.com/v1/images/generations', 'payload': {
        'model': 'gpt-image-2.6-sunburst', 'prompt': 'neon lamp', 'output_format': 'gif',
        'frame_count': 9, 'frame_duration_ms': 210, 'size': 'auto', 'n': 1}}


@pytest.mark.parametrize('kwargs', [dict(prompt=''), dict(model='other'), dict(frame_count=1),
    dict(frame_count=True), dict(frame_count=17), dict(frame_duration_ms=19),
    dict(frame_duration_ms=2001), dict(size='17x32'), dict(size='https://evil'), dict(size='0x16')])
def test_invalid_requests(kwargs):
    p = provider()
    with pytest.raises(p.GifProviderError):
        p.build_request(**({'prompt': 'lamp'} | kwargs))


def test_reference_validated_as_still_image(tmp_path):
    p = provider()
    ref = tmp_path / 'ref.png'
    Image.new('RGB', (8, 8)).save(ref)
    req = p.build_request('lamp', reference=ref)
    assert req['endpoint'].endswith('/images/edits')
    assert req['payload']['images'] == [{'image_url': 'data:image/png;base64,' + base64.b64encode(ref.read_bytes()).decode()}]
    ref.write_bytes(gif_bytes())
    with pytest.raises(p.GifProviderError):
        p.build_request('lamp', reference=ref)
    ref.write_bytes(b'broken')
    with pytest.raises(p.GifProviderError):
        p.build_request('lamp', reference=ref)


def test_reference_size_bound(tmp_path, monkeypatch):
    p = provider()
    ref = tmp_path / 'ref.png'
    Image.new('RGB', (8, 8)).save(ref)
    monkeypatch.setattr(p, 'MAX_REFERENCE_BYTES', 10)
    with pytest.raises(p.GifProviderError):
        p.build_request('lamp', reference=ref)


def test_no_paid_call_without_explicit_opt_in(tmp_path, network):
    p = provider()
    for opt in [False, 1, 'yes']:
        with pytest.raises(p.GifProviderError):
            p.generate('lamp', tmp_path / 'out', allow_paid=opt)
    network.assert_not_called()
    assert not (tmp_path / 'out').exists()


def test_missing_key_no_call(tmp_path, network, monkeypatch):
    p = provider()
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(p.GifProviderError):
        p.generate('lamp', tmp_path / 'out', allow_paid=True)
    network.assert_not_called()


def test_preserves_raw_gif_and_safe_metadata(tmp_path, network):
    p = provider()
    result = p.generate('lamp', tmp_path / 'out', allow_paid=True)
    assert Path(result['gif_path']).read_bytes() == gif_bytes()
    metadata = json.loads(Path(result['metadata_path']).read_text())
    assert metadata == result['metadata']
    assert metadata['usage'] == {'total_tokens': 8, 'input_tokens_details': {'text_tokens': 3}}
    assert 'test-secret' not in json.dumps(metadata) and 'omit' not in json.dumps(metadata)
    req = network.call_args.args[0]
    assert req.full_url == 'https://api.openai.com/v1/images/generations'
    assert req.method == 'POST'
    assert json.loads(req.data)['output_format'] == 'gif'
    assert network.call_count == 1


def test_refuses_existing_output_before_spend(tmp_path, network):
    p = provider()
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'source.gif').write_bytes(b'keep')
    with pytest.raises(p.GifProviderError):
        p.generate('lamp', out, allow_paid=True)
    assert (out / 'source.gif').read_bytes() == b'keep'
    network.assert_not_called()


@pytest.mark.parametrize('failure', [urllib.error.HTTPError('https://api.openai.com', 403, 'secret', {}, io.BytesIO(b'secret')),
    urllib.error.HTTPError('https://api.openai.com', 429, 'secret', {}, io.BytesIO(b'secret')),
    urllib.error.HTTPError('https://api.openai.com', 503, 'secret', {}, io.BytesIO(b'secret')),
    TimeoutError('secret'), urllib.error.URLError('secret')])
def test_failures_do_not_retry_or_leak(tmp_path, network, failure):
    p = provider()
    network.side_effect = failure
    with pytest.raises(p.GifProviderError) as error:
        p.generate('lamp', tmp_path / 'out', allow_paid=True)
    assert 'secret' not in str(error.value)
    if isinstance(failure, urllib.error.HTTPError):
        assert str(failure.code) in str(error.value)
    assert network.call_count == 1
    assert not (tmp_path / 'out' / 'source.gif').exists()


@pytest.mark.parametrize('data', [b'not json', b'[]', b'{}', b'{"data": [null]}',
    b'{"data": [{"b64_json": "@@@"}]}', b'{"data": [{"b64_json": 123}]}',
    json.dumps({'data': [{'b64_json': base64.b64encode(gif_bytes(False)).decode()}]}).encode(),
    json.dumps({'data': [{'b64_json': base64.b64encode(b'GIF89a broken').decode()}]}).encode()])
def test_bad_responses_no_fallback(tmp_path, network, data):
    p = provider()
    network.return_value = io.BytesIO(data)
    with pytest.raises(p.GifProviderError):
        p.generate('lamp', tmp_path / 'out', allow_paid=True)
    assert network.call_count == 1
    assert not (tmp_path / 'out' / 'source.gif').exists()


def test_http_read_is_bounded(tmp_path, network, monkeypatch):
    p = provider()
    monkeypatch.setattr(p, 'MAX_RESPONSE_BYTES', 16)
    network.return_value = io.BytesIO(b'x' * 17)
    with pytest.raises(p.GifProviderError):
        p.generate('lamp', tmp_path / 'out', allow_paid=True)


def test_redirect_handler_refuses_external_target():
    p = provider()
    with pytest.raises(urllib.error.HTTPError):
        p._NoRedirect().redirect_request(None, None, 307, 'redirect', {}, 'https://evil.example')


def test_incomplete_http_response_is_safe(tmp_path, network):
    from http.client import IncompleteRead
    p = provider()
    network.side_effect = IncompleteRead(b'secret')
    with pytest.raises(p.GifProviderError):
        p.generate('lamp', tmp_path / 'out', allow_paid=True)
    assert network.call_count == 1


def test_animated_png_reference_rejected(tmp_path):
    p = provider()
    ref = tmp_path / 'ref.png'
    Image.new('RGB', (8, 8), 'red').save(ref, save_all=True,
        append_images=[Image.new('RGB', (8, 8), 'blue')], duration=200)
    with pytest.raises(p.GifProviderError):
        p.build_request('lamp', reference=ref)


@pytest.mark.parametrize('bound,value', [('MAX_GIF_BYTES', 10), ('MAX_IMAGE_PIXELS', 63), ('MAX_ANIMATION_PIXELS', 100)])
def test_gif_resource_bounds(tmp_path, network, monkeypatch, bound, value):
    p = provider()
    monkeypatch.setattr(p, bound, value)
    with pytest.raises(p.GifProviderError):
        p.generate('lamp', tmp_path / 'out', allow_paid=True)
    assert not (tmp_path / 'out' / 'source.gif').exists()


def test_excessive_gif_frames_rejected(tmp_path, network):
    p = provider()
    out = io.BytesIO()
    frames = [Image.new('RGB', (8, 8), (i * 10, 0, 0)) for i in range(17)]
    frames[0].save(out, format='GIF', save_all=True, append_images=frames[1:], duration=200)
    network.return_value = io.BytesIO(json.dumps({'data': [{'b64_json': base64.b64encode(out.getvalue()).decode()}]}).encode())
    with pytest.raises(p.GifProviderError):
        p.generate('lamp', tmp_path / 'out', allow_paid=True)
    assert not (tmp_path / 'out' / 'source.gif').exists()


def test_expanding_frame_canvas_rejected(tmp_path, network):
    p = provider()
    data = bytearray(gif_bytes())
    # This fixture has two full-canvas image descriptors. Move the second
    # descriptor right one pixel, beyond the declared 8x8 logical canvas.
    descriptor = data.rfind(b'\x2c\x00\x00\x00\x00\x08\x00\x08\x00')
    assert descriptor > 0
    data[descriptor + 1] = 1
    network.return_value = io.BytesIO(json.dumps({'data': [{'b64_json': base64.b64encode(data).decode()}]}).encode())
    with pytest.raises(p.GifProviderError):
        p.generate('lamp', tmp_path / 'out', allow_paid=True)
    assert not (tmp_path / 'out' / 'source.gif').exists()


def test_import_byte_limit_is_applied_before_decode():
    p = provider()
    data = b'GIF89a' + b'\0' * (25 * 1024 * 1024)
    with pytest.raises(p.GifProviderError, match='bounded GIF'):
        p._validate_gif(data)


def test_result_warns_when_requested_timing_or_count_differs(tmp_path, network):
    p = provider()
    result = p.generate('lamp', tmp_path / 'out', allow_paid=True, frame_count=9, frame_duration_ms=300)
    assert result['metadata']['warnings'] == [
        'Observed frame count 2 differs from requested 9.',
        'Observed frame durations differ from requested 300 ms.']
    assert result['metadata']['observed']['durations_ms'] == [200, 200]
    assert network.call_count == 1


def test_exact_result_has_no_mismatch_warning_and_has_fingerprints(tmp_path, network):
    import hashlib
    p = provider()
    ref = tmp_path / 'ref.png'
    Image.new('RGB', (8, 8), 'green').save(ref)
    ref_bytes = ref.read_bytes()
    response = network.return_value
    def respond(*args, **kwargs):
        # The hash must identify the bytes actually submitted, not a later
        # reread of a file that another process may have changed.
        ref.write_bytes(b'changed after sending')
        return response
    network.side_effect = respond
    result = p.generate('lamp', tmp_path / 'out', allow_paid=True, reference=ref, frame_count=2)
    metadata = result['metadata']
    assert metadata['warnings'] == []
    assert metadata['source_sha256'] == hashlib.sha256(gif_bytes()).hexdigest()
    assert metadata['reference_sha256'] == hashlib.sha256(ref_bytes).hexdigest()
    assert 'image_url' not in json.dumps(metadata)


def test_custom_size_total_animation_budget_rejected_before_paid_call(tmp_path, network):
    p = provider()
    # Individual 4096x4096 frames fit, but nine decoded frames do not.
    with pytest.raises(p.GifProviderError):
        p.generate('lamp', tmp_path / 'out', allow_paid=True, size='4096x4096', frame_count=9)
    network.assert_not_called()
    assert not (tmp_path / 'out').exists()
    assert p.build_request('lamp', size='2048x2048', frame_count=16)['payload']['size'] == '2048x2048'
