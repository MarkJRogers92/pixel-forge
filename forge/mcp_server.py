"""MCP server so Claude Code, Codex (GPT) or any MCP client can use Pixel Forge.

Two ways for an agent to make art:
  1. forge_make    - describe it; Pixel Forge runs a Claude or GPT artist, reviews
                     the render, runs QA and saves the sprite. Fully automatic.
  2. forge_render  - the calling agent draws itself: read forge_guide once, write a
                     sprite program, look at the returned image, send a fix.

Run:  python -m forge mcp   (stdio)
"""
from __future__ import annotations

import json
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from mcp.types import ToolAnnotations

from . import artist, dsl, library, profiles, projects, qa
from . import lighting
from .cleanup import CleanupOptions, cleanup, load_image
from .sprite import Sprite, preview_image

server = MCPServer(
    "pixel-forge",
    instructions=(
        "Pixel Forge creates real pixel art. Preferred: forge_prepare makes one GPT reference, "
        "uses the free RD neural fixer, then cleanup/QA and a review sheet. Inspect it yourself at "
        "1x/2x; visual review is not automatic. forge_repair optionally requests ONE Claude Opus "
        "pixel patch, creating a new item for another review. Then use forge_open for Aseprite. "
        "Use forge_make for a fully automatic sprite from a "
        "description (optionally in the style of a registered project), or read forge_guide and "
        "draw yourself with forge_render. Use forge_pixelize to turn any image into pixel art, "
        "and forge_add_project / forge_learn_style to pull style and assets from an existing game. "
        "forge_make can also take drafts from PixelLab or Retro Diffusion (routes, spends credits) "
        "and open the result in Aseprite as a layered document (open_in). After editing in "
        "Aseprite, forge_from_aseprite brings the document back, checked, as a new version. "
        "forge_lint reports located problems with the same checks as Aseprite's validate. "
        "forge_dither, forge_quantize and forge_blend run Aseprite's own engine on a PNG "
        "(ordered/error-diffusion dithering onto a palette, colour quantization, and blend-mode "
        "compositing); forge_animate builds a tagged animation from frame PNGs and exports a "
        "sprite sheet + JSON atlas + GIF. All need Aseprite installed."),
)


def _preview(s: Sprite) -> Image:
    import io
    buf = io.BytesIO()
    preview_image(s, 8 if max(s.width, s.height) <= 64 else 4).convert("RGB").save(buf, "PNG")
    return Image(data=buf.getvalue(), format="png")


def _image_of(path, scale: int = 4) -> Image:
    """Nearest-neighbour preview of a full-colour PNG (normal map, lit render)."""
    import io
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        im = im.convert("RGB")
        if scale > 1:
            im = im.resize((im.width * scale, im.height * scale), PILImage.NEAREST)
        buf = io.BytesIO()
        im.save(buf, "PNG")
    return Image(data=buf.getvalue(), format="png")


@server.tool(description="The sprite-program drawing guide. Read once before using forge_render.")
def forge_guide() -> str:
    return artist.GUIDE


@server.tool(description=(
    "Render a sprite program (JSON object as described by forge_guide). Returns an 8x preview "
    "image, render errors and QA. Pass base_png (path) with program.base='current' to edit an "
    "existing sprite. save=true stores the result in the Pixel Forge library."))
def forge_render(program: dict, base_png: str | None = None, save: bool = True,
                 name: str = "agent sprite") -> list:
    base = Sprite.from_image(load_image(Path(base_png).read_bytes())) if base_png else None
    res = dsl.render(program, base)
    info = {"errors": res.errors, "qa": qa.check(res.sprite)}
    if save:
        item_id, d = library.new_dir(name)
        library.save_result(d, res.sprite, {"kind": "agent", "prompt": name, "program": program,
                                            "size": [res.sprite.width, res.sprite.height]})
        info["png"] = str(d / "sprite.png")
    return [json.dumps(info), _preview(res.sprite)]


