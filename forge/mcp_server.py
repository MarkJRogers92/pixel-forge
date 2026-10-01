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

from . import artist, dsl, library, profiles, projects, qa
from .cleanup import CleanupOptions, cleanup, load_image
from .sprite import Sprite, preview_image

server = MCPServer(
    "pixel-forge",
    instructions=(
        "Pixel Forge creates real pixel art. Use forge_make for a fully automatic sprite from a "
        "description (optionally in the style of a registered project), or read forge_guide and "
        "draw yourself with forge_render. Use forge_pixelize to turn any image into pixel art, "
        "and forge_add_project / forge_learn_style to pull style and assets from an existing game. "
        "forge_make can also take drafts from PixelLab or Retro Diffusion (routes, spends credits) "
        "and open the result in Aseprite as a layered document (open_in). After editing in "
        "Aseprite, forge_from_aseprite brings the document back, checked, as a new version. "
        "forge_lint reports located problems with the same checks as Aseprite's validate."),
)


def _preview(s: Sprite) -> Image:
    import io
    buf = io.BytesIO()
    preview_image(s, 8 if max(s.width, s.height) <= 64 else 4).convert("RGB").save(buf, "PNG")
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
    "final judging. Takes 2-6 minutes."))
def forge_make(prompt: str, width: int | None = None, height: int | None = None,
               profile: str | None = None, project: str | None = None, rounds: int = 2,
               artist_name: str | None = None, model: str | None = None,
               start_png: str | None = None, out: str | None = None, candidates: int = 3,
               checks: list[str] | None = None, routes: list[str] | None = None,
               open_in: str | None = None, combine: bool = True) -> list:
    from .cli import make
    log: list[str] = []
    result = make(prompt, width, height, profile, project, rounds, artist_name, model,
                  start_png, out, log=log.append, candidates=candidates, checks=checks,
                  routes=routes, open_in=open_in, combine=combine)
    result["log"] = log
    return [json.dumps(result, default=str),
            _preview(Sprite.from_image(load_image(Path(result["png"]).read_bytes())))]


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
