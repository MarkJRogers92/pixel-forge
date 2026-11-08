# Animated GIF workflow

## Use a GIF made in normal ChatGPT

Generate or edit an animation in ChatGPT, download the **actual animated GIF**,
then import it locally. No OpenAI API key or API preview access is needed for
this path. A still preview saved as a PNG/JPEG is not an animation and is rejected.
Availability of GIF creation in your ChatGPT account is separate from this importer.

```bash
# Keep exact canvas and colours (maximum 62 visible colours over the whole clip).
./forge.sh gif-import ~/Downloads/recoil.gif -o out/recoil --tag recoil

# Recommended for a larger/painted GIF: one target canvas and one game palette.
./forge.sh gif-import ~/Downloads/recoil.gif -o out/recoil-small \
  -W 64 -H 64 --profile dead-mall --tag recoil

# Also create an editable animation document and Aseprite atlas.
./forge.sh gif-import ~/Downloads/recoil.gif -o out/recoil-editable \
  -W 64 -H 64 --profile dead-mall --tag recoil --project
```

Use a new output directory each time. Original source files are never changed.
To refine the result, change the palette/canvas and re-import the same GIF. Do
not generate again merely because import needs different options.

A useful prompt to paste into ChatGPT alongside a canonical character still:

> Animate this same character performing one physical recoil action in place.
> Keep its identity, full silhouette and pixel-art details. Use a fixed camera
> and one stable canvas, with room for the whole movement. Show anticipation,
> a clear reaction, and a lively recovery to the starting stance. Keep the
> background consistent. Return an animated GIF, not a still sprite sheet.

The importer cannot fix identity drift, invent better motion, or guarantee a
smooth loop. Inspect actual 1x and 2x frames and play the preview before approval.

## What is preserved

- Sequentially decoded GIF frames are composited on the logical canvas using
  their local palettes, transparency, delta rectangles and disposal methods.
- Frame order, duplicate frames and intentionally blank frames are retained.
- Source frame delays are retained, including unequal delays. Variable timing
  sets `fps` to null instead of advertising a false constant FPS.
- `gif_loop_count` is separate from playback direction: null means no looping
  extension (play once), 0 means infinite, and a positive number is the GIF's
  repeat count. Playback direction is forward. No reverse/ping-pong conversion
  is implied by repeat counts.
- One full-canvas nearest-neighbour transform and one shared palette are used
  by the existing `forge clip` path. No per-frame cropping, recentering, foot
  pinning, cleanup, background removal, or interpolation is applied. Movement
  inside the canvas stays movement.
- Missing/zero GIF delays are rejected because viewers interpret them
  differently. `--default-duration-ms 100` explicitly supplies only those
  missing/zero delays and records each replacement. The value must be a positive
  multiple of 10 ms, at most 655350 ms.

GIF cannot represent soft alpha, so backgrounds produced by a generator remain
unless removed separately. Palette mapping can change visible RGB colours;
with no palette/profile, the 62-colour document limit is enforced rather than
silently quantizing. A fully opaque background is retained and warned about.

Limits match the local clip pipeline: 25 MiB input, 2–256 GIF frames, 16,777,216
pixels per source frame, 67,108,864 total decoded pixels, 1–1024 pixels per target
side, and 16,777,216 total output frame pixels. Use a target width/height for
larger GIF canvases. Oversized, static, wrong-format and undecodable inputs fail
before final output is published. No network or provider process is used.

## Output files

- `source.gif`: exact original GIF bytes.
- `frames/f0.png`, `frames/f1.png`, …: full normalized canvases in source order.
- `sheet.png`, `sheet@2x.png`, `sheet.json`: stable cells and ordered timing data.
- `sprite.png`, `sprite.json`, `sprite@8x.png`: first frame, as in `forge clip`.
- `manifest.json`: source hash, frame paths, durations, canvas and repeat count.
- `animation.gif`: normalized playback preview, verified by decoding and
  comparing every frame, delay and repeat count. Duplicate frames are not merged.
