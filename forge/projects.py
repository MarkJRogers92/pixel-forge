"""Pull pixel art from an existing project folder.

  register(path)        remember a folder of art (e.g. a game's assets directory)
  scan(project)         index its PNGs (read-only; nothing in the project is modified)
  learn_style(project)  build a style profile automatically: shared palette,
                        typical sizes, colour counts, outline habit, and a few
                        representative reference sprites for the artist to look at
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

import numpy as np
from PIL import Image

from . import profiles, qa
from .sprite import Sprite

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "projects.json"
MAX_FILES = 3000
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".venv", "__pycache__"}


def _load() -> dict:
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}


def _save(d: dict) -> None:
    REGISTRY.write_text(json.dumps(d, indent=1))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "project"


def register(path: str | Path, name: str | None = None, project_id: str | None = None) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise FileNotFoundError(f"not a folder: {p}")
    pid = project_id or slug(name or p.name)
    d = _load()
    d[pid] = {"name": name or p.name, "path": str(p)}
    _save(d)
    return {"id": pid, **d[pid]}


def list_projects() -> list[dict]:
    return [{"id": k, **v} for k, v in _load().items()]


def get(project: str) -> dict:
    d = _load()
    if project in d:
        return {"id": project, **d[project]}
    p = Path(project).expanduser()
    if p.is_dir():
        return register(p)
    raise FileNotFoundError(f"unknown project {project!r}")


def scan(project: str) -> list[dict]:
    root = Path(get(project)["path"])
    out = []
    for f in sorted(root.rglob("*.png")):
        if any(part in SKIP_DIRS for part in f.relative_to(root).parts):
            continue
        try:
            with Image.open(f) as im:
                w, h = im.size
        except Exception:
            continue
        out.append({"path": str(f.relative_to(root)), "width": w, "height": h,
                    "folder": str(f.parent.relative_to(root))})
        if len(out) >= MAX_FILES:
            break
    return out


def file_path(project: str, rel: str) -> Path:
    root = Path(get(project)["path"]).resolve()
    p = (root / rel).resolve()
    if root not in p.parents or p.suffix.lower() != ".png":
        raise ValueError("path outside project")
    return p


def _pick_references(files: list[dict], n: int) -> list[dict]:
    """Varied, sprite-sized examples: one per folder first, mid-sized first."""
    sprites = [f for f in files if 16 <= max(f["width"], f["height"]) <= 160]
    sprites.sort(key=lambda f: abs(max(f["width"], f["height"]) - 64))
    picked, seen = [], set()
    for f in sprites:
        if f["folder"] not in seen:
            picked.append(f)
            seen.add(f["folder"])
        if len(picked) >= n:
            return picked
    for f in sprites:
        if f not in picked:
            picked.append(f)
        if len(picked) >= n:
            break
    return picked


def learn_style(project: str, profile_id: str | None = None, name: str | None = None,
                colors: int = 32, n_refs: int = 4, include: str | None = None) -> dict:
    """Analyse the project's art and write profiles/<id>.json. Returns a summary."""
    proj = get(project)
    root = Path(proj["path"])
    files = scan(project)
    if include:
        files = [f for f in files if include in f["path"]]
    if not files:
        raise ValueError("no PNG files found")

    sample = [f for f in files if max(f["width"], f["height"]) <= 512][:120]
    color_counts, outlines, sizes = [], [], []
    for f in sample:
        try:
            s = Sprite.from_image(Image.open(root / f["path"]))
        except ValueError:
            continue  # too many colours: not flat pixel art (photo, gradient UI…)
        r = qa.check(s)
        if r.get("colors"):
            color_counts.append(r["colors"])
        if "outline_coverage" in r:
            outlines.append(r["outline_coverage"])
        if max(s.width, s.height) <= 160:
            sizes.append((s.width, s.height))

    pid = profile_id or proj["id"]
    refs = _pick_references(files, n_refs)
    ref_paths = [root / f["path"] for f in refs]
    palette = profiles.palette_from_images([root / f["path"] for f in sample[:80]], colors)

    med_colors = int(statistics.median(color_counts)) if color_counts else 16
    med_outline = statistics.median(outlines) if outlines else 0
    size = ([int(statistics.median(w for w, _ in sizes)), int(statistics.median(h for _, h in sizes))]
            if sizes else [32, 32])
    outline_txt = ("dark outer outlines on almost every sprite" if med_outline >= 0.8 else
                   "partial / selective outlines" if med_outline >= 0.4 else
                   "mostly no dark outline; edges defined by colour")
    style = (f"Match the existing art of '{proj['name']}' ({len(files)} images analysed). "
             f"Typical sprite: about {size[0]}x{size[1]} px using ~{med_colors} colours, "
             f"{outline_txt}. Keep proportions, level of detail and shading style consistent "
             f"with the reference images.")

    refdir = profiles.ROOT / "refs" / pid
    refdir.mkdir(parents=True, exist_ok=True)
    rel_refs = []
    for src in ref_paths:
        dst = refdir / src.name
        dst.write_bytes(src.read_bytes())
        rel_refs.append(str(dst.relative_to(profiles.ROOT)))

    prof = {"name": name or proj["name"], "style": style, "palette": palette,
            "palette_mode": "prefer", "size": size, "references": rel_refs,
            "source": {"project": proj["id"], "path": proj["path"], "files": len(files)}}
    profiles.ROOT.mkdir(exist_ok=True)
    (profiles.ROOT / f"{pid}.json").write_text(json.dumps(prof, indent=2))
    return {"profile": pid, "style": style, "colors": len(palette), "size": size,
            "references": [f["path"] for f in refs]}
