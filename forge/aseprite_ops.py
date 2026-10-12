"""Aseprite-backed passes: operations Aseprite's C++ engine does better than ours.

Pixel Forge's DSL renders deterministically in Python. A few operations,
however, are best delegated to the installed Aseprite 1.3 while it is present:

  * ordered / error-diffusion / threshold dithering into an exact palette
  * palette quantization (Aseprite's ``ColorQuantization`` + indexed conversion)
  * blend-mode compositing of two sprites (multiply, screen, overlay, HSL, ...)

Each pass writes the input sprite(s) to temporary PNGs, runs a small Lua script
through ``aseprite -b --script`` and reads the result back into a Sprite. The
names and enum values used here were read off the installed Aseprite
(1.3.18.6, Lua API 41) with REA + live Lua probing; see ``ASEPRITE-NOTES.md``.

Aseprite's ``Outline`` command does NOT take effect in batch mode on 1.3.18.6
(it runs but changes nothing), so it is intentionally not exposed here; Forge's
own ``outline`` DSL op covers that case.

Nothing runs unless Aseprite is installed: check :func:`available` or catch
:class:`AsepriteError`.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from . import aseprite
from .aseprite import AsepriteError
from .sprite import Sprite, parse_hex

# Aseprite's ChangePixelFormat accepts these dithering algorithm names (verified
# on 1.3.18.6 via app.command.ChangePixelFormat{ dithering = <name> }).
ALGORITHMS = ("ordered", "error-diffusion", "threshold")

# Pixel Forge name -> Aseprite BlendMode member. Values were read live:
# BlendMode.MULTIPLY == 14, BlendMode.SCREEN == 15, ... (api 41).
BLEND_MODES: dict[str, str] = {
    "normal": "NORMAL", "multiply": "MULTIPLY", "screen": "SCREEN", "overlay": "OVERLAY",
    "darken": "DARKEN", "lighten": "LIGHTEN", "color-dodge": "COLOR_DODGE",
    "color-burn": "COLOR_BURN", "hard-light": "HARD_LIGHT", "soft-light": "SOFT_LIGHT",
    "difference": "DIFFERENCE", "exclusion": "EXCLUSION", "addition": "ADDITION",
    "subtract": "SUBTRACT", "divide": "DIVIDE",
    "hue": "HSL_HUE", "saturation": "HSL_SATURATION", "color": "HSL_COLOR",
    "luminosity": "HSL_LUMINOSITY",
}

_LOAD = """
local img = Image{ fromFile = app.params.input }
local spr = Sprite(img.width, img.height, ColorMode.RGB)
spr:newCel(spr.layers[1], spr.frames[1], img, Point(0, 0))
"""

_DITHER_LUA = _LOAD + """
if app.params.palette ~= "" then
  local cols = json.decode(io.open(app.params.palette):read("a"))
  local p = Palette(); p:resize(#cols)
  for i, c in ipairs(cols) do p:setColor(i - 1, Color(c[1], c[2], c[3], c[4])) end
  spr:setPalette(p)
end
local args = { ui = false, format = "indexed", dithering = app.params.algorithm }
if app.params.matrix ~= "" then args.ditheringMatrix = app.params.matrix end
app.command.ChangePixelFormat(args)
spr:saveAs(app.params.output)
"""

_QUANTIZE_LUA = _LOAD + """
app.command.ColorQuantization{ ui = false, maxColors = tonumber(app.params.colors) }
local args = { ui = false, format = "indexed" }
if app.params.algorithm ~= "" then args.dithering = app.params.algorithm end
app.command.ChangePixelFormat(args)
spr:saveAs(app.params.output)
"""

_BLEND_LUA = _LOAD + """
local top = Image{ fromFile = app.params.top }
local L = spr:newLayer(); L.name = app.params.name
L.blendMode = BlendMode[app.params.mode]
L.opacity = tonumber(app.params.opacity)
spr:newCel(L, spr.frames[1], top, Point(0, 0))
spr:flatten()
spr:saveAs(app.params.output)
"""


def available() -> bool:
    """True when an Aseprite binary can be found (env, PATH or the usual spots)."""
    return aseprite.find_executable() is not None


def run_batch(script: str, params: dict[str, str], timeout: int = 120) -> None:
    exe = aseprite.find_executable()
    if not exe:
        raise AsepriteError("Aseprite not found; install it or set ASEPRITE_PATH")
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "pass.lua"
        script_path.write_text(script)
        args = [exe, "-b"]
        for key, value in params.items():
            args += ["--script-param", f"{key}={value}"]
        args += ["--script", str(script_path)]
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            raise AsepriteError(f"aseprite pass failed ({res.returncode}): "
                                f"{(res.stderr or res.stdout).strip()[:400]}")


def _load_result(path: Path) -> Sprite:
    from PIL import Image
    if not path.exists():
        raise AsepriteError("Aseprite did not write the result")
    try:
        return Sprite.from_image(Image.open(path))
    except ValueError as e:
        raise AsepriteError(f"result has too many colours for an indexed sprite: {e}") from e


def _palette_colors(palette) -> list[list[int]] | None:
    """Normalize a palette of hex strings or [r,g,b,a] tuples to ARGB-ready ints."""
    if not palette:
        return None
    cols: list[list[int]] = []
    for entry in palette:
        if isinstance(entry, str):
            cols.append(list(parse_hex(entry)))
        else:
            r, g, b = (int(x) for x in entry[:3])
            a = int(entry[3]) if len(entry) > 3 else 255
            cols.append([r, g, b, a])
    return cols


def dither(sprite: Sprite, algorithm: str = "ordered", palette=None, matrix: str | Path | None = None,
           timeout: int = 120) -> Sprite:
    """Dither `sprite` into `palette` with Aseprite's engine.

    `algorithm` is one of :data:`ALGORITHMS`. `palette` is a list of hex strings
    ("#rrggbb"/"#rrggbbaa") or [r,g,b,a] tuples; when omitted Aseprite indexes the
    image with its own generated palette. `matrix` is an optional Aseprite
    dithering-matrix file (only used by the ordered algorithm).
    """
    if algorithm not in ALGORITHMS:
        raise ValueError(f"unknown dithering algorithm {algorithm!r}; choose {ALGORITHMS}")
    cols = _palette_colors(palette)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        src, out, pal = tmpdir / "in.png", tmpdir / "out.png", tmpdir / "palette.json"
        sprite.save(src)
        if cols:
            pal.write_text(json.dumps(cols))
        run_batch(_DITHER_LUA, {"input": str(src), "output": str(out),
                           "palette": str(pal) if cols else "",
                           "algorithm": algorithm,
                           "matrix": str(Path(matrix).resolve()) if matrix else ""}, timeout)
        return _load_result(out)


def quantize(sprite: Sprite, colors: int = 16, dither: str | None = None,
             timeout: int = 120) -> Sprite:
    """Reduce `sprite` to at most `colors` colours with Aseprite's quantizer.

    Aseprite's ``ColorQuantization`` shrinks the palette; a following indexed
    conversion maps the pixels onto it. `dither` (one of :data:`ALGORITHMS`, or
    None for flat nearest-colour mapping) chooses how pixels snap to the palette.
    """
    if colors < 2:
        raise ValueError("colors must be at least 2")
    if dither is not None and dither not in ALGORITHMS:
        raise ValueError(f"unknown dithering algorithm {dither!r}; choose {ALGORITHMS}")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        src, out = tmpdir / "in.png", tmpdir / "out.png"
        sprite.save(src)
        run_batch(_QUANTIZE_LUA, {"input": str(src), "output": str(out), "colors": str(int(colors)),
                             "algorithm": dither or ""}, timeout)
        return _load_result(out)


def blend(base: Sprite, top: Sprite, mode: str = "normal", opacity: float = 1.0,
          timeout: int = 120) -> Sprite:
    """Composite `top` over `base` with an Aseprite blend mode and opacity (0..1)."""
    if mode not in BLEND_MODES:
        raise ValueError(f"unknown blend mode {mode!r}; choose {sorted(BLEND_MODES)}")
    level = max(0, min(255, round(opacity * 255)))
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        src, tp, out = tmpdir / "base.png", tmpdir / "top.png", tmpdir / "out.png"
        base.save(src)
        top.save(tp)
        run_batch(_BLEND_LUA, {"input": str(src), "top": str(tp), "output": str(out),
                          "mode": BLEND_MODES[mode], "opacity": str(level), "name": mode}, timeout)
        return _load_result(out)


PASS_OPS = ("quantize", "dither")


def run_pass(sprite: Sprite, spec: dict | None) -> Sprite:
    """Run one named Aseprite pass described by `spec`; a falsy spec is a no-op.

    ``spec = {"op": "quantize", "colors": 16, "dither": None}``
    ``spec = {"op": "dither", "algorithm": "ordered", "palette": ["#rrggbb", ...],
              "matrix": None}``
    """
    if not spec:
        return sprite
    op = str(spec.get("op", "")).lower()
    if op == "quantize":
        return quantize(sprite, colors=int(spec.get("colors", 16)), dither=spec.get("dither"))
    if op == "dither":
        return dither(sprite, algorithm=spec.get("algorithm", "ordered"),
                      palette=spec.get("palette"), matrix=spec.get("matrix"))
    raise ValueError(f"unknown aseprite pass {op!r}; choose {PASS_OPS}")
