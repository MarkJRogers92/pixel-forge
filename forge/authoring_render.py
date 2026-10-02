"""Fixed deterministic rasterizer. Only validated data reaches the dispatcher."""
from __future__ import annotations

import io
import time

from PIL import Image, __version__ as PILLOW_VERSION

from .authoring_schema import AuthoringError, DEADLINE_SECONDS, MAX_COLORS, validate_geometry
from .dsl import _line_points, _poly_mask
from .qa import check
from .sprite import KEY_CHARS, TRANSPARENT, Sprite, to_hex


def flat_pixels(image):
    return image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()


def source_palette(image: Image.Image) -> list[list[int]]:
    colors = sorted(set(flat_pixels(image)))
    if len(colors) > MAX_COLORS:
        raise AuthoringError("LIMIT_EXCEEDED", "source.palette_entries")
    return [list(color) for color in colors]


def decode_png(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise AuthoringError("INVALID_REQUEST", "source.png")
            if max(image.size) > 512 or image.width * image.height > 262144:
                raise AuthoringError("LIMIT_EXCEEDED", "source.dimensions")
            image.load()
            return image.convert("RGBA")
    except AuthoringError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError):
        raise AuthoringError("INVALID_REQUEST", "source.png") from None


def source_over(source, destination):
    """Straight RGBA; exact integer source-over, round half upward once per byte."""
    sa, da = source[3], destination[3]
    if sa == 0:
        return destination
    denominator = sa * 255 + da * (255 - sa)
    if denominator == 0:
        return (0, 0, 0, 0)
    rgb = tuple((source[i] * sa * 255 + destination[i] * da * (255 - sa) + denominator // 2)
                // denominator for i in range(3))
    return (*rgb, (denominator + 127) // 255)


def render_pixels(request, base: Image.Image | None):
    image = base.copy() if base is not None else Image.new(
        "RGBA", (request.canvas.width, request.canvas.height), (0, 0, 0, 0))
    palette = source_palette(base) if base is not None else request.palette
    validate_geometry(request, image.width, image.height, palette)
    pixels = image.load()
    deadline = time.monotonic() + DEADLINE_SECONDS

    def put(x, y, color):
        pixels[x, y] = source_over(color, pixels[x, y])

    for op in request.operations:
        if time.monotonic() >= deadline:
            raise AuthoringError("RENDER_TIMEOUT", "operations")
        if op.op == "replace_color":
            src, dst = tuple(palette[op.from_color]), tuple(palette[op.to_color])
            for y in range(image.height):
                for x in range(image.width):
                    if pixels[x, y] == src:
                        pixels[x, y] = dst  # Exact replacement, not another alpha layer.
        elif op.op == "erase_rectangle":
            for y in range(op.y, op.y + op.height):
                for x in range(op.x, op.x + op.width):
                    pixels[x, y] = (0, 0, 0, 0)
        else:
            color = tuple(palette[op.color])
            if op.op == "pixel":
                points = [(op.x, op.y)]
            elif op.op == "line":
                points = _line_points(op.x1, op.y1, op.x2, op.y2)
            elif op.op == "polygon":
                points = sorted(_poly_mask(op.points))
            elif op.op == "rectangle":
                points = ((x, y) for y in range(op.y, op.y + op.height)
                          for x in range(op.x, op.x + op.width))
            elif op.op == "ellipse":
                # Integer pixel-centre inclusion within the rectangle's ellipse.
                w, h = op.width, op.height
                points = ((op.x + x, op.y + y) for y in range(h) for x in range(w)
                          if (2*x + 1-w)**2 * h*h + (2*y + 1-h)**2 * w*w <= w*w*h*h)
            else:
                raise AuthoringError("UNSUPPORTED_OPERATION", "operations")
            for x, y in points:
                put(x, y, color)
    source_palette(image)  # Compositing can produce new colours; never quantize.
    return image


def exact_sprite(image: Image.Image) -> Sprite:
    """Preserve all RGBA bytes; legacy from_image thresholds alpha and has 62 keys."""
    colors = [tuple(c) for c in source_palette(image) if c != [0, 0, 0, 0]]
    keys = dict(zip(colors, KEY_CHARS + "!?"))
    grid = [[TRANSPARENT if image.getpixel((x, y)) == (0, 0, 0, 0)
             else keys[image.getpixel((x, y))] for x in range(image.width)] for y in range(image.height)]
    return Sprite(image.width, image.height, {keys[c]: to_hex(c) for c in colors}, grid)


def encode(image, request, profile):
    sprite = exact_sprite(image)
    qa_sprite = exact_sprite(Image.frombytes("RGBA", image.size, bytes(
        byte for color in flat_pixels(image) for byte in (color if color[3] else (0, 0, 0, 0)))))
    report = check(qa_sprite, max_colors=MAX_COLORS)
    semi = sum(1 for color in flat_pixels(image) if 0 < color[3] < 255)
    if semi:
        report["warnings"].append(f"{semi} pixels have partial alpha; check their native-size readability")
    used = source_palette(image)
    if profile and profile.get("palette_rgba"):
        allowed = {tuple(c) for c in profile["palette_rgba"]}
        outside = sum(1 for c in used if c[3] and tuple(c) not in allowed)
        if outside:
            report["warnings"].append(f"{outside} output colors differ from the profile palette (preference)")
    report["checks_run"] = ["dimensions", "rgba_bytes", "palette_limit", "png_roundtrip",
                             "strays", "outline", "banding", "near_duplicate_colors", "partial_alpha"]
    if profile:
        report["checks_run"].append("profile_palette_preference")
    report["automatic_cleanup"] = False
    png = sprite.to_png_bytes()
    with Image.open(io.BytesIO(png)) as decoded:
        if decoded.size != image.size or decoded.convert("RGBA").tobytes() != image.tobytes():
            raise AuthoringError("STORAGE_FAILURE", "png_roundtrip")
    return {"png": png, "preview": sprite.to_png_bytes(scale=8), "sprite": sprite.to_dict(),
            "qa": report, "palette_count": len(used), "pillow_version": PILLOW_VERSION}
