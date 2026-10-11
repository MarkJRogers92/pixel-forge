# Pixel Forge — agent instructions (Codex / ChatGPT, Claude Code)

Pixel Forge is Mark's local pixel-art generator for his game DEAD MALL (a neon
1990s mall roguelite, repo `~/code/90s`). It makes real, editable pixel art:
several AI drafts compete, a judge scores them, the best is reviewed, combined
with the best features of the others, cleaned up, checked, and saved.

Read `HANDOFF-CHATGPT.md` once for the full picture; this file is the manual.

## Run it

Preferred image workflow: `./forge.sh prepare "<brief>" --project dead-mall -W <w> -H <h>`.
GPT reference → free RD neural fixer → conservative cleanup/QA → Dot visual review
at 1x/2x → optional `forge.sh repair <id> "<focused fix>"` (one Claude Opus patch)
→ Aseprite → import final edits and QA. See `FIXER-WORKFLOW.md`.
`prepare` returns `awaiting_visual_review`; inspect its review sheet before calling
the sprite approved. It does not call Claude. The original `make` pipeline below
remains available for competing drafts.

```bash
cd ~/code/pixel-forge
./forge.sh make "<what to draw>" --project dead-mall -W <w> -H <h> -n 3 --rounds 2 \
    --check "<visible at 1x>" --check "clean dark outline, no speckle" \
    [--artist gpt-image] [--route pixellab] [--route rd] [--open aseprite|piskel|both] \
    --out ~/Desktop/art/<asset>/<name>.png
```

It prints JSON (library id, png path, QA) on stdout and its progress on
stderr. Takes 4-12 minutes: run it in the background and read the log.

| Option | Meaning |
|---|---|
| `--project dead-mall` | match the game's style (palette, outline, sizes); always use it for DEAD MALL |
| `-W/-H` | sprite size in pixels; props are usually 32x48, wide props up to 120x70 |
| `-n 3` | drafts from the artist (Claude by default) |
| `--rounds 2` | review rounds on the winner |
| `--check "..."` | must-satisfy requirements the reviews and the judge verify (3-6, things visible at 1x) |
| `--artist gpt-image` | GPT paints a detailed image, it is pixelized, Claude redraws it (richest detail, slowest) |
| `--route pixellab` / `--route rd` | extra drafts from PixelLab / Retro Diffusion that compete in the judging (spend credits: PixelLab one generation, RD about $0.02) |
| `--no-combine` | skip combine (by default the judge names each draft's best feature and the artist merges them into the winner; the merge must still win the final judging) |
| `--start in.png` / `--ref ref.png` | edit an existing sprite / show the artist a reference |
| `--open aseprite` | open the result in Aseprite as a layered document (final on top, every draft, route draft, review and the combined version as hidden layers) |
| `--out path.png` | copy the result there; never overwrites (pick a new name) |

Other commands:

```bash
./forge.sh lint sprite.png                         # located problems + 0-100 score (100 = clean)
./forge.sh qa sprite.png                           # measurements + lint
./forge.sh open <library-id> --editor aseprite     # or piskel / both
./forge.sh from-aseprite <file.aseprite> -o out.png --name "..."   # edited art back, checked, new library version
./forge.sh cleanup any_image.png -W 32 -H 32 --colors 16           # any image -> true pixel art
./forge.sh project list | project learn dead-mall  # registered games / refresh a style profile
./forge.sh serve                                   # browser editor (Piskel + AI panel) on :9002
```

MCP (same abilities as tools): `forge_make` (`routes`, `open_in`, `combine`),
`forge_open`, `forge_from_aseprite`, `forge_lint`, `forge_render` (+ `forge_guide`
to draw directly), `forge_pixelize`, `forge_status`, `forge_project_assets`.

## Workflow for a new asset

Use the preferred GPT/fixer workflow in `FIXER-WORKFLOW.md` by default. The
competition workflow below is available when an asset needs alternative drafts.

1. **Checklist**: turn the request into 3-6 `--check`s visible at 1x. No text or
   letters in art unless asked (the game renders signs).
2. **Generate**: one run with routes for variety (`--route rd --route pixellab`),
   optionally a second with `--artist gpt-image`. Use `--project dead-mall`.
3. **Compare at game size**: put every candidate side by side at 1x and 2x on a
   dark mall-floor background (the library item's `work/` folder has every draft:
   `draft_N.png`, `route_*.png`, `combined.png`, `step_N.png`). Judge by the 1x read.
4. **Fix, never repaint**: small changes are pixel patches on the existing sprite.
   For hard subjects, edit in Aseprite (layers hold every version to copy from).
5. **Check**: `./forge.sh lint` — fix every error (strays) before calling it done.
   An outline warning caused by deliberate soft floor shadows is fine.
6. **Finals**: copy to `~/Desktop/art/finals/<name>.png` plus an `@8x` preview
   (nearest-neighbour). Everything also stays in `library/`.

## Art direction (Mark's taste)

- Bold neon accents and distinctive shapes, but clean and readable at 1x.
- No speckle or noisy painted texture; narrow accents on mid/dark ramp steps.
- Edits never lose existing detail.
- Readability beats detail: if it is hard to tell what it is at game size, simplify
  (fewer, bigger shapes) and add the one cue that says what it is.
- A "knocked over / broken" state needs a physical cue (tilt, wheels in the air,
  spilled contents). Square-on symmetric layouts read as standing furniture.

## Rules

- **Never write into the game repo** (`~/code/90s`) unless Mark asks to implement
  art. Game art is read-only input for style.
- Never overwrite files in `~/Desktop/art/` (use a new name).
- Ask before spending credits on big batches (each route call costs).
- Don't edit `editor/` (Piskel fork, its own git repo) unless the task is the editor.
- Tests: `.venv/bin/python -m pytest -q tests` (46 tests; one does a real Aseprite
  round trip and is skipped if Aseprite is missing). Run them after code changes.
- Commit Pixel Forge changes locally. Never push or merge unless Mark explicitly
  authorizes that action; authorization applies only to the targets he names.

## Environment and pitfalls

- Needs network and the ability to start processes: Forge calls the `claude` CLI
  (artist and judge), `codex exec` (GPT painting), PixelLab/Retro Diffusion APIs,
  and Aseprite. In a Codex sandbox, run with full access or approve those; a
  read-only/no-network sandbox makes every run fail.
- API keys come from the environment or `~/.zshrc` (Forge falls back to `zsh -ic`):
  `PIXELLAB_API_KEY`, `RETRODIFFUSION_API_KEY`. Never print or copy them.
- GPT painting uses Codex's configured model (`gpt-6.1-sol`). If Codex refuses it
  ("not supported when using Codex with a ChatGPT account"), the CLI is out of date:
  `codex update`. Forge retries once with `gpt-5.5` (or `FORGE_CODEX_MODEL`) so a
  run still finishes, and logs the model each painting used.
- `make` saves to `library/<id>/` before anything can fail; read that folder's
  `work/` and `meta.json` to see what happened.
- Aseprite is `/Applications/Aseprite.app` (override with `ASEPRITE_PATH`). Forge
  drives it in batch mode; it does not need the Aseprite AI Artist extension.
