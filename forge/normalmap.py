"""Optional tangent-space normal-map pass and a contained lighting preview.

This is an ORIGINAL Pixel Forge pass, not a reimplementation of
SpriteIlluminator. Its behaviour is *inspired by the documented product
behaviour* of SpriteIlluminator (codeandweb.com/spriteilluminator): surface
information is derived from the sprite's TRANSPARENCY (the silhouette is
inflated into a rounded surface) and embossed detail is derived from COLOUR
differences. Those are documented product behaviours, not a decoded algorithm;
nothing here was extracted from that binary.

Two independent controls, so they can be varied separately:

* ``strength``         - how much the silhouette-inflation surface tilts the
                         normals (height in pixels -> 45 degrees at 1.0);
* ``detail_strength``  - how much high-pass colour/luminance detail embosses;
* ``edge_treatment``   - how the height field is extended past the canvas when
                         the gradient is measured (``clamp`` | ``wrap`` | ``zero``).

Coordinate convention (STATED, not implied): the map is TANGENT SPACE, with
``+X`` = sprite right and ``+Z`` = out of the screen.  The green channel's
sign is chosen by ``convention``:

* ``"opengl"``  (default): ``+Y`` = up on screen  -> ``G = ( ny * 0.5 + 0.5)``
* ``"directx"``          : ``+Y`` = down on screen -> ``G = (-ny * 0.5 + 0.5)``

The red and blue channels are identical in both conventions.  A flat surface
encodes exactly ``(128, 128, 255)``.  A renderer consumes the file by decoding
``n = rgb / 127.5 - 1`` with the SAME convention (flipping green for DirectX)
and lighting it with a light direction expressed in the same tangent frame.
Where the sprite is transparent the normal map is flat RGB and ``A = 0``, so it
composites exactly like the colour sprite.

Whether the DEAD MALL game can consume a normal map is a separate question:
no normal-map lighting exists in that renderer today, so this pass is a
contained preview with a documented contract, not a claim of integration.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

CONVENTIONS = ("opengl", "directx")
EDGE_TREATMENTS = ("clamp", "wrap", "zero")

# Flat tangent-space normal: +Z out of the screen.
FLAT = (128, 128, 255)

# High-pass luminance is multiplied by this before it is added to the height
# field, so a one-step colour difference is the same order of magnitude as a
# one-pixel change in the inflated surface. Documented constant, not tuned
# per-asset.
DETAIL_GAIN = 4.0
ALPHA_THRESHOLD = 128


class NormalMapError(ValueError):
    pass


def _rgba(image) -> np.ndarray:
    if isinstance(image, Image.Image):
        arr = np.asarray(image.convert("RGBA"))
    else:
        arr = np.asarray(image)
    if arr.ndim != 3 or arr.shape[2] != 4:
        raise NormalMapError("normal_map expects an RGBA image (H, W, 4)")
    return arr.astype(np.uint8)


def _mask(arr: np.ndarray) -> np.ndarray:
    return arr[..., 3] >= ALPHA_THRESHOLD


def _distance_field(mask: np.ndarray) -> np.ndarray:
    """City-block distance from each opaque pixel to the nearest transparent one.

    A pixel on the silhouette rim is 1, the next one in is 2, and so on: the
    height of the inflated surface. Transparent pixels are 0.
    """
    h, w = mask.shape
    dist = np.zeros((h, w), np.int32)
    inner = mask.copy()
    depth = 0
    while inner.any():
        depth += 1
        dist[inner] = depth
        pad = np.zeros((h + 2, w + 2), bool)
        pad[1:-1, 1:-1] = inner
        inner = (inner & pad[:-2, 1:-1] & pad[2:, 1:-1]
                 & pad[1:-1, :-2] & pad[1:-1, 2:])
    return dist


def _luminance(arr: np.ndarray) -> np.ndarray:
    rgb = arr[..., :3].astype(np.float64) / 255.0
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _fill_outside(lum: np.ndarray, mask: np.ndarray, rounds: int) -> np.ndarray:
    """Extend the masked luminance outward by neighbour averaging.

    This keeps the emboss term driven by COLOUR differences only: without it,
    the hard luminance drop across the silhouette (interior vs transparent)
    would emboss the outline and make ``detail_strength`` move the very normals
    ``strength`` is meant to own.
    """
    out = np.where(mask, lum, 0.0)
    known = mask.copy()
    for _ in range(max(0, rounds)):
        if known.all():
            break
        acc = np.zeros_like(out)
        cnt = np.zeros_like(out)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            acc += np.where(np.roll(known, (dy, dx), (0, 1)), np.roll(out, (dy, dx), (0, 1)), 0.0)
            cnt += np.roll(known, (dy, dx), (0, 1))
        fill = (~known) & (cnt > 0)
        out[fill] = acc[fill] / cnt[fill]
        known |= fill
    return out


def _high_pass(lum: np.ndarray, radius: int) -> np.ndarray:
    """Local colour differences: luminance minus its blurred version."""
    if radius <= 0:
        return lum
    blurred = np.asarray(
        Image.fromarray(np.uint8(np.rint(np.clip(lum, 0.0, 1.0) * 255.0)))
        .filter(ImageFilter.GaussianBlur(radius))
    ).astype(np.float64) / 255.0
    return lum - blurred


def _gradient(height: np.ndarray, edge_treatment: str) -> tuple[np.ndarray, np.ndarray]:
    """Central differences of the height field, extended per ``edge_treatment``.

    ``clamp`` replicates the border pixel (default), ``wrap`` is periodic and
    ``zero`` treats everything off-canvas as zero height.
    """
    if edge_treatment not in EDGE_TREATMENTS:
        raise NormalMapError(f"edge_treatment must be one of {EDGE_TREATMENTS}")
    mode = {"clamp": "edge", "wrap": "wrap", "zero": "constant"}[edge_treatment]
    p = np.pad(height, 1, mode=mode)
    # rows increase downward on screen, so gy is d(height)/d(down).
    gy = (p[2:, 1:-1] - p[:-2, 1:-1]) / 2.0
    gx = (p[1:-1, 2:] - p[1:-1, :-2]) / 2.0
    return gx, gy


def height_field(image, *, strength: float = 1.0, detail_strength: float = 0.5,
                 detail_radius: int = 2, edge_treatment: str = "clamp") -> np.ndarray:
    """The raw height field before it becomes a normal map (useful for tests).

    ``strength`` scales the silhouette inflation (transparency-derived surface)
    and ``detail_strength`` scales the colour/luminance emboss; the two terms
    are independent - the emboss is computed from a luminance field extended
    past the silhouette, so it never moves the outline normals.
    """
    arr = _rgba(image)
    mask = _mask(arr)
    surface = _distance_field(mask).astype(np.float64) * float(strength)
    lum = np.where(mask, _luminance(arr), 0.0)
    filled = _fill_outside(lum, mask, rounds=max(2, 2 * int(detail_radius) + 2))
    detail = np.where(mask, _high_pass(filled, detail_radius), 0.0)
    # Drop uint8-quantisation noise so a flat-coloured sprite has no emboss at all.
    detail[np.abs(detail) < 1.0 / 255.0] = 0.0
    return surface + float(detail_strength) * DETAIL_GAIN * detail


def normal_map_array(image, *, strength: float = 1.0, detail_strength: float = 0.5,
                     detail_radius: int = 2, edge_treatment: str = "clamp",
                     convention: str = "opengl") -> np.ndarray:
    """RGBA uint8 tangent-space normal map for an RGBA image."""
    if convention not in CONVENTIONS:
        raise NormalMapError(f"convention must be one of {CONVENTIONS}")
    arr = _rgba(image)
    mask = _mask(arr)
    height = height_field(arr, strength=strength, detail_strength=detail_strength,
                          detail_radius=detail_radius, edge_treatment=edge_treatment)
    gx, gy = _gradient(height, edge_treatment)
    # +Y up on screen: image rows go down, so ny_up = +gy.
    nx, ny_up = -gx, gy
    nz = np.ones_like(nx)
    length = np.sqrt(nx * nx + ny_up * ny_up + nz * nz)
    nx, ny_up, nz = nx / length, ny_up / length, nz / length
    green = ny_up if convention == "opengl" else -ny_up
    out = np.zeros((*arr.shape[:2], 4), np.uint8)
    out[..., 0] = np.rint((nx * 0.5 + 0.5) * 255.0)
    out[..., 1] = np.rint((green * 0.5 + 0.5) * 255.0)
    out[..., 2] = np.rint((nz * 0.5 + 0.5) * 255.0)
    out[..., 3] = np.where(mask, 255, 0)
    flat = ~mask
    out[flat, 0], out[flat, 1], out[flat, 2] = FLAT
    return out


def normal_map_image(image, **kwargs) -> Image.Image:
    return Image.fromarray(normal_map_array(image, **kwargs), "RGBA")


def normal_map_sprite(sprite, **kwargs) -> Image.Image:
    """Convenience for the library's Sprite model (grid -> RGBA -> normal map)."""
    return normal_map_image(sprite.to_image(), **kwargs)