@server.tool(description=(
    "Fully automatic sprite: describe it and an artist model draws, reviews its own render and "
    "fixes it. artist_name: 'claude' (claude CLI login), 'gpt' (codex CLI login), 'api', or "
    "'gpt-image' (GPT paints a detailed base image, it is pixelized, then Claude refines it: "
    "richest detail, slowest). "
    "project: a registered project id or folder to match its style. out: optional folder/.png "
    "to copy the result to (never overwrites). candidates: parallel drafts judged before refining "
    "(default 3). checks: acceptance requirements to verify. routes: also get drafts from "
    "'pixellab' and/or 'rd' (Retro Diffusion) that compete in the judging (spends credits). "
    "open_in: 'aseprite' (layered document: final on top, every other version as hidden "
    "layers), 'piskel' or 'both'. combine (default true): after judging, the artist brings the "
    "best feature of each other draft into the winner; the combination still has to win the "
    "final judging. Takes 2-6 minutes. aseprite_pass (optional) finishes the result with an "
    "Aseprite engine pass, e.g. {'op':'quantize','colors':16} or "
    "{'op':'dither','algorithm':'ordered'} (palette defaults to the profile's)."))
def forge_make(prompt: str, width: int | None = None, height: int | None = None,
               profile: str | None = None, project: str | None = None, rounds: int = 2,
               artist_name: str | None = None, model: str | None = None,
               start_png: str | None = None, out: str | None = None, candidates: int = 3,
               checks: list[str] | None = None, routes: list[str] | None = None,
               open_in: str | None = None, combine: bool = True,
               aseprite_pass: dict | None = None) -> list:
    from .cli import make
    log: list[str] = []
    result = make(prompt, width, height, profile, project, rounds, artist_name, model,
                  start_png, out, log=log.append, candidates=candidates, checks=checks,
                  routes=routes, open_in=open_in, combine=combine, aseprite_pass=aseprite_pass)
    result["log"] = log
    return [json.dumps(result, default=str),
            _preview(Sprite.from_image(load_image(Path(result["png"]).read_bytes())))]


WORKFLOW_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                 idempotent_hint=False, open_world_hint=True)


def _workflow_response(result: dict, log: list[str]) -> list:
    result["log"] = log
    return [json.dumps(result, default=str), Image(data=Path(result["review"]["sheet"]).read_bytes(), format="png")]


@server.tool(description=(
    "Preferred workflow: one GPT pixel-art-style reference -> free RD neural fixer at target size "
    "-> conservative cleanup/QA -> review sheet at 1x/2x. Uses GPT allowance and the existing RD "
    "key; fixer spends no RD balance. input_png reuses an existing reference and skips GPT. "
    "No Claude, judging, paid RD generation or automatic retries. Returns awaiting_visual_review: "
    "the calling agent must visually review requirements, then optionally forge_repair and "
    "review again, then forge_open in Aseprite. out copies a candidate without overwriting; "
    "open_in opens it for inspection, without marking it approved. aseprite_pass (optional) "
    "finishes it with an Aseprite engine pass, e.g. {'op':'dither','algorithm':'ordered'}."),
    annotations=WORKFLOW_WRITE)
def forge_prepare(prompt: str, width: int | None = None, height: int | None = None,
                  profile: str | None = None, project: str | None = None,
                  input_png: str | None = None, checks: list[str] | None = None,
                  out: str | None = None, open_in: str | None = None,
                  aseprite_pass: dict | None = None) -> list:
    from .fixer_workflow import prepare
    log = []
    return _workflow_response(prepare(prompt, width, height, profile, project, input_png,
                                      checks, out, open_in, log.append,
                                      aseprite_pass=aseprite_pass), log)


@server.tool(description=(
    "Optional explicit repair after visual review: ONE Claude Opus call makes a targeted patch "
    "on a library item. Uses Claude subscription allowance. Default changed-pixel budget: 10% "
    "of the canvas. Rejects redraws, resizing, palette-wide recolouring, invalid operations and "
    "over-budget edits. Preserves the source and saves a new item, QA and review sheet. "
    "Review it yourself again before Aseprite/final export; no additional fixer/generation calls."),
    annotations=WORKFLOW_WRITE)
def forge_repair(library_id: str, instructions: str, max_changed_pixels: int | None = None,
                 out: str | None = None, open_in: str | None = None) -> list:
    from .fixer_workflow import repair
    log = []
    return _workflow_response(repair(library_id, instructions, max_changed_pixels, out,
                                     open_in, log.append), log)


