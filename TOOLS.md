# Tool decisions

Candidates came from two research handoffs (the DEAD MALL Forge packet and the
79-tool free pixel-art catalog, both 2026-09-29). Rule: a tool is adopted only when
it fixes a measured weakness of Pixel Forge on our own assets. Target machine: M2,
16 GB unified memory.

## Adopted

| Tool | License | Role | Why |
|---|---|---|---|
| Piskel (fork in `editor/`) | Apache-2.0 | Editor, layers, frames, PNG/GIF/sheet export | Browser-based, maintained (2026 commits), easy to extend with the AI panel |
| Pillow + NumPy | HPND / BSD | All pixel processing | Deterministic, exact pixels, already needed |
| MCP Python SDK 2.x | MIT | `forge mcp` tools for Claude Code / Codex | Official SDK |
| `claude` CLI | subscription | Artist + judge | Uses the logged-in Claude plan, no API key |
| `codex` CLI (+ image tool) | subscription | GPT artist, GPT painting for the hybrid | Uses the ChatGPT login; images land in `~/.codex/generated_images` |

## Ideas adopted (implemented in our code, no dependency)

| Idea | Source | Where |
|---|---|---|
| Best-of-N drafts + rubric judge (identity / readability / style / structure, 0-4) | Forge packet, quality doc | `artist.generate`, `artist.judge` |
| Keep the best version; a worse late revision never wins | both handoffs | final judging in `artist.generate` |
| Acceptance checklists per asset | Forge packet benchmark | `--check`, MCP `checks` |
| Judge at 1x / 2x on a dark game background, not only zoomed | Forge packet | `artist.contact_sheet` |
| Cleanup must never delete intended thin cords, sparks, holes | Forge packet Task 4 | `polish.py` + tests |
| Grid detection verified before use (native art is never re-gridded) | Forge packet | `cleanup.verify_grid` |

## Adopted 2026-10-01: the merged generator

| Tool | License | Role | Why |
|---|---|---|---|
| Aseprite AI Artist (with-pebbly) | MIT | Companion MCP for live edits in Aseprite; its `validate` checks ported into `lint.py` | Forge generated well but was hard to touch up; Aseprite is the stronger editor and its live look/validate loop fixed a sprite PixelLab could not draw (the toppled rack). Forge talks to Aseprite through its own batch mode, so it does not depend on the extension. |
| PixelLab (pixflux REST) | service terms | Optional draft route (`--route pixellab`) | Strongest at characters and some props; now competes in Forge's judging instead of replacing it. Costs a generation per draft. |
| Retro Diffusion (v2 REST) | service terms | Optional draft route (`--route rd`) | Good painterly game assets; `rd_fast` is asked at 2x (min 64) since it rejects tiny sizes. About $0.02 per draft. |

## Evaluated, not adopted (yet)

| Tool | License | Result |
|---|---|---|
| PixelOE 1.0.0 (legacy numpy/OpenCV path) | Apache-2.0 | Tested on the same GPT painting as our converter: slightly cleaner neon trims and kept coin lights, but lost the same small details (screen crack). The hybrid redraws from the conversion anyway, so the gain mostly disappears. Would add OpenCV (~100 MB; full package with torch is ~900 MB). Revisit if a pixelize-only workflow needs it. |
| Pixel Art Lab | MIT | Per-pixel `draw_pixels` via Aseprite + OpenRouter. Our shape/material program is a higher-level design; its restore-point and budget ideas are covered by keep-best / bounded rounds. |
| Aseprite MCP bridges (diivi, willibrandon, rkdfx, sloplink) | MIT (Aseprite EULA separate) | Superseded by Aseprite AI Artist (below). |
| Retro Diffusion pixel-art-fixer, Sprite Fusion Pixel Snapper | MIT | Our grid detection recovers 28/30 degraded test cases and rejects 139/140 native game sprites correctly. Revisit if real inputs fail. |
| Pixelorama | MIT | Stronger editor, but Godot/GDScript with no headless API: much harder to wire to the AI loop than Piskel. |
| ComfyUI lane | various | Out of scope: PixelLab and Retro Diffusion cover hosted generation (routes, below). |
| Tiled/LDtk, TilePipe, Laigter, texture packers, Blender/Blockbench | various | Not needed until tiles, lighting maps, atlases or 3D-based sprites are on the roadmap. |
| Model training (AI Toolkit, kohya_ss) | various | Deferred: needs a rights-cleared dataset. PixelLab outputs cannot be used as training data (their terms). |
