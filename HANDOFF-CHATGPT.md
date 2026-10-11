# Handoff: using Pixel Forge (for ChatGPT / Codex)

Written 2026-10-01 by Claude Code for Mark. Read this once; `AGENTS.md` (same
folder) is the day-to-day manual and Codex loads it automatically.

Current preferred workflow: `forge.sh prepare` makes a GPT pixel-art reference,
runs the free RD neural fixer, performs conservative cleanup/QA and returns a
pending Dot visual-review sheet. Optional `forge.sh repair` makes one Claude Opus
patch in a new item; review again, then open layered Aseprite and import edits for
final QA. See `FIXER-WORKFLOW.md`. The existing `make` pipeline described below
remains available. Provider tools are on local full MCP only; the restricted Dot
authoring tunnel has not been expanded.

## What Pixel Forge is

A local pixel-art generator at `~/code/pixel-forge` (Python, MIT-style own code;
Piskel editor fork in `editor/`, Apache-2.0). It makes art for Mark's game DEAD
MALL, a neon 1990s-mall roguelite (Phaser + TypeScript, repo `~/code/90s`, GitHub
`MarkJRogers92/90s`).

One `make` run:

1. learns the game's style (palette, outline, sizes) from its existing art;
2. gets several drafts: Claude writes small drawing programs that a deterministic
   renderer turns into exact pixels; optionally GPT paints a detailed image that
   is pixelized and redrawn; optionally PixelLab and Retro Diffusion paint drafts;
3. a judge scores every draft at 1x/2x on a dark floor (identity, readability,
   style, structure) and names each one's best feature;
