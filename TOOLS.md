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

## Evaluated, not adopted (yet)

| Tool | License | Result |
|---|---|---|
| PixelOE 1.0.0 (legacy numpy/OpenCV path) | Apache-2.0 | Tested on the same GPT painting as our converter: slightly cleaner neon trims and kept coin lights, but lost the same small details (screen crack). The hybrid redraws from the conversion anyway, so the gain mostly disappears. Would add OpenCV (~100 MB; full package with torch is ~900 MB). Revisit if a pixelize-only workflow needs it. |
| Pixel Art Lab | MIT | Per-pixel `draw_pixels` via Aseprite + OpenRouter. Our shape/material program is a higher-level design; its restore-point and budget ideas are covered by keep-best / bounded rounds. |
| Aseprite MCP bridges (diivi, willibrandon, rkdfx, sloplink) | MIT (Aseprite EULA separate) | Not needed: the AI panel lives in our own editor. Aseprite is installed and can still be used for `.aseprite` export later. |
| Retro Diffusion pixel-art-fixer, Sprite Fusion Pixel Snapper | MIT | Our grid detection recovers 28/30 degraded test cases and rejects 139/140 native game sprites correctly. Revisit if real inputs fail. |
| Pixelorama | MIT | Stronger editor, but Godot/GDScript with no headless API: much harder to wire to the AI loop than Piskel. |
| PixelLab, Retro Diffusion hosted, ComfyUI lane | service terms | Out of scope by choice: Pixel Forge's artist is Claude/GPT. |
| Tiled/LDtk, TilePipe, Laigter, texture packers, Blender/Blockbench | various | Not needed until tiles, lighting maps, atlases or 3D-based sprites are on the roadmap. |
| Model training (AI Toolkit, kohya_ss) | various | Deferred: needs a rights-cleared dataset. PixelLab outputs cannot be used as training data (their terms). |
