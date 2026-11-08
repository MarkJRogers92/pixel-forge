"""Opt-in OpenAI GIF preview adapter. Preparing a request is entirely offline.

No SDK, key discovery, retries, model fallback, redirects, or ChatGPT-login
fallback. API preview access may require account verification. A failed request
can still have incurred a charge; do not automatically rerun it.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import http.client
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import warnings

from PIL import Image

MODELS = ('gpt-image-2.6-sunburst', 'gpt-image-2.6-flare')
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 96 * 1024 * 1024
MAX_GIF_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_777_216
MAX_ANIMATION_PIXELS = 67_108_864


class GifProviderError(ValueError):
    """Safe diagnostic without credentials or provider response bodies."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError('', code, 'Redirect refused', {}, None)


# A dedicated opener leaves global urllib behavior unchanged and prevents a
# redirect from transmitting the authorization header to another host.
urlopen = urllib.request.build_opener(_NoRedirect()).open


def _reference_url(reference) -> str:
    try:
        with Path(reference).open('rb') as stream:
            data = stream.read(MAX_REFERENCE_BYTES + 1)
        if len(data) > MAX_REFERENCE_BYTES:
            raise GifProviderError('Reference exceeds byte limit.')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}.get(image.format)
                if not mime or image.width * image.height > MAX_IMAGE_PIXELS or getattr(image, 'n_frames', 1) != 1:
                    raise GifProviderError('Reference must be a bounded, still PNG, JPEG, or WebP.')
                image.load()
        return f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')
    except GifProviderError:
        raise
    except (OSError, ValueError, TypeError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise GifProviderError('Unable to read a valid reference image.') from None


def build_request(prompt: str, *, model: str = MODELS[0], frame_count: int = 9,
                  frame_duration_ms: int = 200, size: str = 'auto', reference=None) -> dict:
    """Validate and return endpoint/payload without accessing credentials/network.

    Durations are rounded to the nearest 10 ms (half rounds up). The API may
    reject otherwise well-formed custom sizes based on its current limits.
    """
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32_000:
        raise GifProviderError('Prompt must contain 1 to 32000 characters.')
    if model not in MODELS:
        raise GifProviderError('Unsupported GIF model.')
    if type(frame_count) is not int or not 2 <= frame_count <= 16:
        raise GifProviderError('Frame count must be an integer from 2 to 16.')
    if type(frame_duration_ms) is not int or not 20 <= frame_duration_ms <= 2000:
        raise GifProviderError('Frame duration must be an integer from 20 to 2000 ms.')
    if size != 'auto':
        match = re.fullmatch(r'([1-9][0-9]{0,5})x([1-9][0-9]{0,5})', size) if isinstance(size, str) else None
        if not match or any(int(n) % 16 for n in match.groups()):
            raise GifProviderError('Size must be auto or positive multiples of 16: WIDTHxHEIGHT.')
        if int(match[1]) * int(match[2]) > MAX_IMAGE_PIXELS:
            raise GifProviderError('Requested size exceeds local pixel safety limit.')
        if int(match[1]) * int(match[2]) * frame_count > MAX_ANIMATION_PIXELS:
            raise GifProviderError('Requested animation exceeds local total pixel safety limit.')
    payload = dict(model=model, prompt=prompt, output_format='gif', frame_count=frame_count,
                   frame_duration_ms=((frame_duration_ms + 5) // 10) * 10, size=size, n=1)
    method = 'generations'
    if reference is not None:
        payload['images'] = [{'image_url': _reference_url(reference)}]
        method = 'edits'
    return {'endpoint': 'https://api.openai.com/v1/images/' + method, 'payload': payload}


def _validate_gif(data: bytes) -> dict:
    if len(data) > MAX_GIF_BYTES or data[:6] not in (b'GIF87a', b'GIF89a'):
        raise GifProviderError('Response is not a bounded GIF.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                canvas = image.size
                pixels = image.width * image.height
                if not 0 < pixels <= MAX_IMAGE_PIXELS:
                    raise GifProviderError('GIF exceeds pixel safety limit.')
                durations = []
                # Sequential scan bounds frame count without allowing n_frames
                # to scan an arbitrary number of frames first.
                for i in range(17):
                    try:
                        image.seek(i)
                    except EOFError:
                        break
                    if image.size != canvas:
                        raise GifProviderError('GIF frame expands beyond the initial canvas.')
                    if i >= 16 or pixels * (i + 1) > MAX_ANIMATION_PIXELS:
                        raise GifProviderError('GIF exceeds animation safety limits.')
                    image.load()
                    durations.append(image.info.get('duration', 0))
                if len(durations) < 2:
                    raise GifProviderError('Provider returned a static image, not an animation.')
                return {'width': image.width, 'height': image.height, 'frame_count': len(durations),
                        'durations_ms': durations}
    except GifProviderError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise GifProviderError('Provider returned an invalid GIF.') from None


def _safe_usage(value):
    if not isinstance(value, dict):
        return {}
    numeric_keys = {'input_tokens', 'output_tokens', 'total_tokens', 'text_tokens', 'image_tokens'}
    usage = {key: number for key, number in value.items()
             if key in numeric_keys and type(number) is int and 0 <= number < 10**15}
    for key in ('input_tokens_details', 'output_tokens_details'):
        if isinstance(value.get(key), dict):
            usage[key] = {k: v for k, v in value[key].items()
                          if k in numeric_keys and type(v) is int and 0 <= v < 10**15}
    return usage


def generate(prompt: str, output_dir, *, allow_paid: bool = False,
             model: str = MODELS[0], frame_count: int = 9, frame_duration_ms: int = 200,
             size: str = 'auto', reference=None) -> dict:
    """Make exactly one paid request, writing only into a newly reserved folder.

    Explicit allow_paid=True and OPENAI_API_KEY are required. On failure the
    reserved folder remains, preventing an accidental repeat against that path.
    """
    if allow_paid is not True:
        raise GifProviderError('Paid generation requires explicit allow_paid=True.')
    prepared = build_request(prompt, model=model, frame_count=frame_count,
                             frame_duration_ms=frame_duration_ms, size=size, reference=reference)
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key:
        raise GifProviderError('OPENAI_API_KEY is required; ChatGPT login is not API access.')
    out = Path(output_dir)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError:
        raise GifProviderError('Output directory must be new and writable; nothing was sent.') from None
    request = urllib.request.Request(prepared['endpoint'],
        data=json.dumps(prepared['payload']).encode('utf-8'), method='POST',
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=180) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise GifProviderError(f'OpenAI HTTP {status}; no retry performed. Check API access, quota, or service status.') from None
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError):
        raise GifProviderError('OpenAI request failed or timed out; outcome may be uncertain. No retry performed.') from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise GifProviderError('OpenAI response exceeds byte limit; no retry performed.')
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get('data'), list) or len(result['data']) != 1:
            raise ValueError
        item = result['data'][0]
        if not isinstance(item, dict) or not isinstance(item.get('b64_json'), str):
            raise ValueError
        gif = base64.b64decode(item['b64_json'], validate=True)
    except (ValueError, TypeError, binascii.Error, UnicodeError, RecursionError):
        raise GifProviderError('OpenAI returned an invalid GIF response; no retry performed.') from None
    observed = _validate_gif(gif)
    mismatch_warnings = []
    if observed['frame_count'] != frame_count:
        mismatch_warnings.append(
            f"Observed frame count {observed['frame_count']} differs from requested {frame_count}.")
    requested_duration = prepared['payload']['frame_duration_ms']
    if any(duration != requested_duration for duration in observed['durations_ms']):
        mismatch_warnings.append(
            f'Observed frame durations differ from requested {requested_duration} ms.')
    metadata = {'provider': 'openai', 'endpoint': prepared['endpoint'], 'model': model,
                'request': {k: v for k, v in prepared['payload'].items() if k != 'images'},
                'reference_supplied': reference is not None, 'observed': observed,
                'source_sha256': hashlib.sha256(gif).hexdigest(), 'warnings': mismatch_warnings,
                'usage': _safe_usage(result.get('usage')), 'status': 'awaiting_visual_review'}
    if reference is not None:
        # Fingerprint the validated bytes actually sent, never reread a path
        # that could have changed while the paid request was in progress.
        data_url = prepared['payload']['images'][0]['image_url']
        metadata['reference_sha256'] = hashlib.sha256(
            base64.b64decode(data_url.split(',', 1)[1], validate=True)).hexdigest()
    if type(result.get('created')) is int:
        metadata['created'] = result['created']
    gif_path, metadata_path = out / 'source.gif', out / 'provider.json'
    try:
        with gif_path.open('xb') as stream:
            stream.write(gif)
        with metadata_path.open('x', encoding='utf-8') as stream:
            json.dump(metadata, stream, indent=2)
            stream.write('\n')
    except OSError:
        raise GifProviderError('Generation returned, but saving failed. Inspect the output folder before any retry.') from None
    return {'gif_path': str(gif_path), 'metadata_path': str(metadata_path), 'metadata': metadata}
