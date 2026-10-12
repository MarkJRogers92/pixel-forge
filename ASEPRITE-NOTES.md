# Aseprite 1.3.18.6 — REA notes and Lua API surface

What the Aseprite-backed passes (`forge/aseprite_ops.py`) rely on, and the
evidence behind it. Gathered on 2026-10-12 with REA (installed-binary
inspection) plus live probing of the running Aseprite Lua engine
(`tools.aseprite.run_lua`).

## Target identity (REA)

- Path: `/Applications/Aseprite.app` → `Contents/MacOS/aseprite`
- Universal binary: `x86_64` + `arm64`; arm64 slice SHA-256
  `5707576b200c1cdd42ca6c923bba9301dd6edbdfa1d7cb831af1fc1c0e164d35`
  (76,570,032 bytes on disk). Version `1.3.18.6`.
- Code signing (REA `inspect_signature`): identifier `org.aseprite.Aseprite`,
  **Developer ID Application: Igara Studio S.A. (6J55SF39UH)**, hardened runtime,
  entitlements `com.apple.security.cs.allow-dyld-environment-variables` and
  `...disable-library-validation`.
- No decompiler provider (Ghidra/Hopper/IDA) is configured for REA here, so the
  API surface was established by string-surface extraction plus live Lua probing
  rather than decompilation. Aseprite is open source, so its Lua API is also
  documented upstream; the probe confirms the *installed* build matches.

## Runtime facts (live probe)

- `app.version` = `1.3.18.6-arm64`; `app.apiVersion` = **41**.
- Globals: `Sprite`, `Image`, `Palette`, `Point`, `Size`, `ColorSpace`, `Brush`
  are constructors; `ColorMode`, `BlendMode`, `Ink` are enum tables;
  `app.command`, `app.fs`, `app.pixelColor` are tables. `Color(r,g,b,a)` works.
- `not present as globals`: `DitheringMatrix`, `Tag`, `Slice`, `Tileset`,
  `Layer`, `Cel`, `Rect`, `Matrix`, `Filter`, `AnimationDirection`,
  `LoopAnimation`.
- `Image` members that exist: `clear`, `getPixel`, `drawPixel`, `drawImage`,
  `putImage`, `resize`, `flip`, `saveAs`, `saveCopyAs`, `setPalette`,
  `loadPalette`, `colorMode`. `Image:dither`, `Image:blend`, `Image:fill` do
  **not** exist (accessing them raises "Field … does not exist").
- `Sprite` members: `newLayer`, `newCel`, `newFrame`, `newTag`, `newSlice`,
  `newTileset`, `newGroup`, `flatten`, `deleteLayer`, `deleteCel`,
  `deleteFrame`, `saveAs`. Properties: `width`, `height`, `frames`, `layers`,
  `cels`, `palettes`, `tags`, `slices`, `colorMode`, `filename`.
  - `sprite:flatten()` mutates the sprite in place and returns `nil`
    (verified: 2 cels → 1 cel).
- `Layer`: `blendMode` (assign a `BlendMode` value), `opacity` (0..255).
- `Palette`: `resize(n)`, `setColor(i, Color)`, `getColor(i)`, `saveAs`;
  length via `#palette` (there is no `.colors` / `.size` field).
- `Cel`: `.image` (an `Image`), used for pixel reads/writes.

### BlendMode enum values (read live)

`NORMAL=3, MULTIPLY=14, SCREEN=15, OVERLAY=16, DARKEN=17, LIGHTEN=18,
COLOR_DODGE=19, COLOR_BURN=20, HARD_LIGHT=21, SOFT_LIGHT=22, DIFFERENCE=23,
EXCLUSION=24, HSL_HUE=25, HSL_SATURATION=26, HSL_COLOR=27, HSL_LUMINOSITY=28,
ADDITION=29, SUBTRACT=30, DIVIDE=31`.

### Ink enum values

`SIMPLE=0, ALPHA_COMPOSITING=1, COPY_COLOR=2, LOCK_ALPHA=3, SHADING=4`.

