"""One explicit asset manifest for a Pixel Forge asset.

The manifest does not replace ``sprite.json`` (the sprite document) or
``meta.json`` (the run record): it is the contract those files leave implicit,
written in the same folder in the same style.  It links, in one place:

* the **reference** the asset was made from (path + sha256 + pixel size);
* the **canvas** (size, alignment, and OUR anchor/attachment contract);
* the **directions**, **states** and **animations** it supports;
* the **outputs** (exported files, sheet layout, frame ordering);
* the **generation settings**: which backend operation produced it and the
  parameters that backend actually sends.

Design rules:

* Canvas size and frame ordering are the adapter contract; ``verify_export``
  proves both survive a round trip through the exported files.
* Our anchor/alignment contract is OURS.  PixelLab demonstrates directions and
  server-side character/object ids; it does not define a pivot, skeleton or
  seed contract for this project, so none is invented here.
* A control the active backend does not implement is *flagged* in
  ``generation.unsupported`` - never relabelled, defaulted or simulated.

Nothing here calls a provider. Generation is remote and credit-spending; the
manifest records the boundary instead of crossing it.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .sprite import Sprite

SCHEMA = "pixel-forge/manifest@1"
COORDINATE_SPACE = "canvas-top-left-pixel"      # x right, y down, pixel (0,0) top-left
ALIGNMENTS = ("canvas", "tight")
LAYOUTS = ("grid",)
# The keywords forge.normalmap.normal_map takes today. The manifest records the
# request it actually sends; anything else in it is listed as ignored, never
# dropped silently.
NORMAL_MAP_PARAMS = ("height_source", "blur", "invert_y", "transparent_background",
                     "rim", "contour", "detail")
DEFAULT_DIRECTION = "south"
DEFAULT_ANCHOR = "bottom-center"
ANCHOR_PRESETS = ("bottom-center", "bottom-left", "bottom-right", "center", "top-left")
LOOPS = ("forward", "reverse", "pingpong")

# --------------------------------------------------------------------------- #
# What each wired backend operation actually sends.
# The parameter tuples are asserted against the live payloads in
# tests/test_manifest.py::test_capability_table_matches_what_each_backend_sends
# so this table cannot drift away from forge/routes.py and forge/pixellab_style.py.
#
# "endpoint" is the URL the code posts to; "documented_endpoint" is what the
# public API map (https://www.pixellab.ai/pixellab-api, inspected 2026-10-12)
# lists for the same operation. They differ for pixflux (code v1, map v2) - the
# manifest records both rather than hiding the gap.
# --------------------------------------------------------------------------- #
BACKENDS: dict[str, dict] = {
    "pixellab-pixflux": {
        "backend": "pixellab",
        "operation": "create-image/pixflux",
        "endpoint": "https://api.pixellab.ai/v1/generate-image-pixflux",
        "documented_endpoint": "POST /v2/create-image-pixflux",
        "parameters": ("description", "image_size", "no_background", "view", "outline",
                       "text_guidance_scale"),
        "size_limit": {"min": 32, "max": 400},
        "documented_size_limit": {"min": 32, "max": 400, "note": "maximum area 400x400"},
        "code": "forge/routes.py:86-98 (routes.pixellab)",
    },
    "pixellab-bitforge": {
        "backend": "pixellab",
        "operation": "create-image/bitforge",
        "endpoint": "https://api.pixellab.ai/v2/create-image-bitforge",
        "documented_endpoint": "POST /v2/create-image-bitforge",
        "parameters": ("description", "image_size", "no_background", "view", "outline",
                       "text_guidance_scale", "seed", "style_image", "style_strength",
                       "color_image"),
        "size_limit": {"min": 16, "max": 200},
        "documented_size_limit": {"min": 16, "max": 200, "note": "maximum area 200x200"},
        "code": "forge/pixellab_style.py:205-216, 489-508",
    },
    "rd-rd_fast": {
        "backend": "retro-diffusion",
        "operation": "inference/rd_fast__game_asset",
        "endpoint": "https://api.retrodiffusion.ai/v2/inferences",
        "documented_endpoint": "https://api.retrodiffusion.ai/v2/inferences",
        "parameters": ("prompt", "prompt_style", "width", "height", "num_images", "remove_bg"),
        "size_limit": {"min": 64, "max": 512},
        "documented_size_limit": None,
        "code": "forge/routes.py:109-131 (routes.retro_diffusion)",
    },
}

# Operations the public PixelLab API map lists that Pixel Forge has NOT wired.
# Recorded with their documented endpoint and bound so a request for one is
# refused with a concrete reason, never silently faked.
PIXELLAB_API_MAP = "https://www.pixellab.ai/pixellab-api"
_NOT_WIRED = "no Pixel Forge backend is wired for it"
PIXELLAB_DOCUMENTED_OPERATIONS: dict[str, dict] = {
    "create-image-pixen": {"endpoint": "POST /v2/create-image-pixen", "bound": "maximum area 512x512"},
    "image-to-pixelart": {"endpoint": "POST /v2/image-to-pixelart", "bound": "output up to 320x320"},
    "resize": {"endpoint": "POST /v2/resize", "bound": "16x16 to 200x200"},
    "remove-background": {"endpoint": "POST /v2/remove-background", "bound": "maximum area 400x400"},
    "animate-with-text": {"endpoint": "POST /v2/animate-with-text", "bound": "64x64, 4 frames"},
    "animate-with-text-v3": {"endpoint": "POST /v2/animate-with-text-v3", "bound": "up to 256x256, up to 16 frames"},
    "animate-pixminimax": {"endpoint": "POST /v2/animate-pixminimax", "bound": "up to 256x256, 4-40 frames in multiples of 4"},
    "animate-with-skeleton": {"endpoint": "POST /v2/animate-with-skeleton", "bound": "up to 128x128"},
    "animate-with-skeleton-v3": {"endpoint": "POST /v2/animate-with-skeleton-v3", "bound": "3-15 frames, up to 256x256"},
    "estimate-skeleton": {"endpoint": "POST /v2/estimate-skeleton", "bound": "16x16 to 256x256"},
    "rotate": {"endpoint": "POST /v2/rotate", "bound": "up to 128x128"},
    "generate-8-rotations-v3": {"endpoint": "POST /v2/generate-8-rotations-v3",
                                "bound": "up to 256x256, from a SOUTH-facing reference"},
    "inpaint": {"endpoint": "POST /v2/inpaint", "bound": "maximum area 200x200"},
    "edit-image": {"endpoint": "POST /v2/edit-image", "bound": "reference 16x16 to 400x400"},
    "create-tileset": {"endpoint": "POST /v2/create-tileset", "bound": "16x16 or 32x32 tiles"},
    "create-tileset-sidescroller": {"endpoint": "POST /v2/create-tileset-sidescroller", "bound": "16x16 or 32x32 tiles"},
    "create-isometric-tile": {"endpoint": "POST /v2/create-isometric-tile", "bound": "16x16 to 64x64"},
    "map-objects": {"endpoint": "POST /v2/map-objects", "bound": "per object"},
    "create-character-with-4-directions": {"endpoint": "POST /v2/create-character-with-4-directions",
                                           "bound": "south, west, east, north; saved in the Character creator"},
    "create-character-with-8-directions": {"endpoint": "POST /v2/create-character-with-8-directions",
                                           "bound": "48x48 to 64x64; saved in the Character creator"},
    "create-character-pro": {"endpoint": "POST /v2/create-character-pro", "bound": "8 directions, up to 168x168"},
    "create-character-v3": {"endpoint": "POST /v2/create-character-v3",
                            "bound": "8 directions from a SOUTH-facing reference; saved server-side"},
    "animate-character": {"endpoint": "POST /v2/animate-character",
                          "bound": "child resource of a saved character"},
    "create-character-state": {"endpoint": "POST /v2/create-character-state",
                               "bound": "child resource of a saved character"},
    "create-1-direction-object": {"endpoint": "POST /v2/create-1-direction-object",
                                  "bound": "up to 168x168; saved in the Object creator"},
    "create-8-direction-object": {"endpoint": "POST /v2/create-8-direction-object",
                                  "bound": "up to 168x168; saved in the Object creator"},
    "objects/{object_id}/animations": {"endpoint": "POST /v2/objects/{object_id}/animations",
                                       "bound": "child resource of a saved object"},
    "objects/{object_id}/states": {"endpoint": "POST /v2/objects/{object_id}/states",
                                   "bound": "child resource of a saved object"},
    "generate-image-v2": {"endpoint": "POST /v2/generate-image-v2", "bound": "canvas 16-512px (Pro)"},
    "generate-with-style-v2": {"endpoint": "POST /v2/generate-with-style-v2", "bound": "16-512px (Pro)"},
    "generate-ui-v2": {"endpoint": "POST /v2/generate-ui-v2", "bound": "up to 512x512 (Pro)"},
    "animate-with-text-v2": {"endpoint": "POST /v2/animate-with-text-v2", "bound": "4, 9 or 16 frames (Pro)"},
    "edit-animation-v2": {"endpoint": "POST /v2/edit-animation-v2", "bound": "2-16 frames (Pro)"},
    "interpolation-v2": {"endpoint": "POST /v2/interpolation-v2", "bound": "up to 256x256 (Pro)"},
    "transfer-outfit-v2": {"endpoint": "POST /v2/transfer-outfit-v2", "bound": "up to 256x256 (Pro)"},
    "generate-8-rotations-v2": {"endpoint": "POST /v2/generate-8-rotations-v2", "bound": "up to 168x168 (Pro)"},
    "inpaint-v3": {"endpoint": "POST /v2/inpaint-v3", "bound": "up to 512x512 (Pro)"},
    "edit-images-v2": {"endpoint": "POST /v2/edit-images-v2", "bound": "up to 512x512 (Pro)"},
    "create-tiles-pro": {"endpoint": "POST /v2/create-tiles-pro", "bound": "tile up to 85x85 (Pro)"},
    "create-image-pro-flash": {"endpoint": "POST /v2/create-image-pro-flash", "bound": "custom sizes in 4px increments (Pro Flash)"},
    "edit-image-pro-flash": {"endpoint": "POST /v2/edit-image-pro-flash", "bound": "32-256px per side in 4px increments"},
    "inpaint-image-pro-flash": {"endpoint": "POST /v2/inpaint-image-pro-flash", "bound": "32-256px per side in 4px increments"},
    "image-to-pixelart-pro-flash": {"endpoint": "POST /v2/image-to-pixelart-pro-flash", "bound": "sources up to 2048px"},
    "create-character-pro-flash": {"endpoint": "POST /v2/create-character-pro-flash", "bound": "8 directions in one request"},
    "create-object-pro-flash": {"endpoint": "POST /v2/create-object-pro-flash", "bound": "1 or 8 directions in one request"},
}

# Control names the PixelLab documentation exposes that this project does not
# send. Used only to give an unsupported parameter an accurate reason.
PIXELLAB_DOCUMENTED_PARAMETERS = frozenset({
    "seed", "negative_description", "direction", "oblique_projection", "init_image",
    "shading", "detail", "isometric", "style_image", "style_strength", "color_image",
    "inpainting_image", "mask_image", "frame_count", "frames", "animation", "skeleton",
    "rotate", "remove_background", "no_background", "image_size", "description",
})


class ManifestError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #
def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reference_record(path: str | Path, base: str | Path | None = None) -> dict:
    """Path + sha256 + pixel size of the reference image, like the game's own manifest."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ManifestError(f"reference image not found: {p}")
    with Image.open(p) as im:
        size = [int(im.width), int(im.height)]
    shown = str(p)
    if base is not None:
        try:
            shown = str(p.resolve().relative_to(Path(base).resolve()))
        except ValueError:
            shown = str(p)
    return {"path": shown, "sha256": sha256_file(p), "pixel_size": size}


