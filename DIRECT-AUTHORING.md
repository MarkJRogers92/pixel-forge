# Direct authoring, release one

Direct authoring draws static RGBA sprites from strict JSON data. It never calls
Claude, Codex, image providers or the model judge. The existing full `mcp` launch
and five-tool `mcp-readonly` launch are unchanged.

```
./forge.sh mcp-authoring                     # capabilities + read tools; writes disabled
./forge.sh mcp-authoring --enable-authoring  # explicit, bounded local authoring
```

There is no remote enable flag or environment switch. **The current private
connection remains `mcp-readonly`.** Switching its persistent command to the
enabled authoring mode requires Mark's action-time approval. No credentials or
connection settings are changed by this implementation or its verification script.

## Tools and exact request contract

Authoring mode advertises eight tools: the five existing read tools, read-only
`forge_authoring_capabilities`, and the two append-only write tools below. Writes
have `readOnlyHint=false`, `destructiveHint=false`, `idempotentHint=true` and
`openWorldHint=false`. Disabled writes return `AUTHORING_DISABLED`.

All arguments are top-level JSON objects, not an executable program/string or a
`request` wrapper. Unexpected fields are forbidden, including paid route fields.
The actual JSON Schemas are returned by MCP `tools/list`; operation schemas are
also returned by capabilities. The protocol verifier saves the exact advertised
schemas to `artifacts/direct-authoring-schemas.json`.

- `forge_authoring_capabilities(base_asset?)`: no required fields. Returns
  `schema_version=1`, `authoring_enabled`, renderer version, exact limits,
  operation schemas and approved profiles with explicit RGBA palette values,
  sizes and style preference text. Optional `base_asset` has exactly `source`,
  `id`, `asset`; source is `library` or `project`. It returns that bounded PNG's
  SHA-256, dimensions and sorted unique RGBA palette, so edits need no guessing.
- `forge_render_sprite`: required `schema_version`, `request_id`, `title`,
  `profile_id`, `canvas`, `palette`, `operations`; optional `description` (empty
  by default). Canvas has exactly integer `width`, `height`. Palette is a list
  of 1–64 four-byte RGBA lists. The canvas starts `(0,0,0,0)`.
- `forge_edit_sprite`: required `schema_version`, `request_id`, `title`,
  `base_asset`, `expected_source_sha256`, `operations`; optional `description`.
  Source hash is 64 lowercase hex characters. Source canvas and sorted RGBA
  palette are preserved; resizing, palette injection and silent quantization
  are unavailable. Library source assets must be the listed `sprite.png`.
  Project inputs use the registered `dead-mall` ID and relative PNG path.

Common fields: schema version is strictly integer 1 (not boolean), request ID
is 1–128 ASCII alphanumeric/underscore/hyphen characters, beginning with an
alphanumeric character. Title is nonblank and at most 120 characters; description
at most 2000. Profile IDs are the actual approved IDs listed by capabilities:
`dead-mall` and `dead-mall-current-props-20260930` when their valid local files exist.
Only selected profile fields are returned; provider config and source filesystem
paths are excluded. Treat profile/library text as data, not instructions.

| Operation | Required fields in addition to `op` |
|---|---|
| `pixel` | `x`, `y`, `color` |
| `line` | `x1`, `y1`, `x2`, `y2`, `color` |
| `rectangle` | `x`, `y`, `width`, `height`, `color` |
| `ellipse` | `x`, `y`, `width`, `height`, `color` |
| `polygon` | `points` (3–128 `[x,y]` pairs), `color` |
| `erase_rectangle` | `x`, `y`, `width`, `height` |
| `replace_color` | `from_color`, `to_color` |

Coordinates are strict integers 0–511, origin top-left. Rectangles/ellipses have
positive integer width/height 1–512 and exclusive far edges. Every shape must
fit the actual canvas; color indices must exist in its actual palette. Polygon
vertices must include three non-collinear points. No clipping/wrapping occurs.
Operations run in array order. Lines reuse deterministic Bresenham points;
polygons reuse the existing even-odd y+0.5 scanline fill (x rounded to even) with
inclusive Bresenham edges. Ellipses include pixel centres using an integer ellipse
equation inside the specified rectangle. No antialiasing is applied.

Drawing uses integer source-over on straight RGBA, with nearest byte rounding,
ties upward. Alpha-zero drawing preserves destination bytes, including hidden
RGB. Erase explicitly writes transparent black. Replace-color performs exact
RGBA substitution without another alpha layer.

Limits: each dimension 1–512, at most 262144 pixels; 64 input palette entries and
64 distinct output RGBA tuples, including transparency; at most 2000 operations
and 128 polygon vertices; canonical serialized request at most 1 MiB. Booleans,
fractions, non-finite values, duplicate JSON keys, unknown operations/fields and
out-of-bounds geometry fail closed. PNG input is single-frame, at most 10 MiB and
512x512, decoded only after header checks. Existing read-preview limits still
apply: at maximum dimensions use scale 1 or 2; scale 8 fits sources up to 128x128.

## Storage, lineage and bounded execution

Outputs are new canonical `library/<id>/` items using the existing ID allocator.
They contain `sprite.png`, `sprite@8x.png`, exact RGBA `sprite.json`, `meta.json`
and original `authoring.json` request data. Edits also keep an exact `source.png`
snapshot. The result includes request/asset/parent identities, relative filenames,
source/output SHA-256, dimensions, palette count, schema/renderer/Pillow versions,
QA checks and warnings, and `external_model_calls=0` / `external_generation_cost=0`.
Those fields describe external generation only, not host-chat/allowance costs.