@server.tool(description=(
    "Turn any image (AI output, photo, screenshot, upscaled sprite) into true pixel art: detects "
    "fake pixel grids, removes flat backgrounds, reduces colours or snaps to a profile palette. "
    "preset='illustration' preserves contrasting details when shrinking GPT illustrations."))
def forge_pixelize(image_path: str, width: int | None = None, height: int | None = None,
                   colors: int = 16, profile: str | None = None, remove_background: str = "auto",
                   detect_grid: bool = True, out: str | None = None,
                   preset: str = "standard") -> list:
    prof = profiles.load(profile)
    sprite, rep = cleanup(load_image(Path(image_path).expanduser().read_bytes()), CleanupOptions(
        width=width, height=height, max_colors=colors, palette=(prof or {}).get("palette"),
        remove_background=remove_background, detect_grid=detect_grid, preset=preset))
    item_id, d = library.new_dir("import-" + Path(image_path).stem)
    library.save_result(d, sprite, {"kind": "import", "prompt": image_path, "report": rep.__dict__,
                                    "size": [sprite.width, sprite.height]})
    info = {"png": str(d / "sprite.png"), "report": rep.__dict__, "qa": qa.check(sprite)}
    if out:
        dst = Path(out).expanduser()
        if dst.exists():
            raise FileExistsError(f"{dst} exists; not overwriting")
        sprite.save(dst)
        info["exported"] = str(dst)
    return [json.dumps(info, default=str), _preview(sprite)]


@server.tool(description=(
    "Open a library item in an editor: 'aseprite' builds a layered .aseprite (final on top, review "
    "steps, drafts and route drafts as hidden layers) and opens it in the running Aseprite; 'piskel' "
    "opens Forge's browser editor on it; 'both' does both."))
def forge_open(library_id: str, editor: str = "aseprite") -> str:
    from . import editors
    return json.dumps(editors.open_item(library_id, editor))


@server.tool(description=(
    "Bring an edited .aseprite document back: its visible layers are flattened, checked (QA + lint) "
    "and saved as a new library version. out: optional .png to copy to (never overwrites). "
    "polish: run Forge's automatic cleanup first (off by default: your edits are kept as drawn)."))
def forge_from_aseprite(document: str, out: str | None = None, polish: bool = False,
                        name: str | None = None) -> list:
    from . import editors
    res = editors.import_from_aseprite(Path(document), out, polish=polish, name=name)
    return [json.dumps(res, default=str), _preview(Sprite.from_image(load_image(Path(res["png"]).read_bytes())))]


@server.tool(description=(
    "Located pixel-art problems in a PNG: stray pixels, gaps in the outline, banding, soft alpha, "
    "near-duplicate colours, with a 0-100 score. Same checks as the Aseprite window's validate."))
def forge_lint(png: str) -> str:
    from PIL import Image as PILImage
    from . import lint
    return json.dumps(lint.lint_image(PILImage.open(Path(png).expanduser())))


def _save_pass(sprite: Sprite, name: str, out: str | None, save: bool, meta: dict) -> list:
    info = {"size": [sprite.width, sprite.height], "qa": qa.check(sprite)}
    if save:
        item_id, d = library.new_dir(name)
        library.save_result(d, sprite, {"kind": "aseprite-pass", "prompt": name, **meta,
                                        "size": [sprite.width, sprite.height]})
        info["library_id"] = item_id
        info["png"] = str(d / "sprite.png")
    if out:
        dst = Path(out).expanduser()
        if dst.exists():
            raise FileExistsError(f"{dst} exists; not overwriting")
        sprite.save(dst)
        info["exported"] = str(dst)
    return [json.dumps(info, default=str), _preview(sprite)]


@server.tool(description=(
    "Dither an image with Aseprite's engine onto a palette: algorithm 'ordered', 'error-diffusion' "
    "or 'threshold'. palette: list of hex colours; if omitted, use the named style `profile`'s "
    "palette (or Aseprite's own generated palette). matrix: an Aseprite dithering-matrix file. "
    "Keeps the size. Saves a new library item unless save=false."))
