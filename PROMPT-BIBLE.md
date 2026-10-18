# Prompting for readable pixel art

This short guide adapts ideas from [TheSpriteWizard's prompt bible](https://www.reddit.com/r/aigamedev/comments/1x649al/my_latest_prompt_bible/). It is a practical brief-writing aid, not a guarantee that an image model will obey a pixel grid or palette exactly.

## Give the sprite a clear priority

Name the subject and the few features that make it recognizable at 1x. Ask for a strong, intentional silhouette, then spend detail on those features. Crisp, consistent pixel clusters and lighting that fits each material usually read more clearly than scattered tiny marks. A compact palette helps; about three shade values per material can be a useful starting point where appropriate, not a fixed limit.

Describe the requested pose, expression, materials, lighting, and any important effects. Keep the requested style authoritative: neon, glow, bright screens, and other deliberate accents belong when the brief calls for them.

If it helps, name details to leave out when they are not part of the design: invented buckles or rivets, microtexture, loose strands, random gloss, or decorative rim light. Do not use exclusions to remove features you asked for.

## Use the optional profile

For generic pixel art, `restrained-pixel-art` provides these priorities as an optional style profile. It works with both `prepare` and `make`:

```bash
./forge.sh prepare "a brass key with a large red glass jewel" --profile restrained-pixel-art -W 32 -H 32 \
  --check "large red jewel reads clearly at 1x" --check "recognizable key silhouette"
```

Selecting `--profile` uses that profile instead of the project's learned profile; profiles do not stack. For DEAD MALL, prefer `--project dead-mall` to retain the game's learned style and palette, then put a short detail priority and any unwanted extras in the brief and `--check` values. The existing prompt wording remains the strongest guide to subject-specific intent.

For DEAD MALL, keep the project's style and palette, name the identifying cues in priority order, and exclude only unwanted extras. For example:

```bash
./forge.sh prepare "cream 1990s mall payphone; prioritize the oversized black handset, then the coiled cord; omit invented labels and tiny screws" --project dead-mall -W 32 -H 48 \
  --check "handset is the clearest cue at 1x" --check "coiled cord is visible" --check "no invented labels or tiny screws"
```

In MCP calls, use the existing `profile` argument with `forge_prepare` or `forge_make`.

For every generated candidate, check its actual dimensions, palette, and transparency through the existing QA/review pipeline. A `prepare` result remains `awaiting_visual_review` until it has been inspected at 1x and 2x.

With `prepare --input`, GPT style prompting is skipped. The supplied image goes through the existing neural fixer and cleanup stages, which can change output pixels; the selected profile may also affect later palette handling.
