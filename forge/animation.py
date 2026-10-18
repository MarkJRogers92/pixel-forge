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


def render_frames(program: dict, base=None):
    """Render each literal animation frame over a fresh copy of common artwork."""
    from . import dsl

    if not isinstance(program, dict):
        raise ValueError("animation program must be a JSON object")
    frame_ops = program.get("frames")
    if not isinstance(frame_ops, list) or not frame_ops:
        raise ValueError("animation program needs a non-empty 'frames' list")
    if any(not isinstance(ops, list) for ops in frame_ops):
        raise ValueError("each animation frame must be a list of operations")

    common_program = {key: value for key, value in program.items() if key != "frames"}
    if base is not None and common_program.get("base") == "current":
        from .sprite import parse_hex

        for key, color in (common_program.get("palette") or {}).items():
            if key in base.palette and parse_hex(color) != parse_hex(base.palette[key]):
                raise ValueError(f"palette key {key!r} conflicts with supplied base color; "
                                 "choose another key to preserve existing pixels")
    common = dsl.render(common_program, base)
    if common.errors:
        raise ValueError("common animation operations failed: " + "; ".join(common.errors))

    frames = []
    for index, ops in enumerate(frame_ops):
        frame_program = {**common_program, "base": "current", "ops": ops}
        result = dsl.render(frame_program, common.sprite.copy())
        if result.errors:
            raise ValueError(f"frame[{index}] operations failed: " + "; ".join(result.errors))
        frames.append(result.sprite)
    return frames

