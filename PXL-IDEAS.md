# claude-pxl review notes

Reviewed upstream source: [crsolver/claude-pxl at 21fee41](https://github.com/crsolver/claude-pxl/tree/21fee41f7ecce00bc868d7567f24ffd0f3acc8fa).

The useful ideas for Pixel Forge were live operation replay, reusable parts, and
patching animation frames over a shared drawing. Forge already has deterministic
pixel operations, lighting passes, and an Aseprite animation exporter. This small
extension adds literal reusable parts and independent frame patches to that path.

The live viewer, scene composition, and per-frame variable durations remain deferred.
The upstream review was source inspection; it does not establish rendered art quality.
No upstream code was copied, no skill was installed, and this example uses no paid
generation service.

## Run the example

```bash
./forge.sh animate --program examples/part-flicker.json -o out/part-flicker --fps 8
```

The program draws one shared prop body and stamps its lamp part twice. Each frame
starts from that same common drawing, then applies only its own small light edit;
frames never accumulate edits from the previous frame. `--fps` controls timing.

To patch an already approved sprite, add `--base approved-sprite.png` and set
`"base": "current"` in the program. The common operations and each frame's edits
then preserve the supplied sprite details unless an operation changes them. Imported
base PNGs must use binary alpha (only fully transparent or opaque pixels) so conversion
does not discard partial transparency. Existing palette keys retain their source colors;
redefining one with a different color is rejected, so use an unused key for new colors.
