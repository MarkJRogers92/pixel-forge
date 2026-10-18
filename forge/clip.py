"""Deterministic import of an explicitly ordered, equal-canvas PNG clip.

Unlike single-image cleanup, this path never fits a frame's content bbox,
removes a background, estimates a per-frame palette, or pins a foot position.
All frames share the same full-canvas transform and RGBA palette. No provider
or Aseprite process is needed; visual approval remains a separate step.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from . import manifest, profiles
from .cleanup import MAX_INPUT_BYTES, MAX_INPUT_PIXELS, map_to_palette
from .sprite import KEY_CHARS, MAX_SIDE, TRANSPARENT, Sprite, parse_hex, to_hex

MAX_FRAMES = 256
MAX_CLIP_BYTES = 128 * 1024 * 1024
MAX_SOURCE_PIXELS = 64 * 1024 * 1024
MAX_OUTPUT_PIXELS = 16 * 1024 * 1024


def _load_frames(paths):
    images, sources = [], []
    total_bytes = total_pixels = 0
    for path in paths:
        path = Path(path).expanduser()
        if not path.is_file():
            raise ValueError(f"frame is not a file: {path}")
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"frame file too large: {path}")
        data = path.read_bytes()
        total_bytes += len(data)
        if len(data) > MAX_INPUT_BYTES or total_bytes > MAX_CLIP_BYTES:
            raise ValueError("clip input byte limit exceeded")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise ValueError(f"frame must be a single PNG image: {path}")
                total_pixels += image.width * image.height
                if image.width * image.height > MAX_INPUT_PIXELS or total_pixels > MAX_SOURCE_PIXELS:
                    raise ValueError("clip input pixel limit exceeded")
                if images and image.size != images[0].size:
                    raise ValueError(f"all frames must have the same canvas; {path} is {image.size}, "
                                     f"expected {images[0].size}")
                rgba = image.convert("RGBA")
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError(f"cannot read PNG frame: {path}") from exc
        if manifest.content_bbox(rgba) is None:
            raise ValueError(f"empty frame: {path}")
        images.append(rgba)
        sources.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                        "pixel_size": list(rgba.size)})
    return images, sources


def _transform(source_size, width, height):
    for name, value in (("width", width), ("height", height)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or not 1 <= value <= MAX_SIDE):
            raise ValueError(f"{name} must be an integer in 1..{MAX_SIDE}")
    sw, sh = source_size
    tw = width if width is not None else (max(1, round(sw * height / sh)) if height else sw)
    th = height if height is not None else (max(1, round(sh * width / sw)) if width else sh)
    if not (1 <= tw <= MAX_SIDE and 1 <= th <= MAX_SIDE):
        raise ValueError(f"target canvas must be within 1..{MAX_SIDE} per side")
    scale = min(tw / sw, th / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    return {"source_canvas": [sw, sh], "target_canvas": [tw, th],
            "resized_canvas": [nw, nh], "offset": [(tw - nw) // 2, (th - nh) // 2],
            "scale": scale, "actual_scale": [nw / sw, nh / sh], "resampling": "nearest",
            "basis": "full source canvas, shared by every frame"}


def _fixed_palette(palette, profile):
    if palette is not None and profile is not None:
        raise ValueError("choose either palette or profile")
    if profile is not None:
        profile_palette = (profiles.load(profile) or {}).get("palette")
        if not isinstance(profile_palette, dict) or not profile_palette:
            raise ValueError("profile must contain a nonempty palette mapping")
        palette = list(profile_palette.values())
    if palette is None:
        return None
    if isinstance(palette, (str, bytes)) or not 1 <= len(palette) <= len(KEY_CHARS):
        raise ValueError(f"palette must contain 1..{len(KEY_CHARS)} opaque hex colours")
    colors = []
    for value in palette:
        if not isinstance(value, str) or not re.fullmatch(
                r"#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value.strip()):
            raise ValueError("palette entries must be hex colours")
        rgba = parse_hex(value)
        if rgba[3] != 255:
            raise ValueError("fixed palette colours must be opaque; source alpha is preserved separately")
        if rgba[:3] not in colors:
            colors.append(rgba[:3])
    return np.array(colors, dtype=np.uint8)


def _map_colors(arr, fixed):
    # Transparent RGB has no visible meaning and cannot be represented by the
    # document's single transparent key. Keep every nonzero alpha byte intact.
    arr = arr.copy()
    arr[arr[..., 3] == 0] = 0
    if fixed is not None:
        flat = arr.reshape(-1, 4)
        # Bound Lab nearest-colour working memory even on a large canvas.
        for start in range(0, len(flat), 4096):
            chunk = flat[start:start + 4096]
            indices = map_to_palette(chunk[None, ...], fixed)[0]
            visible = indices >= 0
            chunk[visible, :3] = fixed[indices[visible]]
    return arr


def import_clip(paths, out, *, width=None, height=None, fps=10.0, loop="forward",
                palette=None, profile=None, tag="clip") -> dict:
    """Export PNG frames in caller order to a new folder, with measured QA.

    Without a palette, preserve visible RGBA colours exactly (up to the sprite
    document's 62-colour limit across the clip). A fixed palette maps RGB only;
    alpha is always retained. Excess RGBA variants are rejected, never silently
    quantized. Width/height fit the whole source canvas, not individual bodies.
    """
    if isinstance(paths, (str, bytes, Path)):
        raise ValueError("pass an ordered sequence of PNG paths")
    paths = list(paths)
    if not 1 <= len(paths) <= MAX_FRAMES:
        raise ValueError(f"clip needs 1..{MAX_FRAMES} ordered frames")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be positive and finite")
    if loop not in manifest.LOOPS:
        raise ValueError(f"loop must be one of {manifest.LOOPS}")
    if not isinstance(tag, str) or not tag.strip():
        raise ValueError("tag must be a nonempty animation name")
    out = Path(out).expanduser()
    if out.exists():
        raise ValueError(f"output already exists; choose a new folder: {out}")
    fixed = _fixed_palette(palette, profile)
    images, sources = _load_frames(paths)
    transform = _transform(images[0].size, width, height)
    tw, th = transform["target_canvas"]
    if tw * th * len(images) > MAX_OUTPUT_PIXELS:
        raise ValueError("clip output pixel limit exceeded")

    arrays, records, warnings = [], [], []
    colors = set()
    for i, image in enumerate(images):
        fid = f"f{i}"
        source_bbox = manifest.content_bbox(image)
        canvas = Image.new("RGBA", (tw, th))
        canvas.paste(image.resize(tuple(transform["resized_canvas"]), Image.Resampling.NEAREST),
                     tuple(transform["offset"]))
        arr = _map_colors(np.asarray(canvas), fixed)
        bbox = manifest.content_bbox(arr)
        if bbox is None:
            raise ValueError(f"empty frame after resizing: {paths[i]}; use a larger target canvas")
        arrays.append(arr)
        colors.update(map(tuple, np.unique(arr[arr[..., 3] > 0], axis=0).tolist()))
        if len(colors) > len(KEY_CHARS):
            raise ValueError(f"clip exceeds {len(KEY_CHARS)} shared RGBA colours; choose a smaller "
                             "fixed palette or prepare fewer alpha variants explicitly")
        records.append({"id": fid, "source_bbox": source_bbox, "output_bbox": bbox})
        sw, sh = image.size
        if (source_bbox[0] == 0 or source_bbox[1] == 0 or source_bbox[2] == sw - 1
                or source_bbox[3] == sh - 1):
            warnings.append({"code": "possible_clipping", "frame": fid,
                             "message": "Source content touches the canvas edge; inspect for cutoff. "
                                        "This may be intentional."})
        alpha = np.asarray(image)[..., 3]
        if (alpha == 255).all():
            warnings.append({"code": "opaque_frame", "frame": fid,
                             "message": "Source is fully opaque; its background is retained."})
        if ((alpha > 0) & (alpha < 255)).any():
            warnings.append({"code": "soft_alpha", "frame": fid,
                             "message": "Source has partial transparency; alpha is retained."})

    # One deterministic RGBA lookup shared by every Sprite, including colours
    # absent from an individual frame. No per-frame palette estimation occurs.
    ordered = sorted(colors)
    lookup = {rgba: KEY_CHARS[i] for i, rgba in enumerate(ordered)}
    shared_palette = {lookup[rgba]: to_hex(rgba) for rgba in ordered}
    frames = {}
    for i, arr in enumerate(arrays):
        grid = [[lookup[tuple(px)] if px[3] else TRANSPARENT for px in row] for row in arr]
        frames[f"f{i}"] = Sprite(tw, th, dict(shared_palette), grid)
    difference = np.any(arrays[0] != arrays[-1], axis=2)
    endpoints = {"equal": not bool(difference.any()), "different_pixels": int(difference.sum()),
                 "note": "Pixel comparison only; matching poses do not prove motion continuity."}
    if not endpoints["equal"]:
        warnings.append({"code": "endpoint_mismatch",
                         "message": "First and last output frames differ; inspect the transition. "
                                    "Frames have not been replaced or reordered."})

    # Refuse collisions, including an output created since the initial check.
    out.mkdir(parents=True, exist_ok=False)
    exported = manifest.export_manifest(out, frames["f0"], frames=frames,
        reference=sources[0], alignment="canvas", generation=None, profile=profile,
        animation={"name": tag, "fps": float(fps), "loop": loop})
    verified = manifest.verify_export(out, exported)
    if not verified["ok"]:
        raise ValueError(f"clip export verification failed: {verified['errors']}")
    with Image.open(out / "sheet.png") as sheet:
        sheet.resize((sheet.width * 2, sheet.height * 2), Image.Resampling.NEAREST).save(out / "sheet@2x.png")
    report = {"schema": "pixel-forge/clip@1", "folder": str(out), "frame_count": len(frames),
              "fps": float(fps), "loop": loop, "tag": tag, "sources": sources,
              "transform": transform, "frames": records, "endpoints": endpoints,
              "palette": {"mode": "fixed" if fixed is not None else "exact", "profile": profile,
                          "requested_rgb": [to_hex(c) for c in fixed] if fixed is not None else None,
                          "rgba": shared_palette, "alpha": "preserved; transparent RGB normalized to zero"},
              "warnings": warnings, "verified": verified,
              "review": {"status": "awaiting_visual_review", "sheets": ["sheet.png", "sheet@2x.png"],
                         "instructions": "Inspect frames at 1x/2x for identity, motion, clipping and colour "
                                         "flicker. Numeric QA is not visual approval."}}
    (out / "clip-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