def forge_dither(png: str, algorithm: str = "ordered", palette: list[str] | None = None,
                 profile: str | None = None, matrix: str | None = None,
                 out: str | None = None, save: bool = True) -> list:
    from . import aseprite_ops
    s = Sprite.from_image(load_image(Path(png).expanduser().read_bytes()))
    pal = palette or list((profiles.load(profile) or {}).get("palette", {}).values()) or None
    return _save_pass(aseprite_ops.dither(s, algorithm, palette=pal, matrix=matrix),
                      "dither " + Path(png).stem, out, save, {"algorithm": algorithm})


@server.tool(description=(
    "Reduce an image to at most `colors` colours with Aseprite's quantizer. dither: optional "
    "'ordered'/'error-diffusion'/'threshold' when snapping to the reduced palette (default flat "
    "nearest-colour). Keeps the size. Saves a new library item unless save=false."))
def forge_quantize(png: str, colors: int = 16, dither: str | None = None,
                   out: str | None = None, save: bool = True) -> list:
    from . import aseprite_ops
    s = Sprite.from_image(load_image(Path(png).expanduser().read_bytes()))
    return _save_pass(aseprite_ops.quantize(s, colors, dither=dither),
                      f"quantize {colors} " + Path(png).stem, out, save, {"colors": colors, "dither": dither})


@server.tool(description=(
    "Composite `top` over `base` with an Aseprite blend mode (normal, multiply, screen, overlay, "
    "darken, lighten, color-dodge/burn, hard/soft-light, difference, exclusion, addition, subtract, "
    "divide, hue, saturation, color, luminosity) and opacity 0..1. Both must match in size. "
    "Note: a blend can create colours beyond the two inputs. Saves a new library item unless save=false."))
def forge_blend(base_png: str, top_png: str, mode: str = "multiply", opacity: float = 1.0,
                out: str | None = None, save: bool = True) -> list:
    from . import aseprite_ops
    base = Sprite.from_image(load_image(Path(base_png).expanduser().read_bytes()))
    top = Sprite.from_image(load_image(Path(top_png).expanduser().read_bytes()))
    if (base.width, base.height) != (top.width, top.height):
        raise ValueError("blend needs both images at the same size")
    return _save_pass(aseprite_ops.blend(base, top, mode, opacity),
                      f"blend {mode} " + Path(base_png).stem, out, save, {"mode": mode, "opacity": opacity})


@server.tool(description=(
    "Build an animation from an ordered list of frame PNGs (all the same size) with Aseprite: "
    "sets each frame's duration from `fps`, tags the loop (tag name + loop 'forward'/'reverse'/"
    "'pingpong'), and writes into out_dir a sprite-sheet texture + JSON atlas, an animated GIF "
    "for review, and (with save_project) an editable .aseprite. sheet_type: horizontal/vertical/"
    "packed/rows/columns. game: set to 'phaser' to also write a drop-in geometry/animation "
    "descriptor (animation.json) matching that engine's loading convention. Sheet packing: trim "
    "crops each frame's transparent border (worth ~75% of the sheet area on sprites with a "
    "margin) and the descriptor then records each frame's source offset; extrude duplicates edge "
    "pixels (use it with trim, or frames bleed); shape_padding/border_padding/inner_padding add "
    "spacing; power_of_two rounds the sheet up; merge_duplicates stores identical frames once. "
    "Returns the written paths; the sheet is shown as an image."))
def forge_animate(frames: list[str], out_dir: str, tag: str = "animation", fps: float = 10.0,
                  loop: str = "forward", sheet_type: str = "horizontal",
                  data_format: str = "json-hash", make_gif: bool = True,
                  save_project: bool = False, game: str | None = None,
                  trim: bool = False, extrude: bool = False, shape_padding: int = 0,
                  border_padding: int = 0, inner_padding: int = 0,
                  power_of_two: bool = False, merge_duplicates: bool = False) -> list:
    from . import animation
    sprites = [Sprite.from_image(load_image(Path(p).expanduser().read_bytes())) for p in frames]
    res = animation.animate(sprites, out_dir, tag=tag, fps=fps, loop=loop, sheet_type=sheet_type,
                            data_format=data_format, game=game,
                            trim=trim, extrude=extrude, shape_padding=shape_padding,
                            border_padding=border_padding, inner_padding=inner_padding,
                            power_of_two=power_of_two, merge_duplicates=merge_duplicates,
                            gif_name="animation.gif" if make_gif else None,
                            project_name="animation.aseprite" if save_project else None)
    return [json.dumps(res, default=str),
            Image(data=Path(res["sheet"]).read_bytes(), format="png")]