def decode_normals(normal, *, convention: str = "opengl") -> np.ndarray:
    """Decode a normal map to unit vectors in the tangent frame (x right, y up, z out)."""
    arr = _rgba(normal).astype(np.float64) / 127.5 - 1.0
    if convention == "directx":
        arr[..., 1] *= -1.0
    length = np.linalg.norm(arr[..., :3], axis=-1, keepdims=True)
    length[length == 0] = 1.0
    return arr[..., :3] / length


# Light positions in the tangent frame (x right, y up, z out of the screen).
LIGHTS: dict[str, tuple[float, float, float]] = {
    "top": (0.0, 1.0, 0.35),
    "top-left": (-0.6, 0.75, 0.4),
    "top-right": (0.6, 0.75, 0.4),
    "left": (-1.0, 0.15, 0.3),
    "right": (1.0, 0.15, 0.3),
    "bottom": (0.0, -1.0, 0.35),
    "bottom-left": (-0.6, -0.75, 0.4),
    "bottom-right": (0.6, -0.75, 0.4),
    "front": (0.0, 0.0, 1.0),
}


def known_lights() -> dict[str, tuple[float, float, float]]:
    return dict(LIGHTS)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v)
    if n == 0:
        raise NormalMapError("light direction must not be zero")
    return v / n


