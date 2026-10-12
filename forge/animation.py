"""Aseprite-backed animation: frames, tags, sprite-sheet and GIF export.

Aseprite 1.3.18.6 in batch mode gives us what a static sprite pipeline cannot:
``sprite:newFrame()`` with per-frame durations, ``sprite:newTag()`` with an
animation direction, ``app.command.ExportSpriteSheet{...}`` (texture + JSON
atlas) and animated GIF output. This module turns an ordered list of Pixel Forge
sprites into a game-ready animation. Every call used here was verified on the
installed build; see ASEPRITE-NOTES.md.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import aseprite_ops
from .aseprite_ops import AsepriteError

# AniDir members read live (api 41): FORWARD=0, REVERSE=1, PING_PONG=2.
LOOP_DIRS = {"forward": "FORWARD", "reverse": "REVERSE", "pingpong": "PING_PONG"}
SHEET_TYPES = ("horizontal", "vertical", "packed", "rows", "columns", "grid")
DATA_FORMATS = ("json-hash", "json-array")
# Games/engines we emit a drop-in descriptor for. DEAD MALL (~/code/90s) is Phaser 4:
# it loads one PNG per texture and numbers a horizontal strip's frames '0'..'n-1'.
GAMES = ("phaser",)

_ANIM_LUA = r"""
local frames = json.decode(io.open(app.params.frames):read("a"))
local dur = tonumber(app.params.duration)
local spr = Sprite(frames[1].w, frames[1].h, ColorMode.RGB)
spr:newCel(spr.layers[1], spr.frames[1], Image{ fromFile = frames[1].path }, Point(0, 0))
spr.frames[1].duration = dur
for i = 2, #frames do
  local f = spr:newFrame()
  spr:newCel(spr.layers[1], f, Image{ fromFile = frames[i].path }, Point(0, 0))
  f.duration = dur
