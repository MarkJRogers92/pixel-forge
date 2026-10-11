# Pixel Forge

A local pixel-art tool for DEAD MALL. The preferred workflow is **GPT pixel-art
reference → RD neural fixer → conservative cleanup/QA → Dot visual review →
Aseprite**. Claude Opus can make a focused repair when needed. The existing
automatic generator and direct pixel drawing tools remain available.

Built on [Piskel](https://github.com/piskelapp/piskel) (Apache-2.0) for the editor.

## Start

```bash
./forge.sh serve          # http://127.0.0.1:9002, then click the "AI" button on the right
```

## Fresh clone setup

Clone the editor submodule with the main repository, then install the local
Python and browser-editor dependencies:

```bash
git clone --recurse-submodules https://github.com/MarkJRogers92/pixel-forge.git
cd pixel-forge
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
# Node.js 22.12 or newer
(cd editor && PUPPETEER_SKIP_DOWNLOAD=true npm ci && npm run build)
./forge.sh project add ./profiles/refs/dead-mall --id dead-mall --name "DEAD MALL"
./forge.sh serve
```

The project registration is local and ignored by Git. It lets browser Create
use the included DEAD MALL profile and reference sprites; if the game assets are
available, register their `public/assets/neon` folder instead. Browser Create
uses your locally signed-in Codex CLI and `RETRODIFFUSION_API_KEY` for the GPT
reference and neural-fixer steps. Configure credentials on your machine; never
put them in this repository. Claude, PixelLab, and Aseprite are optional for
their respective workflows. `projects.json`, generated library art, and local
credentials are not included in Git.

### The AI panel
| Tab | What it does |
|---|---|
| **Create** | Defaults to GPT reference → free RD neural fixer → QA and visual review. Stage previews stay crisp at pixel scale; refresh reattaches to the same request. Cancel is available while work runs. Your current canvas stays untouched during generation; choose **Open final as new sprite** to load the result when ready. Switch to **Competing drafts** for the older competition workflow. |
| **Refine** | "Make the cord more frayed": the artist edits the current frame; the result is added as a new layer, so your original stays. |
| **Pixelize** | Turn any image (AI output, photo, screenshot, blurry upscale) into true pixel art: detects fake pixel grids, removes flat backgrounds, reduces colours or snaps to a style palette. |
| **Assets** | Browse an existing project's sprites (read-only) and open copies to edit or refine. **Learn style** builds a style profile (palette, sizes, outline habit, reference sprites) from that project. |

## Preferred image workflow

```bash
./forge.sh prepare "cream 1994 mall payphone with a frayed cord" --project dead-mall -W 32 -H 48 \
  --check "frayed cord visible at 1x" --check "clean dark outline, no lettering"
```

This generates one GPT reference, reconstructs it with the free RD neural fixer,
then saves the original, fixer output, cleaned sprite, QA and a review sheet.
It returns `awaiting_visual_review`: Dot inspects the sheet at 1x/2x before
requesting a repair or opening Aseprite. Claude is not called by `prepare`.
Use `--input reference.png` to reuse an image and skip GPT generation.
See [FIXER-WORKFLOW.md](FIXER-WORKFLOW.md) for repair, Aseprite and connection details.
The AI panel's Create tab uses this same workflow by default. Cancelling stops
local work when possible, but cannot refund provider credits already spent.
The older competing-drafts mode remains available. For an optional guided
PixelLab style route and credential-free dry-run trial, see
[PIXELLAB-STYLE.md](PIXELLAB-STYLE.md).

## Existing automatic generator (for you, Claude Code, or Codex)

```bash
./forge.sh make "cream 1994 mall payphone with a frayed cord" --project dead-mall -W 32 -H 48 --out ~/Desktop/art
./forge.sh make "rusty shopping cart" --artist gpt --rounds 2
./forge.sh cleanup some_ai_image.png -W 32 -H 32 --colors 16
./forge.sh cleanup gpt_image.png -W 64 -H 64 --colors 32 --preset illustration --out sprite.png
./forge.sh project add ~/code/my-game/assets --name "My game" && ./forge.sh project learn my-game
```

`make` = learn style (if needed) → N drafts in parallel → a judge scores them (0-4 on
identity, readability at 1x, style, structure) → review rounds on the winner → the best
version is kept (a worse late round never wins) → automatic cleanup → save to `library/` →
optional copy to `--out` (never overwrites).

Useful options:
- `-n 3` three drafts, judge picks the best (default 1 in the CLI, 3 in the chat tools)
- `--check "screen crack visible at 1x"` requirements the reviews and judge must verify
- `--artist gpt-image` GPT paints a detailed base, it is converted to pixels, Claude redraws it clean
- `--rounds 0` fastest draft only; `--no-polish` skip cleanup

For an existing GPT illustration, choose **GPT image / illustration** in the Pixelize
tab, or pass `--preset illustration` to `cleanup` (`preset="illustration"` in
`forge_pixelize`). It preserves contrasting highlights during downsampling, then
uses the existing polish pass to reduce painted speckle and organize the outline.
Native pixels and recovered upscales are left intact when no shrinking is needed.
Standard keeps the previous conversion behavior. Both produce native pixels, limited
colors and binary transparency. Review at 1x: a detailed painting can still need a
pixel redraw, especially below 64 pixels. Background removal handles existing alpha
or a flat border color; the report warns when it leaves the source opaque.

### Routes and editors (the merged generator, 2026-10-01)
Pixel Forge now pulls in the other pixel-art tools instead of replacing them:

```bash
# Claude drafts + a Retro Diffusion draft + a PixelLab draft, judged together; open in Aseprite
./forge.sh make "red 1990s cola machine" --project dead-mall -W 32 -H 48 \
    --route rd --route pixellab --open aseprite
./forge.sh open <library-id> --editor both        # layered .aseprite and/or the Piskel editor
./forge.sh from-aseprite ~/code/pixel-forge/library/<id>/sprite.aseprite -o ~/Desktop/art/final.png
./forge.sh lint sprite.png                         # located problems + a 0-100 score
```

- **Routes** (`--route pixellab|rd`): PixelLab (pixflux) and Retro Diffusion
  (`rd_fast__game_asset`) each paint a draft, it is converted to true pixel art at the
  target size, and it competes with the artist's drafts in the judging. A winning route
  draft is then reviewed and polished like any other. Keys: `PIXELLAB_API_KEY`,
  `RETRODIFFUSION_API_KEY` (environment or `~/.zshrc`). Each route call spends credits.
- **Combine** (on by default; `--no-combine` to skip): the judge also names the best
  feature of every draft ("the glowing marquee", "plush readable through the glass"), and
  the artist brings those into the winner as one coherent sprite. The combination is
  reviewed like any version and must win the final judging, so it can never lower quality.
- **Aseprite** (`--open aseprite`, `forge open`): a layered `sprite.aseprite` in the
  library item (final on top, then review steps, drafts and route drafts as hidden
  layers) opens in your Aseprite window. With the Aseprite AI Artist extension attached
  an agent can keep editing it live. `from-aseprite` flattens your visible layers back
  into a new, checked library version.
- **Piskel** (`--open piskel`): the browser editor opens with the result loaded
  (`http://127.0.0.1:9002/?open=<id>`); the server starts if it is not running.
- **Lint** (`forge lint`, `forge_lint`, and in every QA report): stray pixels, gaps in
  the outline, banding, soft alpha and near-duplicate colours, ported from Aseprite AI
  Artist's `validate` so both canvases give the same verdict.

### Automatic cleanup (every result)
Removes stray pixels and speckle, fills dents in the outline and draws one consistent dark
outer outline, while keeping deliberate details: glints, rows of lights, multi-shade lights,
1-px cords and wires, detached sparks and enclosed holes (all covered by tests).

### MCP server (Claude Code / Codex call it as tools)
```bash
claude mcp add pixel-forge -s user -- ~/code/pixel-forge/forge.sh mcp
codex mcp add pixel-forge -- ~/code/pixel-forge/forge.sh mcp
```
Tools: `forge_prepare` (preferred; returns the visual review sheet), `forge_repair`
(one optional Claude Opus patch), `forge_make` (automatic; `routes`, `open_in`),
`forge_guide` + `forge_render` (the
calling agent draws itself and sees the image), `forge_pixelize`, `forge_open`,
`forge_from_aseprite`, `forge_lint`, `forge_add_project`, `forge_learn_style`,
`forge_project_assets`, `forge_status` (also reports route keys and the Aseprite path).

For a connection that can only inspect existing art, use `./forge.sh mcp-readonly`.
It exposes five tools: `forge_status`, `forge_projects`, `forge_project_assets`,
`forge_library`, and `forge_preview`. It cannot generate, edit, export, register
projects, launch editors, or read route credentials. Project reads are limited to
the registered `dead-mall` art folder; library previews show the final `sprite.png`.
See [READONLY-MCP.md](READONLY-MCP.md) for setup and the private tunnel approval boundary.

For bounded direct drawing without model calls, `./forge.sh mcp-authoring` adds
capabilities and two disabled write tools. Local authoring requires the explicit
`--enable-authoring` launch flag. Each render or edit creates a new library item;
source art is preserved. See [DIRECT-AUTHORING.md](DIRECT-AUTHORING.md) for the
strict JSON contract, fixture and the separate approval before remote write access.

For the narrow asynchronous image workflow plus direct drawing, use
`./forge.sh mcp-workflow --enable-authoring --enable-workflow`. It preserves
the eight direct tools and adds preparation, optional Opus repair, progress,
and Aseprite open/import. See [WORKFLOW-MCP.md](WORKFLOW-MCP.md) before updating
a persistent connection.

## Artists
| id | how | needs |
|---|---|---|
| `claude` / `cli` | `claude -p` (your Claude subscription) | `claude` logged in (`claude` → `/login`) |
| `gpt` / `codex` | `codex exec` (your ChatGPT login) | `codex login` |
| `api` | Anthropic API | `ANTHROPIC_API_KEY` |
| `gpt-image` | GPT paints (codex image tool) → pixelize → Claude redraws | both CLIs logged in |

## Layout
```
forge/sprite.py        indexed sprite document (palette + rows, "." = transparent)
forge/dsl.py           sprite-program renderer (rect, ellipse, poly, pixels, shade, outline, dither…)
forge/artist_guide.md  the pixel-art craft guide + language reference the artist reads
forge/artist.py        draft → render → look → fix loop; claude / codex / API backends
forge/cleanup.py       image → pixel art (grid detection + verification, background, palette)
forge/polish.py        automatic cleanup (noise, speckle, outline) that keeps intended details
forge/qa.py            measurable checks (colours, stray pixels, outline, cropping, fragments)
forge/lint.py          located problems + score (ported from Aseprite AI Artist's validate)
forge/routes.py        PixelLab and Retro Diffusion draft routes
forge/rd_fixer.py      free neural reconstruction with bounded, alpha-preserving uploads
forge/fixer_workflow.py GPT reference → fixer → QA/review handoff; optional targeted repair
forge/aseprite.py      layered .aseprite documents, flatten back, open in Aseprite (batch mode)
forge/editors.py       open a result in Aseprite and/or Piskel; import an edited document
forge/projects.py      pull art + learn style from existing project folders (read-only)
forge/server.py        local HTTP server for the editor panel
forge/mcp_server.py    MCP tools
editor/                Piskel fork; AI panel = src/js/controller/settings/ForgeController.js
profiles/              style profiles (+ refs/ reference sprites)
library/               every result with its programs, review notes and QA (git-ignored)
```

## Tests
```bash
.venv/bin/python -m pytest -q tests
```

## Asset workflow
The step-by-step process for new assets (checklist → GPT/fixer → Dot review →
optional repair → Aseprite → final QA) is in `WORKFLOW.md`.
In Claude Code, ask for a new asset or run `/pixel-asset` to follow it automatically.

## Tool decisions
See `TOOLS.md` for what was adopted from the research handoffs, what was tested and rejected, and why.

## Licences
Piskel is Apache-2.0 (see `editor/LICENSE`). Your project art is only read, never
modified; reference copies go to `profiles/refs/`.
