"""Sprite document: a small indexed-color pixel grid.

The canonical format is deliberately human- and Claude-readable:

    {"width": 8, "height": 2,
     "palette": {"k": "#1a1420", "a": "#f2e6c8"},
     "rows": ["..kkkk..", ".kaaaak."]}

"." is always transparent. Every other character is a palette key.
"""
from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

TRANSPARENT = "."
KEY_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MAX_SIDE = 1024


def parse_hex(value: str) -> tuple[int, int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) not in (6, 8):
        raise ValueError(f"bad color {value!r}")
    r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    a = int(v[6:8], 16) if len(v) == 8 else 255
    return r, g, b, a


def to_hex(rgba) -> str:
    r, g, b = (int(x) for x in rgba[:3])
    a = int(rgba[3]) if len(rgba) > 3 else 255
    return f"#{r:02x}{g:02x}{b:02x}" + ("" if a == 255 else f"{a:02x}")


@dataclass
class Sprite:
    width: int
    height: int
    palette: dict[str, str] = field(default_factory=dict)
    grid: list[list[str]] = field(default_factory=list)

    def __post_init__(self):
        if not (1 <= self.width <= MAX_SIDE and 1 <= self.height <= MAX_SIDE):
            raise ValueError(f"size {self.width}x{self.height} outside 1..{MAX_SIDE}")
        if not self.grid:
            self.grid = [[TRANSPARENT] * self.width for _ in range(self.height)]

    # ---- access -------------------------------------------------------
    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get(self, x: int, y: int) -> str:
        return self.grid[y][x] if self.inside(x, y) else TRANSPARENT

    def set(self, x: int, y: int, key: str) -> None:
        if self.inside(x, y):
            self.grid[y][x] = key

    def copy(self) -> "Sprite":
        return Sprite(self.width, self.height, dict(self.palette), [row[:] for row in self.grid])

    def used_keys(self) -> set[str]:
        return {k for row in self.grid for k in row if k != TRANSPARENT}

    # ---- serialization ------------------------------------------------
    def rows(self) -> list[str]:
        return ["".join(row) for row in self.grid]

    def to_dict(self) -> dict:
        used = self.used_keys()
        return {
            "width": self.width,
            "height": self.height,
            "palette": {k: v for k, v in self.palette.items() if k in used},
            "rows": self.rows(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Sprite":
        rows = d["rows"]
        w, h = int(d.get("width", len(rows[0]))), int(d.get("height", len(rows)))
        grid = []
        for y in range(h):
            row = rows[y] if y < len(rows) else ""
            grid.append([(row[x] if x < len(row) else TRANSPARENT) for x in range(w)])
        return cls(w, h, dict(d["palette"]), grid)

    def to_rgba(self) -> np.ndarray:
        lut = {k: parse_hex(v) for k, v in self.palette.items()}
        arr = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        for y, row in enumerate(self.grid):
            for x, k in enumerate(row):
                if k != TRANSPARENT and k in lut:
                    arr[y, x] = lut[k]
        return arr

    def to_image(self, scale: int = 1) -> Image.Image:
        img = Image.fromarray(self.to_rgba(), "RGBA")
        if scale > 1:
            img = img.resize((self.width * scale, self.height * scale), Image.NEAREST)
        return img

    def to_png_bytes(self, scale: int = 1) -> bytes:
        buf = io.BytesIO()
        self.to_image(scale).save(buf, "PNG")
        return buf.getvalue()

    def to_data_url(self, scale: int = 1) -> str:
        return "data:image/png;base64," + base64.b64encode(self.to_png_bytes(scale)).decode()

    def save(self, path: str | Path, scale: int = 1) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.to_image(scale).save(path, "PNG")
        return path

    @classmethod
    def from_image(cls, img: Image.Image, alpha_threshold: int = 128) -> "Sprite":
        """Exact conversion of an image that is already true pixel art."""
        arr = np.asarray(img.convert("RGBA"))
        h, w = arr.shape[:2]
        colors: dict[tuple, str] = {}
        grid = []
        for y in range(h):
            row = []
            for x in range(w):
                px = arr[y, x]
                if px[3] < alpha_threshold:
                    row.append(TRANSPARENT)
                    continue
                c = (int(px[0]), int(px[1]), int(px[2]))
                if c not in colors:
                    if len(colors) >= len(KEY_CHARS):
                        raise ValueError("too many colors; run cleanup first")
                    colors[c] = KEY_CHARS[len(colors)]
                row.append(colors[c])
            grid.append(row)
        return cls(w, h, {k: to_hex(c) for c, k in colors.items()}, grid)


def preview_image(sprite: Sprite, scale: int = 8, grid: bool = False) -> Image.Image:
    """Upscaled render on a checkerboard, used for Claude's visual review."""
    w, h = sprite.width * scale, sprite.height * scale
    bg = Image.new("RGBA", (w, h), (200, 200, 200, 255))
    cell = max(scale * 2, 8)
    px = bg.load()
    for y in range(h):
        for x in range(w):
            if ((x // cell) + (y // cell)) % 2:
                px[x, y] = (232, 232, 232, 255)
    bg.alpha_composite(sprite.to_image(scale))
    if grid and scale >= 6:
        g = bg.load()
        for y in range(h):
            for x in range(w):
                if x % scale == 0 or y % scale == 0:
                    r, gg, b, a = g[x, y]
                    g[x, y] = (r * 7 // 8, gg * 7 // 8, b * 7 // 8, a)
    return bg


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
