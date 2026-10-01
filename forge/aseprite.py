"""Aseprite as Pixel Forge's second canvas.

Forge builds a layered .aseprite document (the final sprite on top, the other
candidates and review steps as hidden layers to compare against or copy
from), opens it in the Aseprite window you already have running, and later
reads your edited document back: flattened to PNG, linted and checked, and
saved as a new library version.

Everything goes through Aseprite's own batch mode (`aseprite -b`), so it works
whether or not the Aseprite AI Artist extension is attached. While it is
attached, an agent can also draw on the opened document live through that
extension's MCP tools.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

CANDIDATES = [
    "/Applications/Aseprite.app/Contents/MacOS/aseprite",
    str(Path.home() / "Applications/Aseprite.app/Contents/MacOS/aseprite"),
    str(Path.home() / "Library/Application Support/Steam/steamapps/common/Aseprite/Aseprite.app/Contents/MacOS/aseprite"),
    "/usr/bin/aseprite",
    "/usr/local/bin/aseprite",
]

BUILD_LUA = r"""
local spec = json.decode(io.open(app.params.spec):read("a"))
local spr = Sprite(spec.width, spec.height, ColorMode.RGB)
local base = spr.layers[1]
-- the first entry ends up on top: build from the bottom of the list upward
for i = #spec.layers, 1, -1 do
  local L = spec.layers[i]
  local layer
  if i == #spec.layers then layer = base else layer = spr:newLayer() end
  layer.name = L.name
  spr:newCel(layer, spr.frames[1], Image{ fromFile = L.path }, Point(L.x or 0, L.y or 0))
  layer.isVisible = L.visible
end
spr:saveAs(spec.out)
"""


class AsepriteError(RuntimeError):
    pass


def find_executable() -> str | None:
    env = os.environ.get("ASEPRITE_PATH")
    if env and Path(env).exists():
        return env
    found = shutil.which("aseprite")
    if found:
        return found
    return next((c for c in CANDIDATES if Path(c).exists()), None)


def _run(args: list[str]) -> str:
    exe = find_executable()
    if not exe:
        raise AsepriteError("Aseprite not found; install it or set ASEPRITE_PATH")
    res = subprocess.run([exe, "-b", *args], capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise AsepriteError(f"aseprite failed ({res.returncode}): {(res.stderr or res.stdout).strip()[:400]}")
    return res.stdout


def build_document(layers: list[tuple[str, Path, bool]], out: Path) -> Path:
    """A layered .aseprite: (name, png, visible) top to bottom; the canvas fits the largest layer."""
    from PIL import Image
    if not layers:
        raise AsepriteError("no layers to build")
    sizes = [Image.open(p).size for _, p, _ in layers]
    w, h = max(s[0] for s in sizes), max(s[1] for s in sizes)
    out = Path(out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    spec = {"width": w, "height": h, "out": str(out),
            "layers": [{"name": name, "path": str(Path(p).resolve()), "visible": bool(visible),
                        # smaller layers sit centred, like the editor panel does
                        "x": (w - size[0]) // 2, "y": (h - size[1]) // 2}
                       for (name, p, visible), size in zip(layers, sizes)]}
    with tempfile.TemporaryDirectory() as tmp:
        spec_path, script = Path(tmp) / "spec.json", Path(tmp) / "build.lua"
        spec_path.write_text(json.dumps(spec))
        script.write_text(BUILD_LUA)
        _run(["--script-param", f"spec={spec_path}", "--script", str(script)])
    if not out.exists():
        raise AsepriteError(f"Aseprite did not write {out}")
    return out


NAMES_LUA = r"""
local spr = app.open(app.params.doc)
local names = {}
for i = #spr.layers, 1, -1 do names[#names + 1] = spr.layers[i].name end
local f = io.open(app.params.out, "w")
f:write(json.encode(names))
f:close()
"""


def layer_names(doc: Path) -> list[str]:
    """Layer names, top to bottom. Read through a file, since extensions may print to stdout in batch mode."""
    with tempfile.TemporaryDirectory() as tmp:
        script, out = Path(tmp) / "names.lua", Path(tmp) / "names.json"
        script.write_text(NAMES_LUA)
        _run(["--script-param", f"doc={Path(doc).expanduser().resolve()}", "--script-param", f"out={out}",
              "--script", str(script)])
        return json.loads(out.read_text())


def export_png(doc: Path, out: Path, layer: str | None = None) -> Path:
    """Flatten the visible layers (or just `layer`, visible or not) to a PNG."""
    out = Path(out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    args = (["--layer", layer] if layer else []) + [str(Path(doc).expanduser().resolve()), "--save-as", str(out)]
    _run(args)
    if not out.exists():
        raise AsepriteError(f"Aseprite did not write {out}")
    return out


def open_in_editor(doc: Path) -> str:
    """Open the document in the running Aseprite window (it starts Aseprite if needed)."""
    doc = Path(doc).expanduser().resolve()
    exe = find_executable()
    if not exe:
        raise AsepriteError("Aseprite not found; install it or set ASEPRITE_PATH")
    if platform.system() == "Darwin" and ".app/" in exe:
        app = exe.split(".app/")[0] + ".app"
        subprocess.run(["open", "-a", app, str(doc)], check=True)
    else:
        subprocess.Popen([exe, str(doc)], start_new_session=True)
    return str(doc)