## Commands used (verified in `aseprite -b` batch mode)

- **Dithering** — `app.command.ChangePixelFormat{ ui=false, format="indexed",
  dithering="<algo>" }` where `<algo>` is `ordered`, `error-diffusion` or
  `threshold`. Install the target palette first with `sprite:setPalette(p)` to
  dither into an exact palette. Backed in the binary by
  `render::OrderedDither` / `render::ErrorDiffusionDither` / `OrderedDither2`,
  and the CLI flags `--dithering-algorithm` / `--dithering-matrix`.
- **Quantization** — `app.command.ColorQuantization{ ui=false, maxColors=N }`
  shrinks the palette (observed 256 → N). The pixels are remapped only by a
  following `ChangePixelFormat{ format="indexed" }`. `colors` / `ncolors` are
  ignored; `maxColors` is the parameter that takes effect.
- **Blend compositing** — create a layer, set `blendMode`/`opacity`, add a cel
  from the top image, then `flatten()`. Verified: white × `#808080` under
  `MULTIPLY` yields `#808080`.
- Command ids seen as userdata: `Outline`, `ColorQuantization`,
  `ChangePixelFormat`, `ExportSpriteSheet`, `ImportSpriteSheet`, `SetPalette`,
  `Flip`, `Rotate`, `InvertMask`, `ReplaceColor`, `SpriteSize`.

## Animation and export (verified in batch mode)

- **Frames** — `sprite:newFrame()` appends a frame and returns it; set
  `frame.duration` (seconds, e.g. `0.1`). `#sprite.frames` is the frame count.
- **Tags** — `sprite:newTag(frame)` returns a tag; set `name`, `fromFrame`,
  `toFrame` and `aniDir = AniDir[...]`. The `AniDir` enum values are
  **`FORWARD=0`, `REVERSE=1`, `PING_PONG=2`** (note: `PINGPONG` is *not* a member).
- **Sprite sheet** — `app.command.ExportSpriteSheet{ ui=false, type=<t>,
  textureFilename=<png>, dataFilename=<json>, dataFormat=<json-hash|json-array> }`.
  `type` accepts `horizontal`, `vertical`, `packed`, `rows`, `columns`, `grid`
  (all verified). `json-hash` writes per-frame rects plus `duration`.
- **Animated GIF** — `sprite:saveAs("<path>.gif")` writes a GIF89a covering the
  frames with their durations.
- **Slices / groups** — `sprite:newSlice()` with `slice.bounds = Rectangle(x,y,w,h)`
  and `sprite:newGroup()` both work. `sprite:newTileset(layer)` needs an extra
  grid/table argument, so it is not used.

Used by `forge/animation.py` (`forge animate` / `forge_animate`).

## Known limitation (why there is no Aseprite-backed outline)

`app.command.Outline{ ui=false, … }` **runs without error but does not modify the
sprite** in batch mode on 1.3.18.6. This was checked at the pixel level with the
`matrix` given as a nested table, a flat 9-element table, and strings
(`"0 1 0 1 1 1 0 1 0"`, `"010111010"`, `"0,1,0;1,1,1;0,1,0"`), with
`place="outside"` and `"inside"`, on both a script-created sprite and one opened
with `app.open`. No pixel or canvas-size change occurred in any variant
(`OutlineParams` / `OutlineFilter.Place` / `OutlineFilter.Matrix` exist in the
binary, and an `outline_matrix` preference string is present). So the passes
module does not expose an Aseprite outline; Forge's own `outline` DSL op covers
that.

## How the passes call Aseprite

`forge/aseprite_ops.py` writes the input sprite(s) to temporary PNGs, runs a Lua
script via `aseprite -b --script-param … --script <file>`, and reads the result
PNG back into a `forge.sprite.Sprite`. `find_executable()` is shared with
`forge/aseprite.py` (honours `ASEPRITE_PATH`, then `PATH`, then the usual
locations).