# AniDir members read live (api 41): FORWARD=0, REVERSE=1, PING_PONG=2.
LOOP_DIRS = {"forward": "FORWARD", "reverse": "REVERSE", "pingpong": "PING_PONG"}
# Aseprite also accepts type="grid", but that layout reads the sprite's grid and
# this flow never creates one: Aseprite then stacks every frame at (0,0) and
# emits an atlas whose frames all overlap (measured, see the CodeAndWeb REA run's
# bench/RESULTS.md). Deliberately not offered.
SHEET_TYPES = ("horizontal", "vertical", "packed", "rows", "columns")
DATA_FORMATS = ("json-hash", "json-array")
# Games/engines we emit a drop-in descriptor for. DEAD MALL (~/code/90s) is Phaser 4:
# it loads one PNG per texture and numbers a horizontal strip's frames '0'..'n-1'.
GAMES = ("phaser",)
# ExportSpriteSheet option names are case-sensitive camelCase and Aseprite
# SILENTLY IGNORES unknown keys (verified: a bogus key still exits 0), so these
# are the names proven to change the output on Aseprite 1.3.18.6:
#   trim, extrude, shapePadding, borderPadding, innerPadding, mergeDuplicates,
#   powerOfTwoSize (NOT "powerOfTwo", which is a no-op), sheetWidth, sheetHeight,
#   sheetColumns, sheetRows, ignoreEmpty, splitGrid.
# `trim` alone measured 75% smaller sheets on sprites with transparent margins;
# `trim` without `extrude` bleeds at frame borders, so they belong together.
PADDING_KEYS = {"shape_padding": "shapePadding", "border_padding": "borderPadding",
                "inner_padding": "innerPadding"}

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
local opts = json.decode(app.params.sheet_opts or "{}")
for k, v in pairs(opts) do args[k] = v end
app.command.ExportSpriteSheet(args)
if app.params.gif ~= "" then spr:saveAs(app.params.gif) end
if app.params.project ~= "" then spr:saveAs(app.params.project) end
"""


def _atlas_frames(data_path):
    """Frame rects in playback order, read from the Aseprite JSON atlas we wrote.

    A trimmed (or rotated) atlas is carried through in full: `spriteSourceSize`
    and `sourceSize` say where the trimmed rect sat inside the original frame, so
    a consumer can still draw the sprite in the same place.
    """
    data = json.loads(Path(data_path).read_text())
    entries = data.get("frames", {})
    entries = entries.values() if isinstance(entries, dict) else entries
    rects = []
    for i, entry in enumerate(entries):
        r = entry["frame"]
        rect = {"index": i, "x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"],
                "durationMs": entry.get("duration")}
        if entry.get("trimmed"):
            rect["trimmed"] = True
            rect["spriteSourceSize"] = entry.get("spriteSourceSize")
            rect["sourceSize"] = entry.get("sourceSize")
        if entry.get("rotated"):
            rect["rotated"] = True
        rects.append(rect)
    return rects


def _atlas_utilisation(data_path, sheet_path=None):
    """Fraction of the sheet that is actually sprite, i.e. TexturePacker's "texture usage".

    TP's `pack-mode Good` targets "at least 90% of texture usage"; a number this
    low means the sheet is mostly padding and a different sheet_type, order or
    trimming would pay. Read from the atlas we wrote (the PNG size is cross-checked
    only when a path is given).
    """
    data = json.loads(Path(data_path).read_text())
    rects = _atlas_frames(data_path)
    size = (data.get("meta") or {}).get("size") or {}
    if not (size.get("w") and size.get("h")):
        return None
    if sheet_path is not None:
        from PIL import Image
        with Image.open(sheet_path) as im:
            if (im.width, im.height) != (int(size["w"]), int(size["h"])):
                size = {"w": im.width, "h": im.height}
    area = int(size["w"]) * int(size["h"])
    return round(sum(f["w"] * f["h"] for f in rects) / area, 4) if area else None


def _descriptor(game, sheet, data, sheet_type, count, size, fps, tag, loop, duration):
    """A drop-in geometry descriptor for a game engine (see GAMES)."""
    import hashlib
    w, h = size
    rects = (_atlas_frames(data) if data and Path(data).exists()
             else [{"index": i, "x": i * w, "y": 0, "w": w, "h": h,
                    "durationMs": round(duration * 1000)} for i in range(count)])
    trimmed = any(f.get("trimmed") for f in rects)
    rotated = any(f.get("rotated") for f in rects)
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
        "atlasTrimmed": trimmed,
        "frames": rects,
        "sha256": hashlib.sha256(Path(sheet).read_bytes()).hexdigest(),
    }
    if game == "phaser":
        n = len(rects)
        yoyo = "true" if loop == "pingpong" else "false"
        if trimmed or rotated:
            # Every frame sits at its own rect: the uniform strip snippet would
            # silently load the wrong pixels.
            add_frames = ("for (const f of frames) texture.add(String(f.index), 0, "
                          "f.x, f.y, f.w, f.h)")
        else:
            add_frames = ("for (let i = 0; i < count; i++) texture.add(String(i), 0, "
                          "i * frameWidth, 0, frameWidth, frameHeight)")
        descriptor["phaser"] = {
            "loadImage": f"this.load.image('<key>', '/assets/<folder>/{Path(sheet).name}')",
            "addFrames": add_frames,
            "createAnim": "this.anims.create({ key: '<animKey>', frames: this.anims."
                          "generateFrameNumbers('<textureKey>', { start: 0, end: " + str(n - 1)
                          + " }), frameRate: " + str(fps) + ", repeat: -1, yoyo: " + yoyo + " })",
        }
        if trimmed:
            descriptor["phaser"]["trimmedNote"] = (
                "atlas is trimmed: each frame's `spriteSourceSize` is the offset of the "
                "trimmed rect inside the original frame; set the sprite origin from it "
                "(or export with trim=false for a uniform grid)")
        if rotated:
            descriptor["phaser"]["rotatedNote"] = (
                "atlas has rotated frames: Aseprite's rect is stored rotated, so swap "
                "w/h when adding the frame")
    return descriptor


def animate(frames, out_dir, tag: str = "animation", fps: float = 10.0, loop: str = "forward",
            sheet_type: str = "horizontal", data_format: str = "json-hash",
            sheet_name: str = "sheet.png", data_name: str | None = "sheet.json",
            gif_name: str | None = "animation.gif", project_name: str | None = None,
            game: str | None = None, descriptor_name: str = "animation.json",
            trim: bool = False, extrude: bool = False, shape_padding: int = 0,
            border_padding: int = 0, inner_padding: int = 0, power_of_two: bool = False,
            merge_duplicates: bool = False, timeout: int = 180) -> dict:
    """Build an animation from `frames` (Sprites of equal size) in `out_dir`.

    Exports a sprite-sheet texture (default sheet.png), its JSON atlas
    (sheet.json), an animated GIF for review (animation.gif) and, if
    `project_name` is given, an editable `.aseprite`. `fps` sets every frame's
    duration; `loop` is "forward", "reverse" or "pingpong".

    Sheet packing (all off by default, all measured on Aseprite 1.3.18.6):
    `trim` crops each frame's transparent border and records where it was
    (`spriteSourceSize`), which is worth ~75% of the sheet area on sprites that
    carry a margin. `extrude` duplicates every edge pixel one pixel outwards and
    is the correct companion to `trim` — without it the neighbouring frame
    bleeds in at the border. `shape_padding` / `border_padding` / `inner_padding`
    add spacing between frames / at the sheet border / inside each frame;
    `merge_duplicates` stores identical frames once; `power_of_two` rounds the
    sheet up to a power of two (engine formats that need it, e.g. GPU
    compression). Turn `trim` on with the game's own loader in mind: a trimmed
    atlas needs the per-frame offset, which the descriptor reports.
    """
    frames = list(frames)
    if not frames:
        raise ValueError("animate needs at least one frame")
    if loop not in LOOP_DIRS:
        raise ValueError(f"loop must be one of {sorted(LOOP_DIRS)}")
    if sheet_type == "grid":
        raise ValueError("sheet_type 'grid' is not supported: it needs a sprite grid that "
                         f"this flow never creates, so Aseprite would emit overlapping frames. "
                         f"Use one of {SHEET_TYPES}")
    if sheet_type not in SHEET_TYPES:
        raise ValueError(f"sheet_type must be one of {SHEET_TYPES}")
    if data_format not in DATA_FORMATS:
        raise ValueError(f"data_format must be one of {DATA_FORMATS}")
    if game is not None and game not in GAMES:
        raise ValueError(f"game must be one of {GAMES} or None")
    if fps <= 0:
        raise ValueError("fps must be positive")
    paddings = {"shape_padding": shape_padding, "border_padding": border_padding,
                "inner_padding": inner_padding}
    for name, value in paddings.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    sizes = {(f.width, f.height) for f in frames}
    if len(sizes) != 1:
        raise ValueError(f"all frames must be the same size; got {sorted(sizes)}")
    duration = 1.0 / float(fps)

    # Only non-default options are sent, so a plain call keeps producing exactly
    # the sheet it produced before this parameter set existed.
    sheet_opts = {}
    if trim:
        sheet_opts["trim"] = True
    if extrude:
        sheet_opts["extrude"] = True
    for name, key in PADDING_KEYS.items():
        if paddings[name]:
            sheet_opts[key] = paddings[name]
    if power_of_two:
        sheet_opts["powerOfTwoSize"] = True
    if merge_duplicates:
        sheet_opts["mergeDuplicates"] = True

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
            "sheet_opts": json.dumps(sheet_opts),
            "gif": str(gif) if gif else "", "project": str(project) if project else ""}, timeout)

    for path in (sheet, data, gif, project):
        if path is not None and not path.exists():
            raise AsepriteError(f"Aseprite did not write {path}")

    result = {"frames": len(frames), "size": [frames[0].width, frames[0].height],
              "fps": fps, "duration": duration, "tag": tag, "loop": loop,
              "sheet": str(sheet), "sheet_options": sheet_opts}
    if data is not None:
        result["atlas_utilisation"] = _atlas_utilisation(data, sheet)
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
