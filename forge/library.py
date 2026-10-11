"""Saved results: library/<id>/ with every step, the final sprite and metadata."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from .sprite import Sprite

ROOT = Path(__file__).resolve().parent.parent / "library"


def allocate_id(prompt: str) -> str:
    """Allocate the canonical ID without publishing a directory."""
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "sprite"
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}-{uuid.uuid4().hex[:4]}"


def new_dir(prompt: str) -> tuple[str, Path]:
    item_id = allocate_id(prompt)
    d = ROOT / item_id
    d.mkdir(parents=True)
    return item_id, d


def save_result(d: Path, sprite: Sprite, meta: dict) -> None:
    sprite.save(d / "sprite.png")
    sprite.save(d / "sprite@8x.png", scale=8)
    (d / "sprite.json").write_text(json.dumps(sprite.to_dict(), indent=1))
    (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))


def list_items(limit: int = 60) -> list[dict]:
    if not ROOT.exists():
        return []
    items = []
    for d in sorted(ROOT.iterdir(), reverse=True):
        if (d / "sprite.png").exists():
            meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
            items.append({"id": d.name, "prompt": meta.get("prompt", ""), "size": meta.get("size"),
                          "kind": meta.get("kind", "generate")})
        if len(items) >= limit:
            break
    return items


def item_path(item_id: str, name: str = "sprite.png") -> Path:
    p = (ROOT / Path(item_id).name / Path(name).name).resolve()
    if ROOT.resolve() not in p.parents:
        raise ValueError("bad path")
    return p
