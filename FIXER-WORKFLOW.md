# GPT → neural fixer → Dot → Aseprite

The preferred workflow makes one pixel-art-style GPT reference, runs RD's neural
fixer at the intended sprite dimensions, performs conservative Forge cleanup/QA,
and hands the actual sprite to Dot for visual review at 1x/2x. Claude Opus is an
optional targeted repair. Aseprite is the final editable canvas.

## Prepare a candidate

```bash
./forge.sh prepare "1990s pink arcade claw machine" --project dead-mall -W 64 -H 96 \
  --check "cabinet and claw identifiable at 1x" \
  --check "toy shapes visible through glass" --check "no lettering"
# Existing reference: skips GPT generation.
./forge.sh prepare "1990s pink arcade claw machine" --project dead-mall -W 64 -H 96 \
  --input /absolute/path/reference.png
```

`prepare` uses the existing Codex image-generation login and RD key integration.
It checks RD authentication before starting GPT. It requests one image and requires
the generating run to report its exact output file; it never guesses from a global
newest-image timestamp. A failed call stops without a retry or paid RD fallback.
Call counters record attempts, including failed calls.

The neural fixer does not deduct RD account balance. GPT generation uses the
GPT/Codex allowance. The fixer API is authenticated and rate limited to 10 calls
per minute. Official contract:
https://github.com/Retro-Diffusion/api-examples/blob/main/PIXEL_FIXER.md

Uploads use PNG to retain alpha. Oversized inputs are progressively downscaled
to fit the 900,000-byte JSON limit; both original and actual upload are kept.
Output must be one PNG at exactly the requested dimensions (1–512 per side).
Cleanup keeps the established grid, caps the palette at 32 colours, produces binary
alpha, and avoids global polishing and automatic orphan removal. QA identifies
remaining problems; it does not imply artistic approval.

Every new Library item contains:

- `sprite.png`, `sprite@8x.png`, `sprite.json`, `meta.json`.
- `review.png`: current sprite, fixer output and scaled reference at 1x/2x and zoom.
- `review.json`: checklist and pending Dot review instructions.
- `work/gpt_painting.png`, `fixer_input.png`, `rd_neural.png`, `cleaned.png`.

Return status is `awaiting_visual_review`. Dot must inspect the review sheet for
identity, readability, style and the checklist. Record that judgment in the chat;
Forge does not run an extra model or manufacture a successful visual-review result.
`--out` copies a review candidate with exclusive creation; `--open aseprite` opens
it for inspection while its review status remains pending.

## Optional Opus repair

```bash
./forge.sh repair <library-id> "make the claw visible; preserve cabinet and toys" \
  --max-changed-pixels 80
```

One explicit Claude CLI Opus call requests a primitive pixel patch. The default
budget is 10% of the canvas. Responses, operations and geometry are bounded;
redraws, resizing, existing-palette recolouring, invalid operations and excessive
changes are rejected. Repair requires a source with binary alpha and at most
32 colours; partial-alpha art belongs in the exact direct-authoring edit tools.
The source is preserved and successful repairs become new Library items, with
the original pixel hash and changed-pixel count. Dot reviews the new sheet again.
Opus uses the Claude subscription allowance. It is never invoked by `prepare`.

## Aseprite and final checks

```bash
./forge.sh open <chosen-library-id> --editor aseprite
./forge.sh from-aseprite /absolute/path/sprite.aseprite --name "claw machine edited"
./forge.sh lint /absolute/path/new-library-item/sprite.png
```

The document has the current sprite visible, with hidden fixer, cleanup, scaled
reference and repair layers. The large original reference stays outside the
document so it cannot enlarge the sprite canvas. Importing final edits creates a
new checked Library item. Review at 1x again, fix lint errors, and export to a new
final filename when approved. Game files are not changed by this workflow.

## Tool surfaces

The local CLI and unrestricted `forge.sh mcp` expose `forge_prepare` and
`forge_repair`; MCP returns the review sheet as an image alongside JSON.
Both tools declare non-read-only, non-idempotent, open-world behavior.
The existing `make` pipeline remains available for Claude/GPT competitions.

For narrow direct use, `forge.sh mcp-workflow --enable-authoring --enable-workflow`
adds asynchronous, deduplicated preparation/repair and Library-only Aseprite
tools to the original eight. See `WORKFLOW-MCP.md` for the exact tool list,
request sequence and separately approved persistent update.

The five-tool read-only server and eight-tool direct-authoring server remain
restricted. Dot's current tunnel runs the latter, so these new provider tools
are **not directly available through that tunnel**. Until a separately approved
connection update, use delegated Mac execution or the existing local full MCP,
and return the review sheet to Dot. No credentials, runtime, plugin permissions
or tunnel configuration are changed by installing this workflow.
