# Narrow workflow connection

Current live launch, activated after Mark explicitly approved the exact expansion:

```bash
/Users/markrogers/code/pixel-forge/forge.sh mcp-workflow --enable-authoring --enable-workflow
```

Existing eight tools remain: `forge_status`, `forge_projects`,
`forge_project_assets`, `forge_library`, `forge_preview`,
`forge_authoring_capabilities`, `forge_render_sprite`, `forge_edit_sprite`.

Five additions:

| Tool | What Dot does |
|---|---|
| `forge_prepare` | Start one GPT reference → free RD neural fixer → cleanup/QA. An approved Library image can skip GPT. |
| `forge_workflow_status` | Read progress/result using the same `request_id`; completed preparation/repair/import returns a review image. |
| `forge_repair` | Request one focused Claude CLI Opus patch after finding a concrete problem. |
| `forge_open_aseprite` | Create and open a new layered document; get its `document_id` from status. |
| `forge_import_aseprite` | Import that document into a new Library item with QA and another visual-review sheet. |

Workflow writes declare readOnly=false, destructive=false, idempotent=true.
Prepare/repair declare openWorld=true because they use providers; Aseprite tools
declare openWorld=false. Progress is read-only. Workflow and direct drawing have
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

Verify native status healthy/ready and local stdio discovery (13 tools).
Then refresh the installed plugin's metadata:
https://chatgpt.com/plugins/plugin_asdk_app_6abee0fc99b88191a41160b938028b3a

Supported refresh: https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata

Dot must independently discover all 13 tools and call `forge_projects` or
`forge_status`. Local/runtime success alone does not establish Dot access.
No permission mode is changed implicitly. Revert the launch/profile/resume
command and refresh again to roll back; retain all assets and authentication.

## Evidence

- Full suite: `.venv/bin/python -m pytest -q tests`.
- Exact schemas: `artifacts/workflow-tool-schemas.json`.
- Existing connection observation: `artifacts/workflow-current-connection.json`.
- Free existing-reference job/dedup test: `artifacts/workflow-connection-smoke.json`.

The live switch is confirmed by `artifacts/workflow-live-connection.json`: healthy,
ready, credential file unchanged and all other profile configuration unchanged.
`artifacts/workflow-live-tools.json` verifies configured discovery. Plugin Refresh
and parent Dot discovery remain unverified until performed in the parent chat.
