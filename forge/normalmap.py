"""Normal maps from a finished sprite.

A port of what SpriteIlluminator 2.2.0 does, with the constants and kernel rules
read out of its shipped binary (CodeAndWeb REA run: `reports/03_spriteilluminator.md`):
height is the sprite's **alpha** (not luminance), the gradient is a 3x3 Sobel
scaled by the fixed **1/8** height->slope factor, blur follows OpenCV's rule
``ksize = odd(max(3, round(2*radius)))``, ``sigma = radius/2``, replicated
borders, and a flat surface encodes as **RGB(128,128,255)**.

A normal map is full-colour data, so it comes back as a PIL image: Pixel Forge
sprites are palette-indexed and cannot hold it.

Two deliberate deviations from SpriteIlluminator, both so the output works in a
game engine without a flip:

* We emit the **standard convention** (G < 128 means the surface faces *up* in
  screen space). SI stores the negated value and negates it again when it lights
  the sprite; our own `forge.lighting.lit_preview` decodes ours directly. Pass
  `invert_y=True` for a consumer that expects SI's encoding.
* The gradient is the standard 3x3 Sobel. SI's `sobelLine` differs by a
  one-column offset; the 1/8 scale and edge replication are the same.

Height from a *flat* shape is constant, so a plain opaque sprite produces an
almost entirely flat normal map. `rim` is what gives such art volume: it builds
SI's "Create Rim/Bevel" dome from the distance to the alpha edge
(``strength * sqrt(1 - (1 - d/contour)^2)``).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

F32 = np.float32
FLAT_RGB = (128, 128, 255)
HEIGHT_SOURCES = ("alpha", "luma")


def to_rgba(source) -> np.ndarray:
    """(h, w, 4) uint8 from a Sprite, a PIL image or an array."""
    if hasattr(source, "to_rgba"):                      # forge.sprite.Sprite
        return source.to_rgba()
    if isinstance(source, np.ndarray):
        arr = source
        if arr.ndim == 2:
            arr = np.dstack([np.repeat(arr[..., None], 3, axis=2), np.full(arr.shape, 255, np.uint8)])
        if arr.shape[2] == 3:
            arr = np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)])
        return arr.astype(np.uint8)
    if isinstance(source, Image.Image):
        return np.asarray(source.convert("RGBA"), dtype=np.uint8)
    return np.asarray(Image.open(source).convert("RGBA"), dtype=np.uint8)


def kernel_size_for_radius(radius: float) -> int:
    """OpenCV/SI rule: odd(max(3, round(2*radius)))."""
    return max(3, int(radius * 2 + 0.5)) | 1


def _taps(radius: float) -> np.ndarray:
    """Separable Gaussian taps, sigma = radius/2 (SI's rule)."""
    k = kernel_size_for_radius(radius)
    sigma = max(radius / 2.0, 1e-6)
    x = np.arange(k, dtype=np.float64) - (k - 1) / 2.0
    g = np.exp(-(x * x) / (2.0 * sigma * sigma))
    return (g / g.sum()).astype(F32)


def _convolve_axis(field: np.ndarray, g: np.ndarray, axis: int) -> np.ndarray:
    """One-pass convolution along `axis` (input already padded by len(g)//2)."""
    k = len(g)
    out_len = field.shape[axis] - k + 1
    shape = list(field.shape)
    shape[axis] = out_len
    acc = np.zeros(shape, F32)
    for i, weight in enumerate(g):
        sl = [slice(None)] * field.ndim
        sl[axis] = slice(i, i + out_len)
        acc += F32(weight) * field[tuple(sl)]
    return acc


def gaussian_blur(field: np.ndarray, radius: float) -> np.ndarray:
    """Separable Gaussian with replicated borders (cv2.BORDER_REPLICATE = 1)."""
    if radius <= 0:
        return field.astype(F32, copy=True)
    g = _taps(radius)
    pad = len(g) // 2
    # One pad in both axes is enough: after the horizontal pass the vertical
    # padding is still in place for the vertical pass (BORDER_REPLICATE).
    p = np.pad(field.astype(F32), pad, mode="edge")
    p = _convolve_axis(p, g, 1)
    return _convolve_axis(p, g, 0)


def distance_inside(mask: np.ndarray) -> np.ndarray:
    """Approximate Euclidean distance (chamfer 1/sqrt2) to the nearest False pixel."""
    inf = F32(1e6)
    d = np.where(mask, inf, F32(0.0))
    h, w = d.shape
    for y in range(h):
        for x in range(w):
            if not mask[y, x]:
                continue
            best = d[y, x]
            if y:
                best = min(best, d[y - 1, x] + 1.0)
                if x:
                    best = min(best, d[y - 1, x - 1] + 1.41421356)
                if x < w - 1:
                    best = min(best, d[y - 1, x + 1] + 1.41421356)
            if x:
                best = min(best, d[y, x - 1] + 1.0)
            d[y, x] = best
    for y in range(h - 1, -1, -1):
        for x in range(w - 1, -1, -1):
            if not mask[y, x]:
                continue
            best = d[y, x]
            if y < h - 1:
                best = min(best, d[y + 1, x] + 1.0)
                if x < w - 1:
                    best = min(best, d[y + 1, x + 1] + 1.41421356)
                if x:
                    best = min(best, d[y + 1, x - 1] + 1.41421356)
            if x < w - 1:
                best = min(best, d[y, x + 1] + 1.0)
            d[y, x] = best
    return d


def height_field(rgba: np.ndarray, source: str = "alpha") -> np.ndarray:
    """Height in 0..1: SI uses alpha; "luma" is its Emboss path (Rec.709, masked by alpha)."""
    if source not in HEIGHT_SOURCES:
        raise ValueError(f"height_source must be one of {HEIGHT_SOURCES}")
    alpha = rgba[..., 3].astype(F32) / 255.0
    if source == "alpha":
        return alpha
    rgb = rgba[..., :3].astype(F32)
    luma = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) / 255.0
    return luma * alpha


