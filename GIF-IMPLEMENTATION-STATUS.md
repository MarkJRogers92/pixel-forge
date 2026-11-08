# GIF implementation status

Implemented 2026-11-08 on top of public main `673f07e`.

- Credential-free downloaded-GIF import, including GIFs made in normal ChatGPT.
- Stable full-canvas transform/shared palette, composited disposal/transparency,
  preserved frame order, duplicates, blank frames, per-frame delays and repeat counts.
- Verified normalized GIF preview, PNG frames, grid sheet/JSON and review report.
- Optional real Aseprite project/atlas export, retaining frame durations.
- Optional GPT Image 2.6 API adapter with offline preview by default and explicit
  paid opt-in. Still-reference edits, no redirects/retries/fallback, bounded input
  and response decoding, safe diagnostics, hashes and request/observed metadata.
- Local unrestricted MCP import tool. No change to restricted server permissions.
- Existing generation, PNG clip and animation workflows preserved.

## Verification

Final suite: **487 passed, 0 failed, 0 skipped**. Includes real Aseprite
1.3.18.6 exports/reopening, real Godot checks, CLI subprocesses, live local MCP
protocol discovery, GIF fixtures, provider transport mocks and existing tests.

The cloud test environment required writable XDG directories for Godot. The
initial unconfigured baseline had three Godot failures because its default
home/cache directories were unwritable; those tests passed once configured.
One existing Pillow `getdata` deprecation warning remains in `forge/lint.py`.

```bash
XDG_DATA_HOME=/tmp/pixel-forge-gif-check/data \
XDG_CACHE_HOME=/tmp/pixel-forge-gif-check/cache \
XDG_CONFIG_HOME=/tmp/pixel-forge-gif-check/config \
ASEPRITE_PATH=/workspace/scratch/46c24ac297cd/aseprite-cloud/aseprite \
.venv/bin/python -m pytest -q tests

.venv/bin/python -m compileall -q forge tests
git diff --check
```

The Aseprite path above is the test machine's existing installation, not a path
to configure on another computer. Use that computer's installed executable.

## Remaining boundaries

- No billable image request was made. Preview entitlement, actual cost and
  generation quality still need a separately authorized live check.
- No generated animation has been artistically approved. Import reports remain
  `awaiting_visual_review`; numerical correctness is not animation quality.
- No push, pull request, merge, deployment or installed user-copy modification
  was performed. The change is packaged from an isolated local checkout.
- No background removal or automatic frame interpolation. Review identity,
  silhouette, palette, motion and loop seam yourself before game integration.

Usage and documented limits: [GIF-WORKFLOW.md](GIF-WORKFLOW.md).
