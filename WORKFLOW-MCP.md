# Narrow workflow connection

Current live launch, activated after Mark explicitly approved the exact expansion:

```bash
/Users/markrogers/code/pixel-forge/forge.sh mcp-workflow --enable-authoring --enable-workflow
```

Existing eight tools remain: `forge_status`, `forge_projects`,
`forge_project_assets`, `forge_library`, `forge_preview`,
`forge_authoring_capabilities`, `forge_render_sprite`, `forge_edit_sprite`.

Six workflow tools (including the local clip importer):

| Tool | What Dot does |
|---|---|
| `forge_prepare` | Start one GPT reference → free RD neural fixer → cleanup/QA. An approved Library image can skip GPT. |
| `forge_workflow_status` | Read progress/result using the same `request_id`; completed preparation/repair/import returns a review image. |
| `forge_repair` | Request one focused Claude CLI Opus patch after finding a concrete problem. |
| `forge_open_aseprite` | Create and open a new layered document; get its `document_id` from status. |
| `forge_import_aseprite` | Import that document into a new Library item with QA and another visual-review sheet. |
| `forge_clip` | Import an explicitly ordered list of existing Library sprites into a new animation clip, with one shared transform/palette and a 2x review sheet. No providers or Aseprite. |

Workflow writes declare readOnly=false, destructive=false, idempotent=true.
Prepare/repair declare openWorld=true because they use providers; Aseprite tools
and clip import declare openWorld=false. Progress is read-only. Workflow and direct drawing have
separate explicit enable flags; omitted flags keep their writes disabled.

## Dot sequence

1. Create a stable request ID and call `forge_prepare` with `prompt`, `width`,
   `height`, `project="dead-mall"`, and up to eight `checks`.
2. Poll `forge_workflow_status(request_id)` until completed. Inspect its image at
   1x/2x for identity, readability, style and the checklist. QA does not mark it
   artistically approved.
3. If a concrete repair is needed, use a new request ID for `forge_repair` with
   `library_id`, `instructions`, and optional `max_changed_pixels`; poll and review.
4. Call `forge_open_aseprite` with a new request ID and chosen `library_id`.
   The returned document ID names a new file within that item's `documents/`.
5. After pixel edits, call `forge_import_aseprite` with a new request ID,
   the same source `library_id`, `document_id`, and optional `title`.
   Inspect final QA/review. The final deliverable is a new Library sprite.

To reuse an approved reference, specify `source_library_id` and
`source_asset="sprite.png"` or `"gpt_painting.png"`. The latter refers only to
that item's `work/gpt_painting.png`. The Mac must be awake and connected.
GPT uses its existing Codex login/allowance; repair uses Claude's login/allowance.
The neural fixer uses the existing RD key integration and spends no RD balance.

No arbitrary paths, URLs, outputs, paid generation routes, shell commands,
provider/model selection, project registration, game writes or desktop-final
exports are accepted by these tools. Reference/layer files reject symlinks and
hardlinks. Existing images/documents are preserved. Narrow Aseprite import
rejects partial alpha and over-32-colour output before publishing; those require
explicit pixel edits or exact direct-authoring work, not silent normalization.

## Ordered animation clips

Call `forge_clip` with a new stable `request_id` and `source_library_ids` in exact
playback order, including any intentional repeats. Each ID resolves only to its
existing `sprite.png`; filenames, directories, URLs and output paths are refused.
The worker checks the same contained, non-symlink/non-hardlink files again before
importing. Source art remains unchanged and the result gets a new `library_id`.

Optional arguments are `width`, `height`, `fps` (default 10), `loop`
(`forward`, `reverse`, `pingpong`), `tag` (default `clip`) and `palette` (1–62
opaque RGB hex colours). Without a palette, the clip preserves exact visible
RGBA colours within the shared 62-colour limit. Alpha is retained in either
mode. No cleanup, background removal or per-frame fitting occurs. A fixed palette
is explicit; this restricted tool does not accept profile paths or provider options.

Poll `forge_workflow_status` with the same request ID. Completed status contains
the transform, source hashes and IDs, per-frame bounds, endpoint warnings,
export-verification results and a 2x contact sheet. Inspect its 2x and 1x reads;
`awaiting_visual_review` remains pending regardless of numeric QA. The new library
folder holds `frames/f0.png` onward, `sheet.png`, `sheet@2x.png`, `sheet.json`,
`manifest.json` and `clip-report.json`. Local CLI/full-MCP consumers can use those
exports; restricted tools return no absolute filesystem paths and retain their
existing asset-access restrictions. This is an ordered-PNG importer, not a video
decoder, animation generator, GIF exporter or multi-frame Aseprite editor.

Bounds: 1–256 source IDs, existing PNG read limits (10 MiB and 1,048,576 pixels per
frame), at most 128 MiB and 67,108,864 input pixels across the clip, output canvas
sides at most 512, and at most 8,388,608 pixels for the 2x sheet. Oversized sheets
are rejected before launching a job. Use fewer frames or a smaller canvas.

## Jobs and retries

Starting a job returns immediately. Request IDs deduplicate durably across tool
calls and runtime restarts. The same request with changed arguments returns
`REQUEST_CONFLICT`. A single active workflow job is admitted; others return
`BUSY`, with no queue or extra provider calls. State is private under
`library/.workflow-jobs/`. Interruptions and failures do not automatically
replay. Inspect retained work before intentionally using a new request ID.
A 30-second reservation timeout clears launches that never reached a worker.
Workers have a 30-minute process-group deadline; individual CLI/API timeouts are
shorter. Status reads never start a model or editor.

## Persistent update and verification

Retain alias `pixel-forge-readonly`, tunnel ID
`tunnel_6abeddf6f4388191bff193c5425eadec`, existing credential reference,
workspace/control-plane association and profile directory. Back up the current
profile and `finish.py`, then use the supported native `runtimes stop` and
`runtimes connect --tunnel-id ... --runtime-api-key file:... --mcp-command ...`
with the existing references. No new credentials or public ports are required.
Update the saved resume command as part of the approved action.

Verify native status healthy/ready and local stdio discovery (14 tools).
Then refresh the installed plugin's metadata:
https://chatgpt.com/plugins/plugin_asdk_app_6abee0fc99b88191a41160b938028b3a

Supported refresh: https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata

Dot must independently discover all 14 tools, including `forge_clip`, and call `forge_projects` or
`forge_status`. Local/runtime success alone does not establish Dot access.
No permission mode is changed implicitly. Revert the launch/profile/resume
command and refresh again to roll back; retain all assets and authentication.

## Evidence

- Full suite: `.venv/bin/python -m pytest -q tests`.
- Exact schemas: `artifacts/workflow-tool-schemas.json`.
- Existing connection observation: `artifacts/workflow-current-connection.json`.
- Free existing-reference job/dedup test: `artifacts/workflow-connection-smoke.json`.

The earlier 13-tool live switch is recorded by `artifacts/workflow-live-connection.json`: healthy,
ready, credential file unchanged and all other profile configuration unchanged.
`artifacts/workflow-live-tools.json` verifies configured discovery. Plugin Refresh
and parent Dot discovery for the 14-tool clip expansion must be verified after deployment.