- `gif-report.json` and `clip-report.json`: source provenance, per-frame bounds,
  timing, warnings, verification and `awaiting_visual_review` status.
- With `--project`, `aseprite/animation.aseprite`, `aseprite/sheet.png`, and
  `aseprite/sheet.json`: editable animation plus Aseprite atlas with original
  millisecond timings. Aseprite frame durations are limited to 65535 ms; longer
  GIF delays are rejected for this optional export instead of truncated. Source
  repeat count remains in the manifest, not the Aseprite tag.

The top-level grid `sheet.json` is not an Aseprite atlas. Existing game importers
should use the appropriate format. No game repository is changed. The optional
Aseprite project needs an installed Aseprite executable (`ASEPRITE_PATH`).

## Optional GPT Image 2.6 API adapter

This is a separate, opt-in path. Default invocation only prints a validated
request preview; it does not read a key, send a request, create output, or spend.

```bash
./forge.sh gif-generate "A pixel-art turtle recoils then recovers in place" \
  -o out/turtle-generated --frames 9 --frame-duration-ms 200 \
  -W 64 -H 64 --profile dead-mall

# Still-reference edit: default remains offline.
./forge.sh gif-generate "Animate this character's recoil and recovery" \
  --ref stance.png -o out/stance-recoil -W 64 -H 64 --profile dead-mall
```

Only after checking account access and accepting the charge, a person can add
`--execute-paid`. This requires an existing `OPENAI_API_KEY` environment value.
Pixel Forge does not create, print or save credentials. The still reference is
sent to OpenAI only for explicit execution. It is represented as a data URL in
the request and is not copied into request previews or metadata.

Supported models are `gpt-image-2.6-sunburst` (default) and
`gpt-image-2.6-flare`. The adapter uses `/v1/images/generations` or
`/v1/images/edits`, with `output_format: gif`, `n: 1`, 2–16 frames and a requested
20–2000 ms frame duration. Requested duration rounds to 10 ms; the actual decoded
timings are authoritative. `--size auto` is the default provider image size;
custom dimensions must be divisible by 16 and still satisfy provider-specific
limits. `-W/-H` control the subsequent local sprite conversion, not API image size.

**GIF API preview access is required**, and organization verification may also
be needed. A ChatGPT subscription is not proof of API access. Fixed per-GIF
pricing and account eligibility have not been verified. The adapter has been
tested with fixtures/mocked transport, **not a billable live generation**.

There are no automatic retries, paid fallback models, redirect following,
streaming, mask edits, or implied transparent-background guarantees. A timeout
may still have incurred a charge. Reserved output directories remain after
failed requests to discourage accidental repeats. Successful generation saves
`source.gif` and `provider.json`, then imports into `clip/`. If local conversion
fails, the response says `needs_import_options` and retains the raw GIF: use
`gif-import` with a different palette/canvas rather than resending generation.

Official documentation checked 2026-11-08:
- https://developers.openai.com/api/docs/guides/image-generation
- https://developers.openai.com/api/reference/resources/images/methods/generate
- https://developers.openai.com/api/reference/resources/images/methods/edit
- https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst
- https://developers.openai.com/api/docs/models/gpt-image-2.6-flare

## MCP and existing workflows

Unrestricted local `forge.sh mcp` exposes `forge_gif_import` with `source`,
`out_dir`, optional `width`, `height`, `palette` or `profile`, `tag`,
`default_duration_ms`, and `save_project`. It returns the report plus 1x and 2x
sheets. API generation is deliberately CLI-only. The restricted read-only,
authoring and workflow servers gain no filesystem or paid-provider permissions.
Refresh local tool discovery after installing the change. Existing `prepare`,
`make`, `clip`, and `animate` commands remain available and keep their defaults.

Python callers of `animation.animate` can additionally supply `durations_ms`,
with one duration per frame. Exact millisecond durations are preserved in the
Aseprite project/atlas. GIF export needs 10 ms multiples; set `gif_name=None`
when using finer timings. The existing uniform `fps` mode is unchanged.
