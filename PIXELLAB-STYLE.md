# PixelLab style comparison

Pixel Forge has an opt-in BitForge route and comparison trial for testing
whether a profile's reference sprite and palette help PixelLab match the
game's art direction. The normal `pixellab` route and default workflow are
unchanged.

To request one guided PixelLab draft alongside the regular `make` drafts, use
the optional route with a profile that has both references and a palette:

```bash
./forge.sh make "a neon mall payphone" --profile dead-mall --route pixellab-style
```

This route makes one BitForge generation with the profile's first local
reference image and its forced palette. It does not run the two-image trial.

First run the safe local preview. It validates the selected profile, reference
image, palette, and dimensions without looking up API credentials or contacting
PixelLab:

```bash
./forge.sh pixellab-style-test "a neon mall payphone" --profile dead-mall -W 32 -H 48
```

If the report looks right, add `--run` to submit exactly two BitForge
generations with the same prompt, dimensions, and seed: text only, then profile
reference plus forced palette. For example:

```bash
./forge.sh pixellab-style-test "a neon mall payphone" --project dead-mall \
  -W 32 -H 48 --seed 71 --run
```

If the baseline succeeds but the guided request fails, preserve that library
item and explicitly retry only the guided half:

```bash
./forge.sh pixellab-style-resume 20261011-001351-example-1234
```

The resume command accepts only a PixelLab style trial with a completed text
baseline and failed guided result. It records the failed attempt and the new
payload hash, preserves the baseline files, and makes at most one guided call
per invocation. A completed guided result returns without another API call.

The run creates a new library folder containing each provider image, a cleaned
copy, QA and palette-distance measurements, usage when returned, and
`review.png` at native, 2x, and 8x scales. The saved status is
`awaiting_visual_review`; the measurements do not judge which image looks
better. A failed call is recorded while any successful result is kept. The
trial does not retry, switch models, or alter the standard PixelLab route.

Use `--reference /path/to/local.png` to choose a local style reference instead
of the profile's first reference. Remote URLs are not fetched. BitForge accepts
dimensions from 16 through 200 pixels per side; the command rejects other
sizes rather than silently changing them. Style references are resized with
nearest-neighbor sampling to fit inside the requested canvas, preserving their
aspect ratio and adding transparent padding as needed. The palette image uses
the same dimensions and contains only colors from the selected palette.