4. **combine**: the artist brings those features into the winner;
5. review rounds; a final judging keeps the best version (a worse later version
   never wins, so combining or reviewing can't lower quality);
6. automatic cleanup (strays, speckle, outline) and checks (QA + lint score);
7. saved to `library/<id>/` with every draft and note; optionally opened in
   Aseprite (layered) or the Piskel editor, and copied to `--out`.

## Who does what on this Mac

| Tool | Role in Pixel Forge | Login / key |
|---|---|---|
| `claude` CLI | default artist and judge | Claude subscription (`claude` → `/login`) |
| `codex` CLI | GPT painting (`--artist gpt-image`) and the GPT artist (`--artist gpt`) | ChatGPT login; model from `~/.codex/config.toml` (`gpt-6.1-sol`); CLI must be current (`codex update`, 0.160.0+) |
| PixelLab API | draft route (`--route pixellab`) | `PIXELLAB_API_KEY` in `~/.zshrc`; Tier 1 subscription, ~500 generations left until Oct 20 |
| Retro Diffusion API | draft route (`--route rd`, `rd_fast__game_asset`) | `RETRODIFFUSION_API_KEY` in `~/.zshrc`; ~$0.02 a draft, balance a few dollars |
| Aseprite 1.3.18 | layered documents, refining by hand or by agent | `/Applications/Aseprite.app` |
| Aseprite AI Artist (MCP + extension) | live pixel editing in the open Aseprite window | installed for Claude Code; optional for Codex (below) |

## One-time setup for Codex

1. Work in the folder so `AGENTS.md` loads: `cd ~/code/pixel-forge && codex`.
2. Give the session network and process access (Forge calls other CLIs and APIs).
   A read-only or no-network sandbox makes every run fail.
3. Optional, to call Forge as tools instead of shell commands:
   ```bash
   codex mcp add pixel-forge -- ~/code/pixel-forge/forge.sh mcp
   ```
4. Optional, for live editing in Aseprite (Mark runs it; it edits Codex's config):
   ```bash
   npx @pebbly/aseprite-ai-artist install codex
   ```
   Then restart Aseprite so its extension connects.
5. Check: `./forge.sh make "test crate" -W 16 -H 16 -n 1 --rounds 0 --no-combine`
   should finish in under a minute (35 s when tested) and print a library id.

## Current state (2026-10-01)

- **Code (as of 2026-10-01)**: local git only (no remote), on `main`. Latest
  commits: the merged generator (routes, Aseprite, lint), the Codex model
  fallback, combine, and the combine fix that keeps judge-named features
  consistent with the checks.
  Tests: `.venv/bin/python -m pytest -q tests` → 46 passed.
- **Publication update (2026-10-11)**: Mark explicitly authorized public GitHub
  publication of `MarkJRogers92/pixel-forge` and its custom `editor` submodule
  at `MarkJRogers92/pixel-forge-editor`. The earlier no-remote status above is
  historical; publish only to these approved targets.
- **Style profile**: `profiles/dead-mall.json` (32-colour palette, ~32x32 sprites,
  selective outlines, 4 reference sprites), learned from the game's
  `public/assets/neon` (project `dead-mall` in `projects.json`, now pointing at
  `~/code/90s/public/assets/neon`, 333 assets).
- **Library**: 52 items in `library/` (git-ignored), newest first; each has
  `sprite.png`, `sprite@8x.png`, `meta.json`, `work/` (all drafts, judge replies),
  and `sprite.aseprite` when opened in Aseprite.
- **Finals** in `~/Desktop/art/finals/` (each with `@8x`):
  - `clothes-rack-toppled.png` (+ `.aseprite`) — Mark: "i love it". A knocked-over
    clothes rack, 120x70, made by merging a hand-drawn Aseprite version with the
    pipeline's spilled clothes. Library `20261001-162250-toppled-clothes-rack-merged-final-ad8a`.
  - `arcade-cabinet-A-v9`, `arcade-cabinet-B-v3`, `arcade-cabinet-combined` — earlier
    arcade cabinets.
- **Recent runs worth looking at**: cola machine `20261001-151732-...`; claw machine
  all-routes run `20261001-153406-...`; claw machine with combine
  `20261001-154021-...` (the combined version won 14 to 11).

## Waiting to be implemented in the game

Approved art not yet wired into DEAD MALL (do it only when Mark asks):

- **Toppled clothes rack** → replaces the rotated standing sprite used for a fallen
  rack in `~/code/90s/src/game/view/HeroPropView.ts` (asset in
  `public/assets/neon/props/`, registered in `PROP_TEXTURES` in
  `src/game/presentation/rooms/roomDressing.ts`, listed in
  `public/assets/neon/manifest.json`). The fallen prop wall is 96x18, so centre
  the larger sprite over it and check the fall direction in game.
- **Arcade cabinets**: ask Mark which version first.

The game repo has its own rules (`~/code/90s/AGENTS.md`): failing test first,
gameplay only in `src/sim`, update STATUS/TEST_EVIDENCE/NEXT_SESSION, and never
push or merge without his say-so.

## Lessons from this week (so you don't repeat them)

- **Readability first.** Mark's main complaint was "it's hard to see what it is".
  Fewer, bigger shapes; one unmistakable cue; check at 1x/2x on a dark floor.
- **Fallen/broken states** need a physical cue: tilt, wheels up, spilled contents.
  PixelLab drew a "toppled" rack standing up three times; square-on symmetric
  layouts read as furniture on display.
- **Combine can misfire** if the judge praises a feature that contradicts the
  request (a neat row of hanging clothes on a knocked-over rack). Fixed: features
  must serve the checks. The final keep-best judging is the safety net; trust it.
- **Codex model errors** ("not supported when using Codex with a ChatGPT
  account") meant an outdated Codex CLI, not a bad model: `codex update`.
- **Lint**: strays are errors (fix them); an outline warning from deliberate soft
  floor shadows is fine. The same checks run in Aseprite's `validate`.

## File map

```
AGENTS.md                 the manual (Codex reads it automatically)
HANDOFF-CHATGPT.md        this file
README.md / WORKFLOW.md / TOOLS.md   overview, the asset workflow, tool decisions
forge.sh                  entry point (`./forge.sh <command>`)
forge/cli.py              commands and the `make` pipeline
forge/artist.py           drafts, judge, combine, review rounds, GPT painting
forge/routes.py           PixelLab / Retro Diffusion draft routes
forge/aseprite.py         layered .aseprite, flatten back, open in Aseprite
forge/editors.py          open in Aseprite/Piskel; import an edited document
forge/lint.py             located problems + score (ported from Aseprite AI Artist)
forge/qa.py, polish.py, cleanup.py, dsl.py, sprite.py   checks, cleanup, pixelize, renderer, sprite model
forge/mcp_server.py       MCP tools
forge/artist_guide.md     the drawing language and craft guide the artist reads
profiles/                 style profiles (+ refs/)
projects.json             registered game art folders (dead-mall)
library/                  every result (git-ignored)
editor/                   Piskel fork with the AI panel (own git repo; `npm run build`)
tests/                    pytest suite
```