@server.tool(description=(
    "Write a normal map for 2D lighting from a sprite's PNG (alpha becomes height, so flat art is "
    "flat). Set rim>0 to bevel solid art into a dome, which is what makes it read as volumetric. "
    "blur smooths the height first; luma takes height from luminance instead of alpha; "
    "transparent keeps non-sprite pixels transparent; invert_y flips the green channel for a "
    "consumer that needs it. Emission follows the standard engine convention (flat is "
    "RGB(128,128,255), blue >= 128), i.e. what Godot's hint_normal decodes. Returns the path of "
    "the normal map and shows it."))
def forge_normalmap(sprite: str, out: str | None = None, blur: float = 0.0, rim: float = 0.0,
                    contour: float = 0.5, invert_y: bool = False, luma: bool = False,
                    transparent: bool = False) -> list:
    from . import normalmap
    src = Path(sprite).expanduser()
    dst = Path(out).expanduser() if out else src.with_name(src.stem + "_n.png")
    normalmap.save_normal_map(src, dst, height_source="luma" if luma else "alpha", blur=blur,
                              invert_y=invert_y, transparent_background=transparent,
                              rim=rim, contour=contour)
    return [json.dumps({"normal": str(dst), "source": str(src)}), _image_of(dst)]


@server.tool(description=(
    "Render a sprite lit by one 2D light, using the sprite's normal map (forge_normalmap). "
    "light is the normalized position (x right, y down); brightness is 0-100 like "
    "SpriteIlluminator; colour/ambient alpha is intensity. With engine='godot' it also writes the "
    "canvas-item shader and a JSON of the uniform values to drive it at runtime. Returns the "
    "written paths and shows the lit sprite."))
def forge_lit_preview(sprite: str, normal: str | None = None, light: list[float] | None = None,
                      height: float = lighting.DEFAULT_HEIGHT, color: str = "#ffffff",
                      brightness: float = lighting.DEFAULT_BRIGHTNESS,
                      ambient: str = "#80808080", engine: str | None = None,
                      out: str | None = None) -> list:
    src = Path(sprite).expanduser()
    nrm = Path(normal).expanduser() if normal else src.with_name(src.stem + "_n.png")
    if not nrm.exists():
        raise FileNotFoundError(f"no normal map at {nrm}; call forge_normalmap first")
    dst = Path(out).expanduser() if out else src.with_name(src.stem + "_lit.png")
    res = lighting.lit_sprite(src, nrm, dst, light=tuple(light or (0.5, 0.5)), height=height,
                              color=color, brightness=brightness, ambient=ambient, engine=engine)
    return [json.dumps(res, default=str), _image_of(res["lit"])]


@server.tool(description="Register an existing folder of pixel art (read-only) so its style and assets can be used.")
def forge_add_project(path: str, name: str | None = None) -> str:
    return json.dumps(projects.register(path, name))


@server.tool(description="Analyse a registered project's art and create/refresh a matching style profile.")
def forge_learn_style(project: str, include: str | None = None) -> str:
    return json.dumps(projects.learn_style(project, include=include))


@server.tool(description="List registered projects, style profiles and recent library items.")
def forge_status() -> str:
    from . import aseprite, routes
    return json.dumps({"artists": artist.available_backends(),
                       "routes": {n: bool(routes.api_key(v)) for n, v in routes.KEYS.items()},
                       "aseprite": aseprite.find_executable(),
                       "projects": projects.list_projects(),
                       "profiles": profiles.list_profiles(), "recent": library.list_items(10)})


@server.tool(description="List PNG assets in a registered project (optionally filtered by a path substring).")
def forge_project_assets(project: str, contains: str | None = None, limit: int = 100) -> str:
    files = projects.scan(project)
    if contains:
        files = [f for f in files if contains.lower() in f["path"].lower()]
    root = projects.get(project)["path"]
    return json.dumps({"root": root, "count": len(files), "files": files[:limit]})


def main():
    server.run("stdio")


if __name__ == "__main__":
    main()