def anchor_pixel(preset: str, width: int, height: int) -> tuple[int, int]:
    """Anchor as an integer pixel index in canvas space (x right, y down)."""
    if preset not in ANCHOR_PRESETS:
        raise ManifestError(f"anchor must be one of {ANCHOR_PRESETS}")
    if preset == "bottom-center":
        return width // 2, height - 1
    if preset == "bottom-left":
        return 0, height - 1
    if preset == "bottom-right":
        return width - 1, height - 1
    if preset == "center":
        return width // 2, height // 2
    return 0, 0


def anchor_record(preset: str, width: int, height: int) -> dict:
    x, y = anchor_pixel(preset, width, height)
    return {"preset": preset, "x": int(x), "y": int(y), "space": COORDINATE_SPACE,
            "origin_note": "pixel the renderer registers the sprite by; the game draws props with "
                           "setOrigin(0.5, 1) (bottom-centre ground contact)"}


def content_bbox(image) -> list[int] | None:
    """Inclusive [x0, y0, x1, y1] of everything with alpha > 0, or None if empty."""
    arr = _rgba_array(image)
    ys, xs = np.nonzero(arr[..., 3] > 0)
    if not len(xs):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def _rgba_array(image) -> np.ndarray:
    if isinstance(image, Image.Image):
        return np.asarray(image.convert("RGBA")).astype(np.uint8)
    if isinstance(image, Sprite):
        return image.to_rgba().astype(np.uint8)
    arr = np.asarray(image)
    if arr.ndim != 3 or arr.shape[2] != 4:
        raise ManifestError("expected an RGBA image")
    return arr.astype(np.uint8)


