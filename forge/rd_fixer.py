"""Free RD neural reconstruction. No generation endpoint, paid fallback or retry."""
from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request

from PIL import Image

from . import routes

MAX_REQUEST_BYTES = 900_000
MAX_RESPONSE_BYTES = 850_000
MAX_INPUT_PIXELS = 16_000_000
MAX_SIDE = 512


def validate_size(width: int, height: int) -> None:
    if any(type(n) is not int or not 1 <= n <= MAX_SIDE for n in (width, height)):
        raise ValueError(f"sprite dimensions must be integers from 1 to {MAX_SIDE}")


def require_key() -> str:
    key = routes.api_key(routes.KEYS["rd"])
    if not key:
        raise routes.RouteError("set RETRODIFFUSION_API_KEY to use the free RD neural fixer")
    return key


def _input_body(image: Image.Image, width: int, height: int) -> tuple[dict, dict, bytes]:
    if min(image.size) < 16 or image.width * image.height > MAX_INPUT_PIXELS:
        raise ValueError("fixer input must be at least 16x16 and at most 16 megapixels")
    uploaded = image.convert("RGBA")
    original_size = list(uploaded.size)
    while True:
        buf = io.BytesIO()
        uploaded.save(buf, "PNG", optimize=True)
        png = buf.getvalue()
        body = {"input_image": base64.b64encode(png).decode("ascii"), "width": width, "height": height}
        if len(json.dumps(body).encode()) <= MAX_REQUEST_BYTES:
            return body, {"source_size": original_size, "upload_size": list(uploaded.size),
                          "upload_format": "PNG", "resized_for_upload": list(uploaded.size) != original_size}, png
        if min(uploaded.size) <= 16:
            raise ValueError("fixer input cannot fit the upload limit without going below 16 pixels")
        factor = max(0.8, 16 / min(uploaded.size))
        size = tuple(max(16, int(n * factor)) for n in uploaded.size)
        uploaded = image.convert("RGBA").resize(size, Image.Resampling.LANCZOS)


def neural_fixer(image: Image.Image, width: int, height: int, *, key: str | None = None,
                 input_path=None) -> tuple[Image.Image, dict]:
    validate_size(width, height)
    key = key or require_key()
    body, report, png = _input_body(image, width, height)
    if input_path is not None:
        input_path.write_bytes(png)
    req = urllib.request.Request(f"{routes.RD_API}/v1/pixel-fixer/neural",
        data=json.dumps(body).encode(), method="POST",
        headers={"X-RD-Token": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # Do not include server bodies, headers, URLs or credentials in errors.
        raise routes.RouteError(f"RD neural fixer HTTP {exc.code}; no paid fallback or retry") from None
    except OSError:
        raise routes.RouteError("RD neural fixer connection failed; no paid fallback or retry") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("RD neural fixer response exceeds its size limit")
    try:
        response = json.loads(raw)
        images = response.get("base64_images") if isinstance(response, dict) else None
        if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], str):
            raise ValueError
        data = base64.b64decode(images[0], validate=True)
        with Image.open(io.BytesIO(data)) as decoded:
            if decoded.format != "PNG" or getattr(decoded, "n_frames", 1) != 1:
                raise ValueError
            if decoded.size != (width, height):
                raise ValueError("RD neural fixer returned unexpected dimensions")
            result = decoded.convert("RGBA")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        if str(exc) == "RD neural fixer returned unexpected dimensions":
            raise ValueError(str(exc)) from None
        raise ValueError("RD neural fixer returned an invalid single PNG response") from None
    report.update({"output_size": list(result.size), "account_balance_spent": False})
    return result, report