end
if app.params.tag ~= "" then
  local t = spr:newTag(spr.frames[1])
  t.name = app.params.tag
  t.fromFrame = spr.frames[1]
  t.toFrame = spr.frames[#spr.frames]
  t.aniDir = AniDir[app.params.loop]
end
local args = { ui = false, type = app.params.sheet_type,
               textureFilename = app.params.sheet, dataFormat = app.params.data_format }
if app.params.data ~= "" then args.dataFilename = app.params.data end
app.command.ExportSpriteSheet(args)
if app.params.gif ~= "" then spr:saveAs(app.params.gif) end
if app.params.project ~= "" then spr:saveAs(app.params.project) end
"""


def _atlas_frames(data_path):
    """Frame rects in playback order, read from the Aseprite JSON atlas we wrote."""
    data = json.loads(Path(data_path).read_text())
    entries = data.get("frames", {})
    entries = entries.values() if isinstance(entries, dict) else entries
    rects = []
    for i, entry in enumerate(entries):
        r = entry["frame"]
        rects.append({"index": i, "x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"],
                      "durationMs": entry.get("duration")})
    return rects


def _descriptor(game, sheet, data, sheet_type, count, size, fps, tag, loop, duration):
    """A drop-in geometry descriptor for a game engine (see GAMES)."""
    import hashlib
    w, h = size
    rects = (_atlas_frames(data) if data and Path(data).exists()
             else [{"index": i, "x": i * w, "y": 0, "w": w, "h": h,
                    "durationMs": round(duration * 1000)} for i in range(count)])
    descriptor = {
        "engine": game,
        "texture": Path(sheet).name,
        "sheetType": sheet_type,
        "frameWidth": w,
        "frameHeight": h,
        "frameCount": len(rects),
        "fps": fps,
        "durationMs": round(duration * 1000),
        "loop": loop,
        "tag": tag,
        "frames": rects,
        "sha256": hashlib.sha256(Path(sheet).read_bytes()).hexdigest(),
    }
    if game == "phaser":
        n = len(rects)
        yoyo = "true" if loop == "pingpong" else "false"
        descriptor["phaser"] = {
            "loadImage": f"this.load.image('<key>', '/assets/<folder>/{Path(sheet).name}')",
            "addFrames": "for (let i = 0; i < count; i++) texture.add(String(i), 0, "
                         "i * frameWidth, 0, frameWidth, frameHeight)",
            "createAnim": "this.anims.create({ key: '<animKey>', frames: this.anims."
                          "generateFrameNumbers('<textureKey>', { start: 0, end: " + str(n - 1)
                          + " }), frameRate: " + str(fps) + ", repeat: -1, yoyo: " + yoyo + " })",
        }
    return descriptor


def animate(frames, out_dir, tag: str = "animation", fps: float = 10.0, loop: str = "forward",
            sheet_type: str = "horizontal", data_format: str = "json-hash",
            sheet_name: str = "sheet.png", data_name: str | None = "sheet.json",
            gif_name: str | None = "animation.gif", project_name: str | None = None,
            game: str | None = None, descriptor_name: str = "animation.json",
            timeout: int = 180) -> dict:
    """Build an animation from `frames` (Sprites of equal size) in `out_dir`.

    Exports a sprite-sheet texture (default sheet.png), its JSON atlas
    (sheet.json), an animated GIF for review (animation.gif) and, if
    `project_name` is given, an editable `.aseprite`. `fps` sets every frame's
    duration; `loop` is "forward", "reverse" or "pingpong".
    """
    frames = list(frames)
    if not frames:
        raise ValueError("animate needs at least one frame")
    if loop not in LOOP_DIRS:
        raise ValueError(f"loop must be one of {sorted(LOOP_DIRS)}")
    if sheet_type not in SHEET_TYPES:
        raise ValueError(f"sheet_type must be one of {SHEET_TYPES}")
    if data_format not in DATA_FORMATS:
        raise ValueError(f"data_format must be one of {DATA_FORMATS}")
    if game is not None and game not in GAMES:
        raise ValueError(f"game must be one of {GAMES} or None")
    if fps <= 0:
        raise ValueError("fps must be positive")
    sizes = {(f.width, f.height) for f in frames}
    if len(sizes) != 1:
        raise ValueError(f"all frames must be the same size; got {sorted(sizes)}")
    duration = 1.0 / float(fps)

    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet = out_dir / sheet_name
    data = out_dir / data_name if data_name else None
    gif = out_dir / gif_name if gif_name else None
    project = out_dir / project_name if project_name else None

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        spec = []
        for i, f in enumerate(frames):
            p = t / f"frame_{i:03d}.png"
            f.save(p)
            spec.append({"path": str(p), "w": f.width, "h": f.height})
        spec_path = t / "frames.json"
        spec_path.write_text(json.dumps(spec))
        aseprite_ops.run_batch(_ANIM_LUA, {
            "frames": str(spec_path), "duration": repr(duration), "tag": tag,
            "loop": LOOP_DIRS[loop], "sheet_type": sheet_type, "sheet": str(sheet),
            "data": str(data) if data else "", "data_format": data_format,
            "gif": str(gif) if gif else "", "project": str(project) if project else ""}, timeout)

    for path in (sheet, data, gif, project):
        if path is not None and not path.exists():
            raise AsepriteError(f"Aseprite did not write {path}")

    result = {"frames": len(frames), "size": [frames[0].width, frames[0].height],
              "fps": fps, "duration": duration, "tag": tag, "loop": loop,
              "sheet": str(sheet)}
    for key, path in (("data", data), ("gif", gif), ("project", project)):
        if path is not None:
            result[key] = str(path)
    if game:
        descriptor = _descriptor(game, sheet, data, sheet_type, len(frames),
                                 (frames[0].width, frames[0].height), fps, tag, loop, duration)
        descriptor_path = out_dir / descriptor_name
        descriptor_path.write_text(json.dumps(descriptor, indent=1))
        result["descriptor"] = str(descriptor_path)
    return result
