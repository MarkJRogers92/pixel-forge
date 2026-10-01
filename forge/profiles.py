"""Style profiles: reusable palette + style notes + default size + reference images.

A profile is profiles/<id>.json:
{
  "name": "DEAD MALL (neon)",
  "style": "free text the artist reads",
  "palette": {"a": "#..."},      # optional
  "palette_mode": "prefer" | "strict",
  "ramps": {...},                # optional
  "size": [32, 32],
  "references": ["refs/foo.png"] # relative to the profile file
}
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .cleanup import kmeans_palette, rgb_to_lab
from .sprite import KEY_CHARS, to_hex

ROOT = Path(__file__).resolve().parent.parent / "profiles"


def list_profiles() -> list[dict]:
    out = []
    for p in sorted(ROOT.glob("*.json")):
        d = json.loads(p.read_text())
        out.append({"id": p.stem, "name": d.get("name", p.stem), "size": d.get("size"),
                    "colors": len(d.get("palette") or {})})
    return out


def load(profile_id: str | None) -> dict | None:
    if not profile_id:
        return None
    p = ROOT / f"{Path(profile_id).name}.json"
    if not p.exists():
        raise FileNotFoundError(f"profile {profile_id!r} not found")
    d = json.loads(p.read_text())
    d["id"] = p.stem
    d["reference_paths"] = [str((p.parent / r).resolve()) for r in d.get("references", [])
                            if (p.parent / r).exists()]
    return d


def palette_from_images(paths: list[Path], colors: int = 24) -> dict[str, str]:
    """Extract a shared palette (dark -> light keys) from existing art."""
    pixels = []
    for path in paths:
        arr = np.asarray(Image.open(path).convert("RGBA"))
        px = arr[arr[..., 3] >= 128][:, :3]
        if len(px) > 20000:
            px = px[np.random.default_rng(0).choice(len(px), 20000, replace=False)]
        pixels.append(px)
    allpx = np.concatenate(pixels) if pixels else np.zeros((0, 3), np.uint8)
    pal = kmeans_palette(allpx, colors)
    pal = pal[np.argsort(rgb_to_lab(pal)[:, 0])]
    return {KEY_CHARS[i]: to_hex(c) for i, c in enumerate(pal)}


def create_from_images(profile_id: str, name: str, paths: list[Path], colors: int = 24,
                       style: str = "", size: tuple[int, int] = (32, 32), n_refs: int = 4) -> Path:
    ROOT.mkdir(exist_ok=True)
    refdir = ROOT / "refs" / profile_id
    refdir.mkdir(parents=True, exist_ok=True)
    refs = []
    for p in paths[:n_refs]:
        dst = refdir / Path(p).name
        dst.write_bytes(Path(p).read_bytes())
        refs.append(str(dst.relative_to(ROOT)))
    d = {"name": name, "style": style, "palette": palette_from_images(paths, colors),
         "palette_mode": "prefer", "size": list(size), "references": refs}
    out = ROOT / f"{profile_id}.json"
    out.write_text(json.dumps(d, indent=2))
    return out