def rim_height(rgba: np.ndarray, strength: float, contour: float = 0.5) -> np.ndarray:
    """SI's Create Rim/Bevel dome: a circular falloff over the inside distance field."""
    mask = rgba[..., 3] > 0
    if not mask.any():
        return np.zeros(rgba.shape[:2], F32)
    d = distance_inside(mask)
    span = max(float(d.max()) * contour, 1e-6)
    t = np.clip(d / span, 0.0, 1.0)
    return (F32(strength) * np.sqrt(np.maximum(0.0, 1.0 - (1.0 - t) ** 2))).astype(F32)


def sobel(field: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """3x3 Sobel with replicated borders, scaled by SI's 1/8 and clamped to [-128, 127].

    Returns (``d/dx``, ``d/dy``) in *screen* space, where y grows downward, so the
    second value is negative of the y-up derivative.
    """
    p = np.pad(field.astype(F32), 1, mode="edge")
    gx = (p[0:-2, 2:] + 2.0 * p[1:-1, 2:] + p[2:, 2:]) - (p[0:-2, 0:-2] + 2.0 * p[1:-1, 0:-2] + p[2:, 0:-2])
    gy = (p[0:-2, 0:-2] + 2.0 * p[0:-2, 1:-1] + p[0:-2, 2:]) - (p[2:, 0:-2] + 2.0 * p[2:, 1:-1] + p[2:, 2:])
    clamp = lambda g: np.clip(g / 8.0, -128.0, 127.0)
    return clamp(gx), clamp(gy)


def normal_map(source, *, height_source: str = "alpha", blur: float = 0.0,
               invert_y: bool = False, transparent_background: bool = False,
               rim: float = 0.0, contour: float = 0.5) -> Image.Image:
    """RGBA normal map for `source` (a Sprite, image path, PIL image or RGBA array).

    `rim` > 0 replaces the (usually flat) alpha height with SI's bevel dome of
    that strength, which is what gives a solid sprite volume. `blur` smooths the
    height before the gradient; SI's base pass uses 0.

    Every channel uses the standard engine encoding (``2n-1``, so flat is
    RGB(128,128,255) and blue never drops below 128). SI additionally offers a
    "7 bit, best compatibility" blue variant; we deliberately emit only the
    standard one, because this is what Godot's ``hint_normal`` and other
    consumers decode. (Which one SI's own default writes could not be verified:
    its free build refuses to export normal maps at all.)
    """
    if rim < 0:
        raise ValueError("rim must be >= 0")
    if not 0 < contour <= 1:
        raise ValueError("contour must be in (0, 1]")
    rgba = to_rgba(source)
    h = rim_height(rgba, rim, contour) if rim > 0 else height_field(rgba, height_source)
    if blur > 0:
        h = gaussian_blur(h, blur)

    gx, gy = sobel(h)
    # A height field's surface normal is (-dh/dx, -dh/dy_up, 1) with y up. Screen
    # space grows downward, so the y-up derivative is -gy and the normal is
    # (-gx, -gy, 1) -> G > 128 means the surface faces up, which is what a
    # standard consumer expects.
    nx = -gx
    ny = -gy if not invert_y else gy
    length = np.sqrt(nx * nx + ny * ny + 1.0)
    nx, ny, nz = nx / length, ny / length, 1.0 / length

    out = np.zeros((rgba.shape[0], rgba.shape[1], 4), np.uint8)
    out[..., 0] = np.rint((nx + 1.0) * 127.5).clip(0, 255)
    out[..., 1] = np.rint((ny + 1.0) * 127.5).clip(0, 255)
    out[..., 2] = np.rint((nz + 1.0) * 127.5).clip(0, 255)
    out[..., 3] = 255

    alpha = rgba[..., 3]
    if transparent_background:
        clear = alpha == 0
        out[clear] = (*FLAT_RGB, 0)
        partial = (alpha > 0) & (alpha < 255)
        out[..., 3][partial] = alpha[partial]
    return Image.fromarray(out, "RGBA")


def save_normal_map(source, path, **kwargs) -> str:
    """Write the normal map next to the sprite and return the path."""
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    normal_map(source, **kwargs).save(p, "PNG")
    return str(p)
