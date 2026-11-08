# ChatGPT GIF review implementation plan

Goal: Make downloaded ChatGPT animations practical to import, inspect and refine in Pixel Forge without an API call. The user delegated implementation choices ("integrate however we can").

Architecture: Extend the existing local GIF importer rather than add a second server or edit the Piskel submodule. Keep original bytes, frame order, delays and full-canvas alignment. Produce a portable local review page alongside every import. Add explicit edge-connected color removal, off by default, before resizing and palette mapping. This is a color key, not subject segmentation: enclosed matching details survive, matching details connected to the canvas edge may not.

Tech stack: Existing Python/Pillow/NumPy, static HTML/CSS/JavaScript. No dependencies, paid providers, game edits or publishing.

Alternatives considered: Documentation alone would not make output easier to review. An editor upload UI would require editing the independent Piskel repository and expanding HTTP permissions. The existing CLI/MCP importer plus a local review page is the smallest complete improvement.

The real downloaded GIF has 255 colors. Add explicit `colors` / `--colors` (1..62), mutually exclusive with palette/profile, to derive one bounded deterministic source palette. Default exact-color behavior stays unchanged.

## Steps

- [x] Add failing importer and CLI/MCP tests: opt-in exact/tolerant border removal, enclosed dark pixels, source/timing retention, malformed options, no-option compatibility.
- [x] Implement `background_color=None`, `background_tolerance=0` on `import_gif` and unrestricted MCP; add CLI flags only to `gif-import`. Record operation/counts/caveats in report and manifest. Reject tolerance without a color and invalid color/tolerance before publication.
- [x] Add tests and implement `review.html` with escaped metadata, source/output playback, checker/light/dark backgrounds, 1x/2x frames, warning summary, delays/repeats, and file links. No network calls or auto-approval.
- [x] Update help, README and GIF workflow with actual ChatGPT download, stable canvas prompt, explicit opt-in keying, Aseprite and review steps.
- [x] Run full pytest, compileall and diff checks. Import the user's real local 12-frame mop GIF with and without keying into new folders, inspect pixels and review UI. Verify source hash remains unchanged.
- [x] Obtain independent code review, fix findings, rerun relevant/full checks, commit locally and report installed branch versus unpublished status.

## Review focus

- A black enclosed feature remains opaque; an edge-connected black outline is not falsely promised safe.
- Static/malformed GIFs and invalid options leave no published output.
- Intentionally blank frames and duplicate delays remain present.
- Source-derived strings cannot become HTML/script markup.
- Local file links, repeat counts and frame playback remain correct when the output folder moves.