# --------------------------------------------------------------------------- #
# generation settings
# --------------------------------------------------------------------------- #
def _unsupported_reason(name: str) -> str:
    if name in PIXELLAB_DOCUMENTED_PARAMETERS:
        return ("documented PixelLab control that the active Pixel Forge operation does not send; "
                "recorded as unsupported rather than simulated")
    return ("not a control of the active operation; recorded as unsupported rather than sent "
            "or relabelled")


def generation_record(backend: str, operation: str | None = None,
                      parameters: dict | None = None) -> dict:
    """The generation settings, with every unsupported control flagged."""
    if backend not in BACKENDS:
        raise ManifestError(f"unknown backend {backend!r}; choose from {sorted(BACKENDS)}")
    spec = BACKENDS[backend]
    op = operation or spec["operation"]
    if op != spec["operation"]:
        doc = PIXELLAB_DOCUMENTED_OPERATIONS.get(op)
        if doc is None:
            raise ManifestError(f"unknown operation {op!r} for backend {backend!r}")
        reason = (f"documented at {doc['endpoint']} ({doc['bound']}); {_NOT_WIRED}, so the request is "
                  f"recorded instead of simulated")
        return {
            "backend": spec["backend"], "operation": op, "supported": False,
            "reason": reason, "endpoint": None, "parameters": {}, "parameters_sent": [],
            "unsupported": [{"name": op, "value": None, "reason": reason,
                             "documented_endpoint": doc["endpoint"], "documented_bound": doc["bound"]}],
            "reference": PIXELLAB_API_MAP,
        }
    supported, unsupported = {}, []
    for name, value in dict(parameters or {}).items():
        if name in spec["parameters"]:
            supported[name] = value
        else:
            unsupported.append({"name": name, "value": value, "reason": _unsupported_reason(name)})
    return {
        "backend": spec["backend"], "operation": op, "supported": True,
        "endpoint": spec["endpoint"], "documented_endpoint": spec["documented_endpoint"],
        "parameters": supported, "parameters_sent": [n for n in spec["parameters"] if n in supported],
        "unsupported": unsupported,
        "size_limit": spec["size_limit"], "documented_size_limit": spec["documented_size_limit"],
        "code": spec["code"],
    }


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def build_manifest(primary, *, frames=None, reference=None, direction=DEFAULT_DIRECTION,
                   directions=None, state="idle", anchor=DEFAULT_ANCHOR,
                   alignment="canvas", animation=None, generation=None,
                   normal_map=None, prompt=None, profile=None) -> dict:
    """Metadata skeleton. ``export_manifest`` adds the output records and writes files."""
    if not isinstance(primary, Sprite):
        raise ManifestError("primary must be a forge.sprite.Sprite")
    if alignment not in ALIGNMENTS:
        raise ManifestError(f"alignment must be one of {ALIGNMENTS}")
    if not isinstance(anchor, str) or anchor not in ANCHOR_PRESETS:
        raise ManifestError(f"anchor must be one of {ANCHOR_PRESETS}")
    frame_map = _frame_map(primary, frames)
    canvas_w, canvas_h = primary.width, primary.height
    first_frame = next(iter(frame_map))
    for fid, s in frame_map.items():
        if (s.width, s.height) != (canvas_w, canvas_h):
            raise ManifestError(f"frame {fid!r} is {s.width}x{s.height}; canvas is {canvas_w}x{canvas_h}")
    dirs = list(directions or [direction])
    if direction not in dirs:
        raise ManifestError(f"canonical direction {direction!r} must be in directions {dirs}")

    manifest: dict = {
        "schema": SCHEMA,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reference": reference,
        "canvas": {
            "width": canvas_w, "height": canvas_h,
            "coordinate_space": COORDINATE_SPACE,
            "alignment": alignment,
            "anchor": anchor_record(anchor, canvas_w, canvas_h),
            "content_bbox": None,
        },
        "directions": {"supported": dirs, "canonical_view": direction,
                       "note": "our list; PixelLab's canonical seed view is a SOUTH-facing "
                               "reference, which is why south is the default here"},
        "states": {state: {d: f"frames/{first_frame}.png" for d in dirs}},
        "animations": {},
        "outputs": [],
        "generation": generation,
        "contract": {
            "owner": "pixel-forge",
            "not_from_pixellab": ["anchor/pivot", "skeletons", "seeds", "8-direction set",
                                  "server-side asset ids"],
            "note": "the anchor and alignment above are this project's registration contract; "
                    "PixelLab demonstrates directions and saved characters/objects, it does not "
                    "define a pivot contract for us",
        },
    }
    if animation:
        loop = animation.get("loop", "forward")
        if loop not in LOOPS:
            raise ManifestError(f"loop must be one of {LOOPS}")
        fps = float(animation.get("fps", 10.0))
        if fps <= 0:
            raise ManifestError("fps must be positive")
        manifest["animations"][animation.get("name", "animation")] = {
            "fps": fps, "loop": loop,
            "frames": [{"id": fid, "path": f"frames/{fid}.png"} for fid in frame_map],
            "per_direction": {d: list(frame_map) for d in dirs},
        }
    if normal_map:
        manifest["normal_map_request"] = normal_map
    if prompt is not None:
        manifest["prompt"] = prompt
    if profile is not None:
        manifest["profile"] = profile
    if generation is not None:
        limit = BACKENDS.get(_backend_key(generation)) or {}
        bound = limit.get("size_limit")
        if bound:
            fits = bound["min"] <= canvas_w <= bound["max"] and bound["min"] <= canvas_h <= bound["max"]
            generation = manifest["generation"]
            generation["canvas_fits_operation_bound"] = fits
            if not fits:
                generation.setdefault("notes", []).append(
                    f"canvas {canvas_w}x{canvas_h} is outside this operation's documented bound "
                    f"{bound['min']}..{bound['max']} per side; the route clamps or refuses, it is "
                    f"not silently resized here")
    return manifest


