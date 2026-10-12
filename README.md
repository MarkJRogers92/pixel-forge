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
- **Aseprite-engine passes** (`forge dither|quantize|blend`, and the DSL ops
  `aseprite_dither`/`aseprite_quantize`): hand a sprite to the real Aseprite 1.3 engine
  for ordered/error-diffusion/threshold dithering onto an exact palette, colour
  quantization, and blend-mode compositing. Installed Aseprite is required; the exact
  API used was reverse-engineered and is recorded in [ASEPRITE-NOTES.md](ASEPRITE-NOTES.md).

  ```bash
  ./forge.sh quantize sprite.png --colors 12 -o reduced.png
  ./forge.sh dither sprite.png --algo error-diffusion --profile dead-mall -o dithered.png
  ./forge.sh blend base.png glow.png --mode screen --opacity 0.5 -o lit.png
  ```

  A pass can also finish a generated sprite automatically: add
  `--aseprite-pass quantize|dither` to `forge make`/`forge prepare` (with `--pass-colors`,
  `--pass-algo`, `--pass-palette`; the palette defaults to the profile's, so a DEAD MALL run
  can snap straight onto the game palette). The MCP `forge_make`/`forge_prepare` take an
  `aseprite_pass` object, e.g. `{"op": "quantize", "colors": 16}`.
- **Animation** (`forge animate`, `forge_animate`): build a real Aseprite animation from
  ordered frames — per-frame durations from `--fps`, a tag with a loop direction
  (`forward`/`reverse`/`pingpong`) — and export a **game-ready sprite sheet** (PNG + JSON
  atlas), an animated **GIF for review**, and an editable `.aseprite`. Frames can be PNG
  paths, `--library` item ids (e.g. the sprites of 2–3 generation runs), or `--steps` of a
  run. `--game phaser` also writes a drop-in descriptor (`animation.json`) matching the
  game's convention: a **horizontal strip** whose frames the code numbers `'0'..'n-1'`
  (`ensureFrames(key, frameWidth, frameHeight, count)`), plus the exact load/add/animate
  snippets.

  **Sheet packing** (off by default; measured on Aseprite 1.3.18.6): `--trim` crops each  frame's transparent border and records where it was — on a 16-sprite corpus with
  transparent margins the sheet came out **75% smaller** (256x288 → 167x109). `--extrude`
  duplicates each frame's edge pixels 1px outwards and should be used *with* `--trim`,
  otherwise the neighbouring frame bleeds in at the border (it costs ~6% of the sheet area
  and is still far smaller than untrimmed). `--shape-padding` / `--border-padding` /
  `--inner-padding` add spacing, `--power-of-two` rounds the sheet up, `--merge-duplicates`
  stores identical frames once. With `--trim` the atlas and the descriptor carry each
  frame's `spriteSourceSize` offset, and the Phaser `addFrames` snippet switches from the
  uniform-strip form to per-frame rects.

  Every run also reports `atlas_utilisation` — the share of the sheet that is actually
  sprite, which is TexturePacker's "texture usage" (its `pack-mode Good` targets at least
  90%). A low number means the sheet is mostly padding: try `--type packed`, reorder the
  frames, or trim.

  ```bash
  ./forge.sh animate frame0.png frame1.png frame2.png -o out/clip --tag flicker --fps 12 --loop pingpong
  ./forge.sh animate --library 20261012-sign-off --library 20261012-sign-on -o out/sign --game phaser
  ./forge.sh animate --library s1 --library s2 -o out/clip --type packed --trim --extrude --shape-padding 2
  ```

