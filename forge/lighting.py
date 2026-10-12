"""Lit-sprite preview: what a sprite looks like under one 2D light.

The equation is the one SpriteIlluminator's own CPU renderer uses, read out of
`PointLighting::render` (CodeAndWeb REA run: `reports/03_spriteilluminator.md`):

    out = sprite * (ambient.rgb * ambient.a + light.rgb * light.a * intensity * d)
    d   = max(0, dot(normal, normalize(light - pixel)))

There is no specular, gloss, roughness or emissive term — SI has none, so this
does not either. SI uploads its brightness as ``value / 100``; we take the same
0..100 shape so its parameters transfer directly.

Normals are decoded the standard way (see `forge.normalmap`): G < 128 faces up,
so a light above a sprite brightens its top. Height is the light's Z: bigger
values pick out flatter, more head-on surfaces.

The module also writes the runtime side: `forge/templates/light2d.gdshader` is a
Godot 4 canvas-item shader using the same uniforms, and `light_params()` produces
the JSON a scene needs to drive them.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from . import normalmap

DEFAULT_LIGHT_COLOR = (255, 255, 255, 255)
DEFAULT_AMBIENT = (128, 128, 128, 128)
DEFAULT_BACKGROUND = (24, 24, 32, 255)
# SI stores light_height/brightness as 0..100-style properties and divides
# brightness by 100 before upload; 100 px is the working height for a small sprite.
DEFAULT_HEIGHT = 100.0
DEFAULT_BRIGHTNESS = 100.0


def _rgba(value, *, alpha_default=255) -> tuple:
    if isinstance(value, str):
        return _rgba_from_hex(value, alpha_default)
    value = tuple(int(v) for v in value)
    if len(value) == 3:
        value = (*value, alpha_default)
    if len(value) != 4:
        raise ValueError(f"colour needs 3 or 4 components, got {value!r}")
    return value


def _rgba_from_hex(text: str, alpha_default: int = 255) -> tuple:
    from .sprite import parse_hex
    r, g, b, a = parse_hex(text)
    if len(text.strip().lstrip("#")) in (3, 6):
        a = alpha_default
    return (r, g, b, a)


def _decode_normals(normal) -> np.ndarray:
    """(h, w, 3) unit normals from the normal map, standard convention."""
    rgba = normalmap.to_rgba(normal).astype(np.float32) / 255.0
    n = rgba[..., :3] * 2.0 - 1.0
    return n


def lit_preview(source, normal, *, light=(0.5, 0.5), height: float = DEFAULT_HEIGHT,
                color=DEFAULT_LIGHT_COLOR, brightness: float = DEFAULT_BRIGHTNESS,
                ambient=DEFAULT_AMBIENT, background=DEFAULT_BACKGROUND,
                flatten: bool = False) -> Image.Image:
    """Render `source` under one light placed at normalized `light` (x right, y down).

    `brightness` is SI's 0..100 property; it is divided by 100 here too. With
    `flatten` the sprite is composited onto `background` first, which is what SI
    does for its exported `_lit.png`; otherwise transparency is kept.
    """
    sprite = normalmap.to_rgba(source).astype(np.float32)
    n = _decode_normals(normal)
    if n.shape[:2] != sprite.shape[:2]:
        raise ValueError(f"normal map {n.shape[:2]} does not match sprite {sprite.shape[:2]}")

    h, w = sprite.shape[:2]
    light_color = np.array(_rgba(color), np.float32)
    ambient_color = np.array(_rgba(ambient), np.float32)

    ys, xs = np.mgrid[0:h, 0:w]
    u = (xs + 0.5) / w
    v = (ys + 0.5) / h
    # +Y up in normal space, so a light above the pixel (smaller y) must have a
    # positive Y component here.
    lx = float(light[0]) - u
    ly = -(float(light[1]) - v)
    lz = np.full_like(lx, float(height) / max(h, w))
    norm = np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / norm, ly / norm, lz / norm

    d = np.clip(n[..., 0] * lx + n[..., 1] * ly + n[..., 2] * lz, 0.0, None)
    gain = ((ambient_color[:3] / 255.0) * (ambient_color[3] / 255.0)
            + (light_color[:3] / 255.0) * (light_color[3] / 255.0)
            * (float(brightness) / 100.0) * d[..., None])
    rgb = np.clip(sprite[..., :3] * gain, 0, 255)
    out = np.dstack([rgb, sprite[..., 3]]).astype(np.uint8)
    img = Image.fromarray(out, "RGBA")
    if flatten:
        bg = Image.new("RGBA", img.size, _rgba(background))
        bg.alpha_composite(img)
        return bg
    return img


def _light_json(source, *, light, height, color, brightness, ambient) -> dict:
    rgba = normalmap.to_rgba(source)
    return {
        "light_pos": [float(light[0]), float(light[1])],
        "light_height": float(height),
        "light_color": _hex(_rgba(color)),
        "brightness": float(brightness),
        "ambient_color": _hex(_rgba(ambient)),
        "sprite_size": [int(rgba.shape[1]), int(rgba.shape[0])],
    }


def _hex(rgba) -> str:
    r, g, b, a = (int(v) for v in rgba)
    return f"#{r:02x}{g:02x}{b:02x}{a:02x}"


def lit_sprite(source, normal, out_png, *, light=(0.5, 0.5), height: float = DEFAULT_HEIGHT,
               color=DEFAULT_LIGHT_COLOR, brightness: float = DEFAULT_BRIGHTNESS,
               ambient=DEFAULT_AMBIENT, background=DEFAULT_BACKGROUND,
               engine: str | None = None) -> dict:
    """Write `<stem>_lit.png` (SI's own naming) plus optional engine bits.

    With `engine="godot"` it also writes `<stem>.light.json` (the uniform values)
    and copies the canvas-item shader to `<stem>.gdshader`.
    """
    png = Path(out_png)
    png.parent.mkdir(parents=True, exist_ok=True)
    lit_preview(source, normal, light=light, height=height, color=color,
                brightness=brightness, ambient=ambient, background=background,
                flatten=True).save(png, "PNG")
    result = {"lit": str(png)}
    if engine:
        if engine != "godot":
            raise ValueError("engine must be 'godot' or None")
        params = _light_json(source, light=light, height=height, color=color,
                             brightness=brightness, ambient=ambient)
        json_path = png.with_suffix(".light.json")
        json_path.write_text(json.dumps(params, indent=1))
        shader_path = png.with_suffix(".gdshader")
        shader_path.write_text(godot_shader())
        result.update({"params": str(json_path), "shader": str(shader_path)})
    return result


def lit_animation(source, normal, out_gif, *, frames: int = 16, fps: float = 12.0,
                  radius: float = 0.35, turns: float = 1.0, phase: float = 0.0,
                  height: float = DEFAULT_HEIGHT, color=DEFAULT_LIGHT_COLOR,
                  brightness: float = DEFAULT_BRIGHTNESS, ambient=DEFAULT_AMBIENT,
                  background=DEFAULT_BACKGROUND) -> dict:
    """A GIF of the light orbiting the sprite: SI's "export animation of lit sprite".

    `frames` images are rendered with the light on a circle of `radius` (in
    normalized sprite units) around the centre, `turns` revolutions per loop,
    starting at `phase` radians.
    """
    if frames < 2:
        raise ValueError("frames must be at least 2")
    if fps <= 0:
        raise ValueError("fps must be positive")
    gif = Path(out_gif)
    gif.parent.mkdir(parents=True, exist_ok=True)
    images = []
    for i in range(frames):
        angle = phase + 2.0 * math.pi * turns * i / frames
        light = (0.5 + radius * math.cos(angle), 0.5 + radius * math.sin(angle))
        images.append(lit_preview(source, normal, light=light, height=height, color=color,
                                  brightness=brightness, ambient=ambient, background=background,
                                  flatten=True).convert("P", palette=Image.ADAPTIVE))
    images[0].save(gif, save_all=True, append_images=images[1:],
                   duration=round(1000.0 / fps), loop=0, optimize=False)
    return {"gif": str(gif), "frames": frames, "fps": fps, "radius": radius, "turns": turns}


def godot_shader() -> str:
    """The Godot 4 canvas-item shader shipped in `forge/templates/`."""
    return (Path(__file__).parent / "templates" / "light2d.gdshader").read_text()
