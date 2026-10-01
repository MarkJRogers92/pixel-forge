# Asset workflow

The process that produced the final arcade cabinet (2026-09-29). Use it for every new asset.

## 1. Brief → checklist
Turn the request into 3-6 concrete checks that must be visible at 1x, e.g.
"screen crack clearly visible at 1x", "marquee glows with no letters",
"clean dark outline, no speckle". No text in art unless asked (the game renders signs).

## 2. Generate two routes in parallel
```bash
./forge.sh make "<brief>" --project dead-mall -W 32 -H 48 -n 3 --rounds 2 --artist claude \
  --check "..." --check "..." --out ~/Desktop/art/<asset>/claude.png
./forge.sh make "<brief>" --project dead-mall -W 32 -H 48 -n 3 --rounds 2 --artist gpt-image \
  --check "..." --check "..." --out ~/Desktop/art/<asset>/hybrid.png
```
- Claude route: cleanest, most reliable detail.
- GPT-paints + Claude-redraws route: most character (e.g. bold side art).
Each run: 3 drafts judged → review rounds on the winner → best version kept → cleanup.

## 3. Compare at game size
Put every candidate side by side at 1x, 2x and zoomed on the dark game background.
Pick favourites by what reads at 1x, not by the zoomed view.

## 4. Targeted fixes, never repaints
- Small changes (tone down an accent, recolour, move a detail): patch only those
  pixels on top of the existing sprite. Toning down = move accent colours one step
  darker/less saturated in their ramp, keep everything else pixel-identical.
- Adding an element: `--start <favourite.png>` with a prompt that says what to keep,
  and checks that the kept parts are unchanged.

## 5. Combine favourites
```bash
./forge.sh make "COMBINE ... keep from start.png: ... bring over from reference_1.png: ..." \
  --start <shape-and-character.png> --ref <detail-version.png> -n 3 --rounds 1 \
  --check "..." --out ~/Desktop/art/<asset>/combined.png
```

## 6. Finals
Copy the chosen sprite(s) to `~/Desktop/art/finals/` (with an `@8x` preview).
Everything is also kept in `library/` with all drafts, rounds, judge scores and notes.

## Art direction (Mark's preferences)
- Character (bold neon accents, distinctive shapes) but clean and readable at 1x.
- No speckle or noisy painted texture; accents toned to mid/dark ramp steps on narrow areas.
- Edits must not lose existing detail.