def _backend_key(generation: dict) -> str | None:
    for key, spec in BACKENDS.items():
        if spec["backend"] == generation.get("backend") and spec["operation"] == generation.get("operation"):
            return key
    return None


def _frame_map(primary: Sprite, frames) -> dict:
    if frames is None:
        return {"f0": primary}
    if isinstance(frames, dict):
        out = dict(frames)
    else:
        out = {f"f{i}": s for i, s in enumerate(frames)}
    if not out:
        raise ManifestError("frames must not be empty")
    for fid, s in out.items():
        if not isinstance(s, Sprite):
            raise ManifestError(f"frame {fid!r} must be a forge.sprite.Sprite")
    return out


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def export_manifest(folder, primary: Sprite, *, frames=None, reference=None,
                    direction: str = DEFAULT_DIRECTION, anchor: str = DEFAULT_ANCHOR,
                    alignment: str = "canvas", animation=None, generation=None,
                    normal_map=None, prompt=None, profile=None, sheet_columns=None) -> dict:
    """Write the asset and its manifest into ``folder`` using the library item shape.

    Files: ``sprite.png``/``sprite@8x.png``/``sprite.json`` (the existing
    document), ``frames/<id>.png`` per animation frame, ``sheet.png`` +
    ``sheet.json``, ``manifest.json`` and ``meta.json``.
    """
    folder = Path(folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    frame_map = _frame_map(primary, frames)

    manifest = build_manifest(primary, frames=frame_map, reference=reference, direction=direction,
                              anchor=anchor, alignment=alignment, animation=animation,
                              generation=generation, normal_map=normal_map, prompt=prompt,
                              profile=profile)

    # --- frames (always canvas-sized; these are what the sheet is cut from) ---
    (folder / "frames").mkdir(exist_ok=True)
    for fid, sprite in frame_map.items():
        sprite.save(folder / "frames" / f"{fid}.png")

    # --- the canonical sprite, per alignment ---
    bbox = content_bbox(primary)
    if alignment == "tight":
        if bbox is None:
            raise ManifestError("cannot export a tight alignment for an empty sprite")
        x0, y0, x1, y1 = bbox
        cropped = primary.to_image().crop((x0, y0, x1 + 1, y1 + 1))
        cropped.save(folder / "sprite.png")
        manifest["canvas"]["content_bbox"] = bbox
        (folder / "sprite.json").write_text(json.dumps(
            Sprite.from_image(cropped).to_dict(), indent=1))
        written = cropped
    else:
        primary.save(folder / "sprite.png")
        (folder / "sprite.json").write_text(json.dumps(primary.to_dict(), indent=1))
        written = primary.to_image()
    # the review copy must be the same sprite we just wrote: in tight alignment it
    # used to be the full canvas, so sprite.png and sprite@8x.png disagreed.
    written.resize((written.width * 8, written.height * 8), Image.NEAREST).save(
        folder / "sprite@8x.png")

    # --- sheet + atlas ---
    order = list(frame_map)
    sheet_rec = _sheet_record(order, primary.width, primary.height, sheet_columns)
    sheet = Image.new("RGBA", tuple(sheet_rec["size"]), (0, 0, 0, 0))
    for fid in order:
        x, y = sheet_rec["frames"][fid]
        sheet.alpha_composite(frame_map[fid].to_image(), (x, y))
    sheet.save(folder / "sheet.png")
    sheet_rec["image"] = "sheet.png"
    (folder / "sheet.json").write_text(json.dumps(sheet_rec, indent=1))

    outputs = [{"kind": "sprite", "path": "sprite.png",
                "size": [primary.width, primary.height],
                "alignment": alignment, "content_bbox": manifest["canvas"]["content_bbox"]},
               {"kind": "sprite_document", "path": "sprite.json",
                "shape": ["width", "height", "palette", "rows"]},
               {"kind": "sheet", "path": "sheet.png", "data": "sheet.json",
                "layout": sheet_rec["layout"], "columns": sheet_rec["columns"],
                "cell": sheet_rec["cell"], "frame_order": order,
                "cells": sheet_rec["frames"]}]

    # --- optional normal-map output (TARGET 4) ---
    if normal_map:
        from . import normalmap as nm
        request = dict(normal_map) if isinstance(normal_map, dict) else {}
        params = {k: v for k, v in request.items() if k in NORMAL_MAP_PARAMS}
        path = "sprite-normal.png"
        nm.normal_map(primary.to_image(), **params).save(folder / path)
        record = {"kind": "normal_map", "path": path, "size": [primary.width, primary.height],
                  "encoding": {"r": "+x right",
                               "g": "+y up (standard; G > 128 faces up), flipped by invert_y",
                               "b": "+z out of the screen", "flat": list(nm.FLAT_RGB)},
                  "parameters": params,
                  "renderer_contract": "decode n = rgb/127.5 - 1, light with a direction in the "
                                       "same tangent frame; forge.lighting.lit_preview decodes it"}
        ignored = sorted(k for k in request if k not in NORMAL_MAP_PARAMS)
        if ignored:
            record["ignored_parameters"] = ignored
        outputs.append(record)

    manifest["outputs"] = outputs
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1))
    meta = {"kind": "manifest", "prompt": prompt or folder.name,
            "size": [primary.width, primary.height], "profile": profile,
            "manifest": SCHEMA}
    (folder / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    return manifest


def _sheet_record(order: list[str], cw: int, ch: int, columns: int | None) -> dict:
    n = len(order)
    cols = int(columns) if columns else max(1, int(math.ceil(math.sqrt(n))))
    cols = max(1, min(cols, n))
    rows = int(math.ceil(n / cols))
    cells = {fid: [(i % cols) * cw, (i // cols) * ch] for i, fid in enumerate(order)}
    return {"layout": "grid", "columns": cols, "rows": rows, "cell": [cw, ch],
            "size": [cols * cw, rows * ch], "frame_order": list(order), "frames": cells}


def attach_manifest(meta: dict, manifest: dict) -> dict:
    """Merge a manifest into a library ``meta.json`` dict (keeps the existing shape)."""
    out = dict(meta)
    out["manifest"] = {"schema": manifest.get("schema"), "file": "manifest.json"}
    return out


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #
def _cell_array(path: Path, cell: tuple[int, int], size: tuple[int, int]) -> np.ndarray:
    with Image.open(path) as im:
        img = im.convert("RGBA")
    x, y = cell
    w, h = size
    return np.asarray(img.crop((x, y, x + w, y + h))).astype(np.uint8)


def verify_export(folder, manifest: dict | None = None) -> dict:
    """Re-read the export from disk and prove size, order, anchor and alignment survived."""
    folder = Path(folder).expanduser()
    if manifest is None:
        manifest = json.loads((folder / "manifest.json").read_text())
    errors: list[str] = []
    checks: dict[str, dict] = {}

    def check(name: str, ok: bool, detail: str) -> None:
        checks[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            errors.append(f"{name}: {detail}")

    canvas = manifest.get("canvas") or {}
    cw, ch = int(canvas.get("width", 0)), int(canvas.get("height", 0))
    record = json.loads((folder / "sheet.json").read_text())
    order = list(record["frame_order"])

    # 1. every frame on disk is the canvas size
    frame_sizes = {}
    for fid in order:
        p = folder / "frames" / f"{fid}.png"
        if not p.is_file():
            errors.append(f"missing frame {p}")
            continue
        with Image.open(p) as im:
            frame_sizes[fid] = [im.width, im.height]
    check("canvas_size_consistent",
          all(s == [cw, ch] for s in frame_sizes.values()) and len(frame_sizes) == len(order),
          f"canvas {cw}x{ch}; frames {frame_sizes}")

    # 2. the sheet cuts back into exactly the exported frames, in order
    mismatched = []
    for fid in order:
        cell = tuple(record["frames"][fid])
        got = _cell_array(folder / "sheet.png", cell, (cw, ch))
        with Image.open(folder / "frames" / f"{fid}.png") as im:
            want = np.asarray(im.convert("RGBA")).astype(np.uint8)
        if not np.array_equal(got, want):
            mismatched.append(fid)
    check("frame_order_round_trip", not mismatched,
          f"{len(order)} frame(s) recovered from sheet.json cells in order; "
          f"mismatched: {mismatched or 'none'}")

    # 3. the anchor survives export: the anchor pixel of each frame lands on the
    #    same sprite pixel inside its sheet cell
    anchor = canvas.get("anchor") or {}
    ax, ay = int(anchor.get("x", -1)), int(anchor.get("y", -1))
    inside = 0 <= ax < cw and 0 <= ay < ch
    anchor_bad = []
    for fid in order:
        x, y = record["frames"][fid]
        sheet_px = _cell_array(folder / "sheet.png", (x + ax, y + ay), (1, 1))[0, 0]
        frame_px = _cell_array(folder / "frames" / f"{fid}.png", (ax, ay), (1, 1))[0, 0]
        if not np.array_equal(sheet_px, frame_px):
            anchor_bad.append(fid)
    check("anchor_survives", inside and not anchor_bad,
          f"anchor {(ax, ay)} in canvas space maps into every cell; mismatched: {anchor_bad or 'none'}")

    # 4. a tight export can be put back exactly where the anchor expects it
    bbox = canvas.get("content_bbox")
    if canvas.get("alignment") == "tight":
        x0, y0, x1, y1 = bbox
        with Image.open(folder / "sprite.png") as im:
            tight = im.convert("RGBA")
        placed = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        placed.alpha_composite(tight, (x0, y0))
        with Image.open(folder / "frames" / f"{order[0]}.png") as im:
            want = np.asarray(im.convert("RGBA")).astype(np.uint8)
        check("tight_alignment_round_trip",
              np.array_equal(np.asarray(placed).astype(np.uint8), want),
              f"tight sprite re-pasted at content_bbox {bbox} reproduces the canvas frame")
        check("content_bbox_covers_content", _bbox_contains(bbox, cw, ch),
              f"content_bbox {bbox} inside the {cw}x{ch} canvas")
    else:
        check("tight_alignment_round_trip", True, "alignment is 'canvas'; no offset to re-apply")

    # 5. the sprite document matches the canvas
    doc = json.loads((folder / "sprite.json").read_text())
    if canvas.get("alignment") == "tight":
        tw, th = x1 - x0 + 1, y1 - y0 + 1
        check("canvas_size_consistent_sprite_document",
              [doc.get("width"), doc.get("height")] == [tw, th],
              f"sprite.json is the tight {tw}x{th} crop; the canvas size lives in manifest.canvas")
    else:
        check("canvas_size_consistent_sprite_document",
              [doc.get("width"), doc.get("height")] == [cw, ch],
              f"sprite.json {doc.get('width')}x{doc.get('height')} == canvas {cw}x{ch}")

    return {"ok": not errors, "checks": checks, "errors": errors, "frame_order": order,
            "canvas": [cw, ch], "schema": manifest.get("schema")}


def _bbox_contains(bbox, cw: int, ch: int) -> bool:
    if not bbox or len(bbox) != 4:
        return False
    x0, y0, x1, y1 = bbox
    return 0 <= x0 <= x1 < cw and 0 <= y0 <= y1 < ch


def read_sheet(folder, manifest: dict | None = None) -> dict[str, Image.Image]:
    """The exported frames, recovered from the sheet using its recorded cells."""
    folder = Path(folder).expanduser()
    if manifest is None:
        manifest = json.loads((folder / "manifest.json").read_text())
    record = json.loads((folder / "sheet.json").read_text())
    return {fid: Image.fromarray(_cell_array(folder / "sheet.png", tuple(cell),
                                             tuple(record["cell"])), "RGBA")
            for fid, cell in record["frames"].items()}


# --------------------------------------------------------------------------- #
# CLI entry (thin; called from forge/cli.py)
# --------------------------------------------------------------------------- #
def run(image: str, out: str, *, reference: str | None = None, direction: str = DEFAULT_DIRECTION,
        anchor: str = DEFAULT_ANCHOR, alignment: str = "canvas", frames=None, fps: float = 10.0,
        loop: str = "forward", columns: int | None = None, backend: str = "pixellab-pixflux",
        operation: str | None = None, parameters: dict | None = None,
        normal_map: bool = False) -> dict:
    """Build the manifest for one image (plus optional frames), export it and verify it."""
    from .cleanup import load_image

    primary = Sprite.from_image(load_image(Path(image).read_bytes()))
    frame_sprites = None
    if frames:
        frame_sprites = {f"f{i}": Sprite.from_image(load_image(Path(p).read_bytes()))
                         for i, p in enumerate(frames)}
        frame_sprites["f0"] = primary
    animation = {"name": "clip", "fps": fps, "loop": loop} if frame_sprites else None
    nm_request = None
    if normal_map:
        # Today's encoder: a solid sprite is otherwise flat, so the rim dome gives
        # it volume and detail embosses interior features; transparent pixels stay
        # A=0, as the old request's edge_treatment="clamp" intent wanted.
        nm_request = {"rim": 1.0, "detail": 0.5, "transparent_background": True}
    generation = generation_record(backend, operation, parameters)
    ref = reference or image
    manifest = export_manifest(out, primary, frames=frame_sprites, reference=reference_record(ref),
                               direction=direction, anchor=anchor, alignment=alignment,
                               animation=animation, generation=generation, normal_map=nm_request,
                               prompt=Path(image).stem, sheet_columns=columns)
    report = verify_export(out, manifest)
    return {"folder": str(Path(out).expanduser()), "schema": manifest["schema"],
            "outputs": [o["kind"] for o in manifest["outputs"]],
            "generation": {"backend": manifest["generation"]["backend"],
                           "operation": manifest["generation"]["operation"],
                           "supported": manifest["generation"]["supported"],
                           "unsupported": [u["name"] for u in manifest["generation"]["unsupported"]]},
            "verified": report}
