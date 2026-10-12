"""Godot 4 side of a sprite sheet: a `SpriteFrames` resource the engine can load.

`animate()` writes a texture plus a JSON atlas. Godot does not read that JSON, so
this turns it into a `SpriteFrames` `.tres` with one `AtlasTexture` per frame —
and for a **trimmed** atlas it puts the frame's `spriteSourceSize` into the
`AtlasTexture.margin`. That is the documented way to keep a trimmed frame
registered against its original canvas: without it, trimming a sheet silently
shifts every sprite inside its own rectangle.

Written for Godot 4 (`.tres`, format 3) and verified by loading the generated file
in the engine — see `tests/test_godot_export.py`, which skips without `godot`.
"""
from __future__ import annotations

import json
from pathlib import Path

GODOT_FORMAT = 3


def _frames_of(atlas: dict) -> list[dict]:
    frames = atlas.get("frames", [])
    return list(frames.values()) if isinstance(frames, dict) else list(frames)


def sprite_frames_tres(atlas_json, texture_res_path: str, out_tres, *,
                       animation: str = "default", fps: float = 10.0,
                       loop: bool = True) -> dict:
    """Write `out_tres` for the atlas at `atlas_json`, referencing `texture_res_path`.

    `texture_res_path` must be the sheet's path *inside the Godot project*
    (`res://...`), not a filesystem path: the resource points at the project, and
    the file must be imported by Godot for the reference to resolve.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    atlas = json.loads(Path(atlas_json).read_text())
    frames = _frames_of(atlas)
    if not frames:
        raise ValueError(f"{atlas_json} has no frames")

    out = Path(out_tres)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'[gd_resource type="SpriteFrames" load_steps={len(frames) + 2} '
             f'format={GODOT_FORMAT}]', "",
             f'[ext_resource type="Texture2D" path="{texture_res_path}" id="1_sheet"]', ""]
    for i, entry in enumerate(frames):
        r = entry["frame"]
        lines.append(f'[sub_resource type="AtlasTexture" id="AtlasTexture_{i}"]')
        lines.append('atlas = ExtResource("1_sheet")')
        lines.append(f"region = Rect2({r['x']}, {r['y']}, {r['w']}, {r['h']})")
        if entry.get("trimmed"):
            # Godot's convention for a trimmed atlas frame: the margin is the
            # untrimmed canvas positioned relative to the drawn region.
            src = entry.get("spriteSourceSize") or {}
            size = entry.get("sourceSize") or {}
            if src and size:
                lines.append(f"margin = Rect2({-int(src['x'])}, {-int(src['y'])}, "
                             f"{int(size['w'])}, {int(size['h'])})")
        lines.append("")

    duration = 1.0 / float(fps)
    frame_blocks = ",\n".join(
        '{\n"duration": %s,\n"texture": SubResource("AtlasTexture_%d")\n}'
        % (repr(round(duration, 6)), i) for i in range(len(frames)))
    lines += [
        "[resource]",
        "animations = [{",
        f'"frames": [{frame_blocks}],',
        f'"loop": {"true" if loop else "false"},',
        f'"name": &"{animation}",',
        f'"speed": {float(fps)}',
        "}]",
        "",
    ]
    out.write_text("\n".join(lines))
    return {"frames": len(frames), "animation": animation, "fps": fps, "loop": loop,
            "texture": texture_res_path, "tres": str(out),
            "trimmed": any(e.get("trimmed") for e in frames)}