- **Normal maps + lighting** (`forge normalmap`, `forge lit`, `forge_normalmap`,
  `forge_lit_preview`): give a sprite volume under a 2D light. Height comes from the
  sprite's **alpha** (not luminance), blurred with the same rule SpriteIlluminator uses
  (`ksize = odd(max(3, round(2*radius)))`, `sigma = radius/2`, replicated borders), and
  tilted with a 3×3 Sobel at the fixed 1/8 slope scale. Output is a standard normal map
  (flat = RGB(128,128,255), blue ≥ 128) — i.e. what Godot's `hint_normal` decodes, so it
  needs no flip. `--invert-y` is there if a consumer wants the other convention.

  **Solid art needs `--rim`.** A flat opaque shape has a constant alpha, so its base pass
  is a flat map that lights nothing. `--rim 0.85` builds SpriteIlluminator's
  "Create Rim/Bevel" dome from the distance to the alpha edge, which is what makes a prop
  read as volumetric.

  ```bash
  ./forge.sh normalmap <id> --rim 0.85 --blur 1.2 -o out/hero_n.png
  ./forge.sh normalmap <id> --rim 0.85 --detail 0.45 -o out/hero_n.png   # interior relief too
  ./forge.sh lit <id> -n out/hero_n.png --light 0.3 0.2 --engine godot -o out/hero_lit.png
  ./forge.sh lit <id> -n out/hero_n.png --gif out/orbit.gif --frames 12 --radius 0.3
  ```

  `--detail` adds that share of the sprite's own luminance on top of the height. Height
  from alpha is silhouette-only, so a keypad, a handset and a panel all sit on one dome
  and read flat; `--detail` is what makes interior features tilt. It is ours, not
  SpriteIlluminator's, off by default, and dark outlines then read as grooves. `--gif`
  writes a loop of the light orbiting the sprite for review.

  `lit` renders one light over the sprite (`--light X Y` normalized, `--brightness 0-100`,
  colour/ambient alpha = intensity) using SpriteIlluminator's own equation — no specular or
  gloss term, because it has none. `--height` sets the light's distance in pixels and is the
  directionality knob: 100 is a soft, almost head-on light (measured: 4% between the lit and
  unlit flank on a 32×48 prop), 15 is strong (~48%). With `--engine godot` it also writes
  `<name>.light.json` (the uniform values) and `<name>.gdshader`, a canvas-item shader using
  the same uniforms, so a Godot scene can drop the pair straight in.

- **Godot sheets** (`forge godot-frames`, `forge_godot_frames`): turn the JSON atlas
  `animate` writes into a Godot 4 `SpriteFrames` `.tres`, one `AtlasTexture` per frame.
  Pass the sheet's path *inside the project* (`--texture res://...`) and write the `.tres`
  into that project.

  ```bash
  ./forge.sh animate --library a --library b -o out/clip --type packed --trim --game phaser
  ./forge.sh godot-frames out/clip/sheet.json --texture res://clip/sheet.png -o out/clip/frames.tres
  ```

  This is the consumer that makes `--trim` worth using: a trimmed frame gets the  trimmed-off padding in its `AtlasTexture.margin` — its position in the original frame,
  plus what was cut — because Godot draws a frame's `region` at `margin.position` inside a
  canvas of `region.size + margin.size`. Without it a trimmed sheet shifts *or resizes*
  every sprite. Verified by loading the generated file in the engine and asserting the drawn
  canvas size the engine reports, not merely that our numbers round-trip — see the
  Godot-export tests, which skip without `godot`.

- **Collision shapes** (`forge collide`, `forge_collide`): convex hulls from a sprite's
  alpha, the way PhysicsEditor does it — the outline is simplified by Douglas-Peucker with
  the tolerance in **pixels**, then decomposed into convex hulls (ear clipping + greedy
  merge, capped at `--max-vertices`, since Godot wants 3–8). Writes the engine-agnostic JSON
  and/or a Godot 4 `.tscn` (`StaticBody2D` + one `CollisionShape2D` per hull, coordinates
  relative to the sprite's centre).

  ```bash
  ./forge.sh collide <id> --tscn out/prop.tscn --tolerance 2.0
  ```

  Every run reports `accuracy` — the IoU between the shapes and the silhouette — because a
  coarse tolerance changes the shape silently. Measured on a real 32x48 prop (783 opaque
  pixels):

  | tolerance | hulls | IoU |
  |---|---|---|
  | 0.5 | 40 | 1.000 |
  | 1.0 | 15 | 0.947 |
  | 2.0 | **8** | 0.919 |
  | 3.0 | 5 | 0.839 (warns) |

  So ~2.0 px is the sweet spot for props: a handful of hulls at ~92% agreement. Area alone
  cannot see a shape change — a coarse tolerance can drop a notch off an L-shaped silhouette
  and gain equal area elsewhere, leaving the ratio at 1.0 — which is why the warning uses
  IoU instead.

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
`forge_from_aseprite`, `forge_dither` / `forge_quantize` / `forge_blend` (Aseprite
engine passes), `forge_animate` (animation → sprite sheet + GIF), `forge_lint`,
`forge_add_project`, `forge_learn_style`,
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
forge/aseprite_ops.py  Aseprite-engine passes: dither, quantize, blend (see ASEPRITE-NOTES.md)
forge/animation.py     Aseprite-backed animation: frames + tag, sprite-sheet + GIF export
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
