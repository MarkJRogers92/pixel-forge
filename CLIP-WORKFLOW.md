# Stable PNG clip import

`forge clip` imports an explicitly ordered set of already-extracted PNG frames.
It uses one full-canvas transform and one palette for the entire animation, so
jumps, crouches, recoil, and changes in the character's silhouette keep their
relative positions. It is a local operation with no generation, provider,
model download, Aseprite requirement, or credit cost.

```bash
# Preserve canvas, visible RGBA pixels, explicit order, and duplicate frames.
./forge.sh clip stance.png recoil.png recovery.png stance.png \
  -o out/turtle-recoil --fps 12 --tag recoil

# Fit the entire source canvas into 64x64 once, using the existing game palette.
./forge.sh clip f000.png f001.png f002.png f003.png \
  -o out/recoil-small -W 64 -H 64 --profile dead-mall --fps 12

# Or supply a shared fixed RGB palette instead of a profile.
./forge.sh clip f000.png f001.png -o out/recoil-two-colours \
  --palette '#241526,#ed5b9a' --loop pingpong
```

Use a new output directory for each import; existing paths are refused. Frame
order is exactly the argument order, including repeats. The importer does not
sort filenames. Shell globs are expanded by the shell, so use zero-padded names
or spell out the sequence to avoid `f1, f10, f2` ordering mistakes.

## Canvas, palette, and alpha contract

- Inputs must be individual PNGs with equal canvas dimensions and nonempty
  visible content. Animated PNGs and other formats are rejected.
- With no target size, the canvas is preserved. One target dimension derives
  the other from the source aspect ratio. Two target dimensions fit the full
  source canvas with nearest-neighbour scaling and centered transparent padding.
  Integer rounding and the same offset apply to every frame.
- Content bounding boxes are measured only. They never drive resizing,
  recentering, foot pinning, cropping, or background removal. A small subject
  that disappears during downsampling causes a clear error.
- Without `--palette` or `--profile`, visible RGBA colours are preserved exactly.
  One deterministic palette covers the whole clip. Transparent RGB bytes are
  normalized to zero, since the sprite document has one transparent key.
- `--palette` and `--profile` are mutually exclusive. They map RGB to a fixed
  palette in Lab space while retaining every source alpha byte. Palette entries
  must be opaque; alpha remains a property of the input pixels.
- The sprite document has 62 palette keys. This is a limit on the whole clip's
  distinct RGBA colours, including different alpha values. An overflow is
  rejected before output; there is no automatic per-frame colour estimation,
  alpha threshold, or hidden cleanup. Choose a smaller fixed palette or prepare
  fewer alpha variants explicitly if needed.
- `--fps` must be positive and finite. `--loop forward|reverse|pingpong` describes
  playback in the manifest. Neither option reorders, duplicates, or deletes the
  exported frames. PNGs supply no original timestamps; this command records the
  explicit uniform FPS rather than inferring timing from filenames.

Bounds: 1–256 frames; at most 25 MiB per input file and 128 MiB for the clip;
16,777,216 pixels per source frame and 67,108,864 source pixels total; output
canvas sides 1–1024 pixels and 16,777,216 output frame pixels total. These limits
keep loading and colour mapping bounded.

## Outputs and review

The existing manifest exporter writes:

- `frames/f0.png`, `frames/f1.png`, and so on, in supplied order
- `sheet.png` and `sheet.json`, preserving equal frame cells
- `sprite.png`, `sprite.json`, and `sprite@8x.png` for the first supplied frame
- `manifest.json` and `meta.json`, with canvas alignment and `generation: null`

The clip importer also writes `sheet@2x.png` using nearest-neighbour scaling and
`clip-report.json`. The report contains source paths and SHA-256 hashes, the
shared transform, palette, per-frame content bounds, explicit FPS/loop, and
results from re-reading and verifying the exported sheet and frames. Original
input files are never changed.

Warnings identify content touching the source edge (possible cutoff, not proof
of it), opaque backgrounds, partial alpha, and unequal first/last output frames.
They do not modify the animation. The endpoint test is an exact output-pixel
comparison: equality does not prove a smooth velocity transition, and inequality
can be intentional for a one-shot action or ping-pong playback.

Review both sheets at actual 1x and 2x for silhouette, colour consistency, motion,
and clipping. The report stays `awaiting_visual_review`; numerical checks do not
approve artistic quality. This importer does not produce a GIF. The existing
Aseprite-backed `forge animate` workflow remains separate and unchanged. This
manifest's `sheet.json` is its grid contract, not the Aseprite atlas format used
by `godot-frames`.

## MCP access

The unrestricted local MCP server (`forge.sh mcp`) exposes `forge_clip` with
`frames` (ordered PNG paths), `out_dir` (a new directory), and the same optional
`width`, `height`, `fps`, `loop`, `palette`, `profile`, and `tag` options. It returns
the report plus both 1x and 2x sheets. It never invokes providers or Aseprite.

The restricted `mcp-workflow` server also exposes `forge_clip`, using only
`source_library_ids` and a stable `request_id`. It writes a new library item,
returns immediately, and delivers the 2x sheet through `forge_workflow_status`.
It accepts dimensions, FPS, loop, tag, and an optional fixed RGB palette, with
strict bounds and no caller-selected filesystem paths. See [WORKFLOW-MCP.md](WORKFLOW-MCP.md)
for the restricted limits and deployment/discovery checks. Existing permission
flags and connection settings remain unchanged; refresh tool discovery after
the updated code is installed.

## Preparing source animation

The following advice is adapted from the creator-written explanation by
[u/FutureTechPilot (Nolan)](https://www.reddit.com/r/aigamedev/comments/1x8m8ly/my_tricks_for_using_ai_video_to_create_2d_battle/).
The text was available; the attached video could not be viewed, so these are
reported techniques rather than a visual validation of that clip or model.

1. Keep a canonical fighting stance and, where the generator supports reference
   endpoints, supply that same stance for the first and last frames. This helps
   actions meet at a common pose; a duplicate endpoint can also produce a hold.
2. Animate in place with a fixed camera and full character visible. Let gameplay
   code handle movement through the level.
3. Describe the body's physical motion literally. For example: “The turtle
   rocks backward on its shell, closes its eyes, then returns to its fighting
   stance.” Avoid describing an unseen attacker as the cause of the reaction.
4. Use plain descriptions and consistent short durations. Avoid naming unwanted
   effects in negative instructions, which can still draw attention to them.
5. A slightly darker strip beneath the feet is the creator's suggested reference
   aid for stable ground/camera framing. Its removal is unresolved in the post;
   the importer retains it and cannot promise lossless background removal.
6. Draw contact flashes, hit stars, damage numbers, and screen shake in the game,
   timed to the contact frame, instead of baking them into the character clip.

A compact prompt/reference contract is: one canonical stance, fixed camera,
full character inside a stable canvas, one literal physical action performed in
place, a common short duration, and matching reference endpoint poses. Treat the
creator's approximately 1.6-second duration as approximate, not a validation
rule. Extract frames externally at a deliberate FPS without interpolation, and
inspect their order and alpha before importing. No new generation backend or
video extraction service is included in this command.