def lighting_preview(colour, normal, light=(-0.6, 0.75, 0.4), *, ambient: float = 0.25,
                     convention: str = "opengl") -> Image.Image:
    """Lambert preview: composite ``colour`` lit by a directional light.

    ``light`` is a direction in the tangent frame (x right, y up, z out); it may
    be a name from :data:`LIGHTS`. Only ``N.L`` is applied - no specular - so the
    preview shows the normal map's response rather than a material model.
    """
    if isinstance(light, str):
        try:
            light = LIGHTS[light]
        except KeyError:
            raise NormalMapError(f"unknown light {light!r}; use one of {sorted(LIGHTS)}") from None
    base = _rgba(colour)
    n = decode_normals(normal, convention=convention)
    lam = np.clip(n @ _unit(light), 0.0, 1.0)
    shade = float(ambient) + (1.0 - float(ambient)) * lam
    out = base.astype(np.float64)
    out[..., :3] = np.clip(out[..., :3] * shade[..., None], 0.0, 255.0)
    return Image.fromarray(np.rint(out).astype(np.uint8), "RGBA")


def review_sheet(colour, normal, lights=("top-left", "top-right", "left", "right"),
                 *, scale: int = 2, convention: str = "opengl") -> Image.Image:
    """One image: the unchanged colour sprite, its normal map, then one lit copy per light."""
    from PIL import ImageDraw

    base = _rgba(colour)
    h, w = base.shape[:2]
    panels = [("colour", Image.fromarray(base, "RGBA")), ("normal", _rgba_image(normal))]
    for name in lights:
        panels.append((name, lighting_preview(base, normal, name, convention=convention)))

    pad, label_h = 10, 20
    cell_w = max(w * scale, 90)
    cell_h = h * scale
    sheet = Image.new("RGBA", (pad + len(panels) * (cell_w + pad),
                              pad + label_h + cell_h + pad), (26, 24, 32, 255))
    draw = ImageDraw.Draw(sheet)
    for index, (name, img) in enumerate(panels):
        x = pad + index * (cell_w + pad)
        draw.text((x, pad), name, fill=(230, 230, 235, 255))
        if scale > 1:
            img = img.resize((w * scale, h * scale), Image.Resampling.NEAREST)
        sheet.alpha_composite(img, (x, pad + label_h))
    return sheet


def _rgba_image(image) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGBA")
    return Image.fromarray(_rgba(image), "RGBA")
