"""Both canvases: open a library item in Aseprite or in Forge's Piskel editor,
and bring an edited Aseprite document back into the library.

Aseprite gets a layered document: the final sprite on top (visible), then
every review step, draft and route draft as hidden layers, so a better detail
from a losing draft is one layer toggle away. Piskel opens the item through
the AI panel (`?open=<library id>`).
"""
from __future__ import annotations

import re
import socket
import subprocess
import time
import webbrowser
from pathlib import Path

from PIL import Image

from . import aseprite, library, polish, qa
from .sprite import Sprite

PISKEL_PORT = 9002
EDITORS = ("aseprite", "piskel", "both")


def _key(path: Path) -> tuple:
    m = re.search(r"(\d+)", path.stem)
    return (int(m.group(1)) if m else 0, path.stem)


def document_layers(item_id: str) -> list[tuple[str, Path, bool]]:
    """(name, png, visible), top to bottom: final, review steps (latest first), drafts, routes."""
    d = library.item_path(item_id, "sprite.png").parent
    work = d / "work"
    layers = [("final", d / "sprite.png", True)]
    if work.exists():
        steps = sorted((p for p in work.glob("step_*.png") if p.stem != "step_0"), key=_key, reverse=True)
        layers += [(f"review {_key(p)[0]}", p, False) for p in steps]
        if (work / "combined.png").exists():
            layers.append(("combined", work / "combined.png", False))
        layers += [(f"draft {_key(p)[0]}", p, False) for p in sorted(work.glob("draft_*.png"), key=_key)]
        layers += [(f"route {p.stem[len('route_'):]}", p, False) for p in sorted(work.glob("route_*.png"))]
        if (work / "pixelized.png").exists():
            layers.append(("gpt painting, pixelized", work / "pixelized.png", False))
    return layers


def aseprite_document(item_id: str) -> Path:
    d = library.item_path(item_id, "sprite.png").parent
    return aseprite.build_document(document_layers(item_id), d / "sprite.aseprite")


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ensure_server(port: int) -> None:
    """Start the Forge editor server in the background if nothing is listening yet."""
    if _port_open(port):
        return
    forge_sh = Path(__file__).resolve().parent.parent / "forge.sh"
    subprocess.Popen([str(forge_sh), "serve", "--port", str(port)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if _port_open(port):
            return
        time.sleep(0.25)


def _open_url(url: str) -> None:
    webbrowser.open(url)


def open_item(item_id: str, editor: str = "aseprite", port: int = PISKEL_PORT) -> dict:
    if editor not in EDITORS:
        raise ValueError(f"editor must be one of {EDITORS}")
    out: dict = {"library_id": item_id}
    if editor in ("aseprite", "both"):
        out["aseprite"] = aseprite.open_in_editor(aseprite_document(item_id))
    if editor in ("piskel", "both"):
        _ensure_server(port)
        url = f"http://127.0.0.1:{port}/?open={item_id}"
        _open_url(url)
        out["piskel"] = url
    return out


def import_from_aseprite(doc: Path, out_png: str | None = None, polish_level: str | None = None,
                         polish: bool = False, name: str | None = None) -> dict:
    """The edited document (its visible layers, flattened) as a new library version, checked."""
    doc = Path(doc).expanduser().resolve()
    item_id, d = library.new_dir(name or f"aseprite edit {doc.stem}")
    flat = aseprite.export_png(doc, d / "from_aseprite.png")
    s = Sprite.from_image(Image.open(flat))
    if polish:
        s = _polish(s, polish_level or "normal")
    report = qa.check(s)
    library.save_result(d, s, {"kind": "aseprite-edit", "prompt": name or doc.stem, "source": str(doc),
                               "size": [s.width, s.height], "qa": report})
    result = {"library_id": item_id, "png": str(d / "sprite.png"), "qa": report}
    if out_png:
        dst = Path(out_png).expanduser()
        if dst.exists():
            raise FileExistsError(f"{dst} exists; not overwriting")
        dst.parent.mkdir(parents=True, exist_ok=True)
        s.save(dst)
        result["exported"] = str(dst)
    return result


def _polish(s: Sprite, level: str) -> Sprite:
    return polish.full_polish(s, level)[0]