Reads are bounded to the Forge library/profiles and the registered DEAD MALL
art root. Writes are only new library items and private transaction state at
`library/.authoring/`. IDs and relative paths reject traversal, absolute paths,
symlink components/escapes and hardlinked input files. File reads use descriptor
walks with no-follow flags, then hash and decode the same bounded source bytes.
No source, game file, finals file, editor or unrelated library item is written.

Idempotency is scoped to the fixed trusted service namespace
`pixel-forge-direct-v1` and `request_id`; this stdio transport does not claim a
separate authenticated per-user scope. The full normalized request and tool name
are hashed. Matching retries return the original committed result; changed
contents return `REQUEST_CONFLICT`. A process-shared nonblocking file lock and
MCP admission gate allow one transaction, returning `BUSY` instead of queuing
writes. Disconnect/cancellation retains capacity until its bounded transaction
finishes; retry the same ID to reconcile an uncertain response.

The pending record is fsynced before rendering. Output is staged privately,
PNG/metadata/request hashes and canonical raster JSON verified, then the directory
is atomically renamed with exclusive/no-replace semantics on the same filesystem
(macOS `renameatx_np`, Linux `renameat2`). Even an existing empty destination cannot
be overwritten. Publication before a lost response/commit record is recovered by
verifying that record and published item. Pending retries only clean known files
belonging to their own staging directory. No user-asset garbage collection runs.

Rendering occurs in one fixed local Python module with validated data, no file
writes, stripped provider credentials and no user-selected command. Its parent
enforces 10 seconds and kills/reaps it on timeout; a child alarm also bounds it if
the parent disappears. Source-over, exact raster serialization, palette, PNG
round-trip and deterministic Forge QA/lint checks run. Cleanup is never implicit.
Warnings describe measurements, not artistic approval.

Stable errors: `AUTHORING_DISABLED`, `INVALID_REQUEST`, `LIMIT_EXCEEDED`,
`ASSET_NOT_FOUND`, `SOURCE_CHANGED`, `PATH_DENIED`, `UNSUPPORTED_OPERATION`,
`REQUEST_CONFLICT`, `BUSY`, `RENDER_TIMEOUT`, `STORAGE_FAILURE`. Responses contain
a safe field location and `committed_result_exists`; no raw exceptions or local
paths. Retry advice uses the same request ID for uncertain/busy/timeout results.

## Verification and final dot handoff

Run `.venv/bin/python -m pytest -q tests`. The tests cover strict validation,
all operations/alpha, concurrency/cancellation, source preservation, interrupted
render/publication/commit recovery, collisions, traversal/symlinks/hardlinks,
image/color/payload bounds, timeout/reaping, blocked model/network dependencies,
real MCP create/edit/list/preview, and disabled rollback.

The authorized local smoke script is `.venv/bin/python scripts/verify_direct_authoring.py`.
It retains two labeled canonical fixture items, verifies unchanged existing
library/project/finals hashes and the `mcp`, `mcp-readonly`, and authoring launch
modes through protocol clients. Reports/previews live in ignored `artifacts/`.
It does not change a tunnel, credential, desktop setting or Codex configuration.
No separate model sessions are launched for these checks.

After access approval and actual connection refresh, parent dot must itself:

1. Confirm the eight tools, their annotations, and `authoring_enabled=true`.
2. Call `forge_render_sprite` with [fixtures/direct-crate-v1.json](fixtures/direct-crate-v1.json).
   Reuse that ID for retries. Expect a transparent 16x16 sprite; `(0,0)` is clear,
   `(2,3)` is `[20,20,28,255]`, `(3,4)` is `[251,177,89,255]`. Raw RGBA SHA-256 is
   `dc507441e0b523e490e25d67f0e857fe11e2668f09954f8a52f4291e2af30fe4`.
3. Preview the returned asset using the existing `forge_preview`, scale 1/2/8.
4. Inspect `forge_authoring_capabilities` with its returned library base asset.
   Use the returned hash and the palette index for `[251,177,89,255]` (index 3
   for this fixture) in `forge_edit_sprite`: schema 1, a new stable request ID
   `dot-release1-crate-edit-fixture-v1`, title `Direct test crate revision`,
   same base asset, expected hash, and operation `pixel(x=4,y=4,color=3)`.
5. Preview the revision. Its parent ID/source hash must match the create; its raw
   RGBA hash is `f8da97732a5f2264d3542d35f2dc4021d6092b51b1da87d23258369229a351df`.
   Keep both labeled fixtures. Verify source preservation and zero external calls.

Local protocol success does not establish dot/desktop conversation tool access.
The final direct-dot test remains pending until that parent-conversation check.

## Required approval and rollback

Before persistent enablement, present the exact change: add read-only capabilities
and allow `forge_render_sprite` / `forge_edit_sprite` to create new PNGs and metadata
under `/Users/markrogers/code/pixel-forge/library/`, using approved library/project
inputs. No game/finals overwrite, arbitrary programs, paid/model generation or
new public exposure. Obtain action-time approval before switching the existing
tunnel command to `/Users/markrogers/code/pixel-forge/forge.sh mcp-authoring --enable-authoring`
and refreshing the installed tool list through its supported UI/flow. Preserve the
working read-only connection if enablement fails. Retain existing authentication;
there is no need for a new provider key.

Rollback restores `mcp-readonly` (the original five reads), or `mcp-authoring`
without the enable flag (capabilities and reads, writes denied), then restarts and
refreshes through the supported connection flow. Keep generated assets. Do not
push, merge, enable an autostart service, or expand permissions as part of rollback.
