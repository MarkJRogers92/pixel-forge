"""Opt-in PixelLab BitForge style and palette comparison tools.

The CLI trial is an explicit two-call comparison; the optional route is a
single guided draft used only when specifically selected by a caller.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from . import library, profiles, qa, routes
from .cleanup import CleanupOptions, cleanup, load_image
from .sprite import parse_hex

ENDPOINT = f"{routes.PIXELLAB_API}/v2/create-image-bitforge"
MAX_REFERENCE_BYTES = 25 * 1024 * 1024
MAX_REFERENCE_PIXELS = 4096 * 4096
NORMALIZED_SIDE = 512
MAX_NORMALIZED_PNG_BYTES = 4 * 1024 * 1024
_SECRET_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,;]+")


class StyleTrialError(ValueError):
    pass


def _safe_png(source: bytes) -> tuple[Image.Image, bytes, tuple[int, int]]:
    if len(source) > MAX_REFERENCE_BYTES:
        raise StyleTrialError("reference image exceeds the 25 MiB input limit")
    try:
        with Image.open(io.BytesIO(source)) as opened:
            width, height = opened.size
            if width < 1 or height < 1 or width * height > MAX_REFERENCE_PIXELS:
                raise StyleTrialError("reference image dimensions exceed the 4096x4096 limit")
            source_dimensions = (width, height)
            image = opened.convert("RGBA")
    except StyleTrialError:
        raise
    except Exception as exc:
        raise StyleTrialError("reference must be a readable local image") from exc
    image.thumbnail((NORMALIZED_SIDE, NORMALIZED_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    if len(data) > MAX_NORMALIZED_PNG_BYTES:
        raise StyleTrialError("normalized reference image is too large")
    return image, data, source_dimensions


def _profile_reference(profile: dict, override: str | Path | None = None) -> tuple[Image.Image, bytes, str, tuple[int, int]]:
    if override is not None:
        path = Path(override).expanduser().resolve()
    else:
        # Prefer profile configuration over the filtered reference_paths from
        # profiles.load(), so a missing configured file is an explicit error.
        configured = profile.get("references")
        if not isinstance(configured, list) or not configured:
            raise StyleTrialError("the selected profile must configure at least one local reference image")
        base = profiles.ROOT.resolve()
        path = (base / str(configured[0])).resolve()
        if base not in path.parents:
            raise StyleTrialError("profile reference must stay inside the profiles folder")
    if not path.is_file():
        raise StyleTrialError(f"reference image not found: {path}")
    try:
        if path.stat().st_size > MAX_REFERENCE_BYTES:
            raise StyleTrialError("reference image exceeds the 25 MiB input limit")
        image, png, source_dimensions = _safe_png(path.read_bytes())
    except OSError as exc:
        raise StyleTrialError(f"could not read reference image: {path}") from exc
    return image, png, str(path), source_dimensions


def _palette_colors(profile: dict) -> list[tuple[int, int, int]]:
    raw = profile.get("palette")
    if isinstance(raw, dict):
        values = list(raw.values())
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        values = []
    if not values:
        raise StyleTrialError("the selected profile must contain a non-empty palette")
    if len(values) > 256:
        raise StyleTrialError("profile palette is too large (maximum 256 colors)")
    colors = []
    try:
        for value in values:
            colors.append(parse_hex(value)[:3])
    except (TypeError, ValueError) as exc:
        raise StyleTrialError("profile palette colors must be valid hex colors") from exc
    return colors


def _palette_png(colors: list[tuple[int, int, int]], width: int, height: int) -> bytes:
    if len(colors) > width * height:
        raise StyleTrialError("palette has more colors than the requested canvas has pixels")
    rows = min(height, max(1, round((len(colors) * height / width) ** 0.5)))
    columns = (len(colors) + rows - 1) // rows
    while columns > width:
        rows += 1
        columns = (len(colors) + rows - 1) // rows
    image = Image.new("RGB", (width, height), colors[0])
    draw = ImageDraw.Draw(image)
    for index, color in enumerate(colors):
        col, row = index % columns, index // columns
        x0, x1 = col * width // columns, (col + 1) * width // columns - 1
        y0, y1 = row * height // rows, (row + 1) * height // rows - 1
        draw.rectangle((x0, y0, x1, y1), fill=color)
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _b64_image(png: bytes) -> dict[str, str]:
    return {"type": "base64", "base64": base64.b64encode(png).decode("ascii"), "format": "png"}


def _normalize_style_image(source: Image.Image, width: int, height: int) -> Image.Image:
    """Contain-fit to the canvas with nearest-neighbor pixels and transparent padding."""
    source = source.convert("RGBA")
    scale = min(width / source.width, height / source.height)
    fitted_size = (max(1, min(width, round(source.width * scale))),
                   max(1, min(height, round(source.height * scale))))
    fitted = source.resize(fitted_size, Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return canvas


def _provider_image(response: dict) -> tuple[bytes, Image.Image, str]:
    image = response.get("image") or {}
    encoded = image.get("base64")
    if not isinstance(encoded, str) or not encoded:
        raise StyleTrialError("PixelLab response did not include an image")
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator:
            raise StyleTrialError("PixelLab returned an invalid image data URL")
        media_type = header[5:].split(";", 1)[0].lower()
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(media_type, "png")
    else:
        extension = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "webp": "webp"}.get(
            str(image.get("format", "png")).lower(), "png")
    if len(encoded) > (MAX_REFERENCE_BYTES * 4 // 3 + 8):
        raise StyleTrialError("PixelLab image exceeds the encoded size limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.width * opened.height > MAX_REFERENCE_PIXELS:
                raise StyleTrialError("PixelLab image exceeds the 4096x4096 limit")
            decoded = opened.convert("RGBA")
    except StyleTrialError:
        raise
    except Exception as exc:
        raise StyleTrialError("PixelLab returned an invalid image") from exc
    return raw, decoded, extension


def _dimensions(width: int, height: int) -> dict[str, int]:
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
        raise StyleTrialError("width and height must be whole numbers")
    if not (16 <= width <= 200 and 16 <= height <= 200):
        raise StyleTrialError("BitForge dimensions must each be between 16 and 200 pixels")
    return {"width": width, "height": height}


def _description(prompt: str, profile: dict) -> str:
    text = profile.get("style") or ""
    notes = profile.get("notes") or profile.get("description") or ""
    if not isinstance(text, str) or not isinstance(notes, str):
        raise StyleTrialError("profile style and notes must be text")
    parts = [prompt.strip()]
    if text.strip():
        parts.append(f"Style direction: {text.strip()}")
    if notes.strip():
        parts.append(f"Additional notes: {notes.strip()}")
    return "\n\n".join(parts)


def build_payloads(prompt: str, width: int, height: int, profile: dict,
                   reference: str | Path | None = None, seed: int = 0) -> tuple[dict, dict, dict]:
    """Validate local inputs and return baseline/guided payloads plus safe summary."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise StyleTrialError("prompt must not be empty")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise StyleTrialError("seed must be a whole number")
    image_size = _dimensions(width, height)
    if not isinstance(profile, dict):
        raise StyleTrialError("a style profile is required")
    colors = _palette_colors(profile)
    reference_img, _source_reference_png, reference_path, source_dimensions = _profile_reference(profile, reference)
    normalized_reference = _normalize_style_image(reference_img, width, height)
    reference_buf = io.BytesIO()
    normalized_reference.save(reference_buf, format="PNG", optimize=True)
    reference_png = reference_buf.getvalue()
    palette_png = _palette_png(colors, width, height)
    common = {
        "description": _description(prompt, profile),
        "image_size": image_size,
        "no_background": True,
        "view": "high top-down",
        "outline": "single color black outline",
        "text_guidance_scale": 8,
        "seed": seed,
    }
    baseline = dict(common)
    guided = {**common, "style_image": _b64_image(reference_png), "style_strength": 50,
              "color_image": _b64_image(palette_png)}
    summary = {
        "endpoint": ENDPOINT,
        "dimensions": image_size,
        "reference": reference_path,
        "reference_source_dimensions": list(source_dimensions),
        "reference_normalized_dimensions": [width, height],
        "palette_count": len(colors),
        "seed": seed,
        "payload_sha256": {
            "text_baseline": hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "style_palette_guided": hashlib.sha256(json.dumps(guided, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        },
    }
    return baseline, guided, summary


def dry_run(prompt: str, width: int, height: int, profile: dict,
            reference: str | Path | None = None, seed: int = 0) -> dict:
    _, _, summary = build_payloads(prompt, width, height, profile, reference, seed)
    return {"status": "dry_run", **summary, "provider_calls": 0}


def _safe_error(exc: Exception, key: str) -> str:
    message = str(exc).replace(key, "[REDACTED]") if key else str(exc)
    message = _SECRET_PATTERN.sub(r"\1[REDACTED]", message)
    message = re.sub(r"[A-Za-z0-9+/]{160,}={0,2}", "[REDACTED_IMAGE_DATA]", message)
    return f"{type(exc).__name__}: {message[:500]}"


def _pixel_palette_measurement(image: Image.Image, colors: list[tuple[int, int, int]]) -> dict:
    import numpy as np
    from .cleanup import rgb_to_lab

    arr = np.asarray(image.convert("RGBA"))
    px = arr[arr[..., 3] > 0][:, :3]
    if not len(px):
        return {"opaque_pixels": 0, "exact_palette_percent": 0.0, "within_delta_e_10_percent": 0.0}
    palette = np.asarray(colors, dtype=np.uint8)
    exact = np.isin(px.view([("r", "u1"), ("g", "u1"), ("b", "u1")]),
                    palette.view([("r", "u1"), ("g", "u1"), ("b", "u1")])).reshape(-1)
    dist = np.linalg.norm(rgb_to_lab(px[:, None, :]) - rgb_to_lab(palette[None, :, :]), axis=2).min(axis=1)
    return {"opaque_pixels": int(len(px)), "exact_palette_percent": round(float(exact.mean() * 100), 2),
            "within_delta_e_10_percent": round(float((dist <= 10).mean() * 100), 2)}


def _save_provider_variant(response: dict, work: Path, name: str, width: int, height: int,
                           colors: list[tuple[int, int, int]]) -> tuple[dict, Image.Image]:
    raw_bytes, raw_image, raw_extension = _provider_image(response)
    raw_path = work / f"{name}_raw.{raw_extension}"
    raw_path.write_bytes(raw_bytes)
    clean_sprite, clean_report = cleanup(raw_image, CleanupOptions(
        width=width, height=height, max_colors=24, remove_background="auto",
        dark_bias=0.5, accent_share=0.2))
    clean_path = work / f"{name}.png"
    clean_sprite.save(clean_path)
    clean_image = clean_sprite.to_image()
    clean_image.resize((width * 2, height * 2), Image.Resampling.NEAREST).save(work / f"{name}@2x.png")
    clean_image.resize((width * 8, height * 8), Image.Resampling.NEAREST).save(work / f"{name}@8x.png")
    record = {
        "status": "completed", "raw_image": str(raw_path), "clean_png": str(clean_path),
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "cleanup": clean_report.__dict__, "qa": qa.check(clean_sprite),
        "palette_match": _pixel_palette_measurement(raw_image, colors),
        "usage": response.get("usage") if isinstance(response.get("usage"), (dict, str, int, float, type(None))) else None,
    }
    return record, clean_image


def _save_review_sheet(folder: Path, variants: list[dict], width: int, height: int) -> str | None:
    if not variants:
        return None
    row_scales = (1, 2, 8)
    pad, label_h = 12, 22
    cell_width = max(width * 8, 160)
    total_width = pad + len(variants) * (cell_width + pad)
    total_height = pad + sum(height * scale + label_h + pad for scale in row_scales)
    sheet = Image.new("RGBA", (total_width, total_height), (238, 238, 238, 255))
    draw = ImageDraw.Draw(sheet)
    y = pad
    for scale in row_scales:
        label = {1: "Native (1x)", 2: "2x nearest-neighbor", 8: "8x nearest-neighbor"}[scale]
        draw.text((pad, y), label, fill=(20, 20, 20, 255))
        y += label_h
        for index, variant in enumerate(variants):
            x = pad + index * (cell_width + pad)
            img = variant["clean_image"].convert("RGBA")
            if scale > 1:
                img = img.resize((width * scale, height * scale), Image.Resampling.NEAREST)
            sheet.alpha_composite(img, (x, y))
            if scale == 1:
                draw.text((x, y + height + 2), variant["name"], fill=(20, 20, 20, 255))
        y += height * scale + pad
    out = folder / "review.png"
    sheet.convert("RGB").save(out, "PNG", optimize=True)
    return str(out)


def run_trial(prompt: str, width: int, height: int, profile: dict,
              reference: str | Path | None = None, seed: int = 0,
              log: Callable[[str], None] = print) -> dict:
    """Make one baseline and one guided call, retaining all partial evidence."""
    baseline, guided, summary = build_payloads(prompt, width, height, profile, reference, seed)
    # Input validation and image normalization above deliberately precede key lookup.
    key = routes.api_key(routes.KEYS["pixellab"])
    if not key:
        raise StyleTrialError("set PIXELLAB_API_KEY to run the PixelLab style trial")
    item_id, folder = library.new_dir(prompt)
    work = folder / "work"
    work.mkdir()
    colors = _palette_colors(profile)
    meta = {
        "kind": "pixellab_style_trial", "status": "running", "prompt": prompt,
        "profile": profile.get("id"), "size": [width, height], "seed": seed,
        "endpoint": ENDPOINT, "reference": summary["reference"],
        "palette_count": len(colors), "payload_sha256": summary["payload_sha256"],
        "provider_calls": [], "variants": {},
    }
    meta_path = folder / "meta.json"

    def save_meta():
        meta_path.write_text(json.dumps(meta, indent=2, default=str))

    save_meta()
    variants = []
    for name, payload in (("text_baseline", baseline), ("style_palette_guided", guided)):
        call = {"variant": name, "status": "started", "seed": seed}
        meta["provider_calls"].append(call)
        save_meta()
        log(f"PixelLab {name}: submitting one BitForge generation")
        try:
            response = routes._post_json(ENDPOINT, {"Authorization": f"Bearer {key}"}, payload)
            if not isinstance(response, dict):
                raise StyleTrialError("PixelLab returned an invalid response")
            variant_result, clean_image = _save_provider_variant(response, work, name, width, height, colors)
            meta["variants"][name] = variant_result
            call.update({"status": "completed", "usage": variant_result["usage"]})
            variants.append({"name": name, "clean_image": clean_image})
        except Exception as exc:
            err = _safe_error(exc, key)
            meta["variants"][name] = {"status": "failed", "error": err}
            call.update({"status": "failed", "error": err})
            log(f"PixelLab {name} failed: {err}")
        # Persist each result before beginning the next potentially failing call.
        meta["review_sheet"] = _save_review_sheet(folder, variants, width, height)
        save_meta()

    succeeded = len(variants)
    meta["status"] = "awaiting_visual_review" if succeeded else "failed"
    meta["review"] = {
        "status": "awaiting_visual_review" if succeeded else "not_available",
        "instruction": "Compare both variants at native size and enlarged scales. Automated metrics are measurements, not an art-quality verdict.",
        "scales": [1, 2, 8],
    }
    save_meta()
    return {
        "library_id": item_id, "folder": str(folder), "status": meta["status"],
        "review_sheet": meta["review_sheet"], "variants": meta["variants"],
        "provider_calls": len(meta["provider_calls"]), "successful_variants": succeeded,
    }


def resume_guided_trial(library_id: str, log: Callable[[str], None] = print) -> dict:
    """Retry only a failed guided half of a saved pair, preserving its baseline."""
    folder = library.item_path(library_id).parent
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        raise StyleTrialError("library item has no trial metadata")
    try:
        meta = json.loads(meta_path.read_text())
    except Exception as exc:
        raise StyleTrialError("trial metadata is unreadable") from exc
    if meta.get("kind") != "pixellab_style_trial":
        raise StyleTrialError("library item is not a PixelLab style trial")
    variants = meta.get("variants") or {}
    baseline = variants.get("text_baseline") or {}
    guided_before = variants.get("style_palette_guided") or {}
    if guided_before.get("status") == "completed":
        return {"library_id": folder.name, "folder": str(folder), "status": meta.get("status"),
                "review_sheet": meta.get("review_sheet"), "variants": variants,
                "provider_calls": 0, "successful_variants": sum(v.get("status") == "completed" for v in variants.values()),
                "already_complete": True}
    if baseline.get("status") != "completed" or guided_before.get("status") != "failed":
        raise StyleTrialError("resume requires a completed text baseline and a failed guided variant")

    def local_file(value: str) -> Path:
        path = Path(value).resolve()
        if folder.resolve() not in path.parents or not path.is_file():
            raise StyleTrialError("saved trial image is missing or outside its library folder")
        return path

    baseline_clean_path = local_file(baseline.get("clean_png", ""))
    prompt = meta.get("prompt")
    profile_id = meta.get("profile")
    size = meta.get("size")
    if not isinstance(size, list) or len(size) != 2:
        raise StyleTrialError("trial metadata has invalid dimensions")
    width, height = size
    try:
        profile = profiles.load(profile_id)
    except Exception as exc:
        raise StyleTrialError("trial profile cannot be loaded") from exc
    if profile is None:
        raise StyleTrialError("trial metadata has no profile")
    reference = meta.get("reference")
    # Rebuild the payload from the saved prompt/profile/seed. Input validation
    # and normalized image creation happen before credentials are resolved.
    _, guided_payload, summary = build_payloads(prompt, width, height, profile, reference,
                                                seed=meta.get("seed", 0))
    saved_baseline_hash = (meta.get("payload_sha256") or {}).get("text_baseline")
    rebuilt_baseline_hash = summary["payload_sha256"]["text_baseline"]
    if not saved_baseline_hash or rebuilt_baseline_hash != saved_baseline_hash:
        raise StyleTrialError("saved text-baseline payload no longer matches; refusing an uncontrolled guided retry")
    key = routes.api_key(routes.KEYS["pixellab"])
    if not key:
        raise StyleTrialError("set PIXELLAB_API_KEY to resume the PixelLab style trial")

    prior_payload_hash = (meta.get("payload_sha256") or {}).get("style_palette_guided")
    attempt_history = meta.setdefault("variant_attempts", {})
    if "style_palette_guided" not in attempt_history:
        prior_attempt = dict(guided_before)
        prior_attempt.setdefault("attempt", 1)
        if prior_payload_hash:
            prior_attempt.setdefault("payload_sha256", prior_payload_hash)
        attempt_history["style_palette_guided"] = [prior_attempt]
        for old_call in meta.get("provider_calls", []):
            if old_call.get("variant") == "style_palette_guided" and old_call.get("status") == "failed":
                old_call.setdefault("attempt", 1)
                if prior_payload_hash:
                    old_call.setdefault("payload_sha256", prior_payload_hash)
    attempts = attempt_history["style_palette_guided"]
    attempt_no = len(attempts) + 1
    call = {"variant": "style_palette_guided", "attempt": attempt_no, "status": "started",
            "seed": meta.get("seed", 0), "payload_sha256": summary["payload_sha256"]["style_palette_guided"]}
    meta.setdefault("provider_calls", []).append(call)
    meta.setdefault("payload_sha256", {})["style_palette_guided"] = call["payload_sha256"]
    meta_path.write_text(json.dumps(meta, indent=2, default=str))
    log("PixelLab style_palette_guided: submitting one corrected BitForge generation")
    work = folder / "work"
    colors = _palette_colors(profile)
    try:
        response = routes._post_json(ENDPOINT, {"Authorization": f"Bearer {key}"}, guided_payload)
        if not isinstance(response, dict):
            raise StyleTrialError("PixelLab returned an invalid response")
        current, clean_image = _save_provider_variant(response, work, "style_palette_guided", width, height, colors)
        call.update({"status": "completed", "usage": current.get("usage")})
    except Exception as exc:
        error = _safe_error(exc, key)
        current = {"status": "failed", "attempt": attempt_no, "error": error}
        call.update({"status": "failed", "error": error})
        log(f"PixelLab guided resume failed: {error}")
        clean_image = None
    current["attempt"] = attempt_no
    current["payload_sha256"] = call["payload_sha256"]
    attempts.append(dict(current))
    variants["style_palette_guided"] = current
    meta["variants"] = variants
    baseline_image = Image.open(baseline_clean_path).convert("RGBA")
    sheet_variants = [{"name": "text_baseline", "clean_image": baseline_image}]
    if clean_image is not None:
        sheet_variants.append({"name": "style_palette_guided", "clean_image": clean_image})
    meta["review_sheet"] = _save_review_sheet(folder, sheet_variants, width, height)
    meta["status"] = "awaiting_visual_review"
    meta["review"] = {"status": "awaiting_visual_review", "scales": [1, 2, 8],
                       "instruction": "Compare the preserved baseline and guided result at native size and enlarged scales. Automated metrics are measurements, not an art-quality verdict."}
    meta_path.write_text(json.dumps(meta, indent=2, default=str))
    return {"library_id": folder.name, "folder": str(folder), "status": meta["status"],
            "review_sheet": meta["review_sheet"], "variants": variants,
            "provider_calls": 1, "total_provider_call_records": len(meta["provider_calls"]),
            "successful_variants": sum(v.get("status") == "completed" for v in variants.values()),
            "already_complete": False}


def guided_route(prompt: str, width: int, height: int, profile: dict | None):
    """Generate exactly one style-and-palette-guided sprite for --route use."""
    if profile is None:
        raise routes.RouteError("pixellab-style requires a profile with a local reference and palette")
    try:
        _, guided, _ = build_payloads(prompt, width, height, profile)
    except StyleTrialError as exc:
        raise routes.RouteError(str(exc)) from exc
    key = routes.api_key(routes.KEYS["pixellab"])
    if not key:
        raise routes.RouteError("set PIXELLAB_API_KEY to use the pixellab-style route")
    try:
        response = routes._post_json(ENDPOINT, {"Authorization": f"Bearer {key}"}, guided)
        if not isinstance(response, dict):
            raise StyleTrialError("PixelLab returned an invalid response")
        _, image, _ = _provider_image(response)
    except Exception as exc:
        safe = _safe_error(exc, key)
        raise routes.RouteError(f"PixelLab BitForge style route failed: {safe}") from None
    return routes._to_sprite(image, width, height, profile)
