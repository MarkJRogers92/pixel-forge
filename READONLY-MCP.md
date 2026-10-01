# Read-only Pixel Forge MCP

Run `/Users/markrogers/code/pixel-forge/forge.sh mcp-readonly` over stdio, with
working directory `/Users/markrogers/code/pixel-forge`. The original `mcp`
server and all existing CLI commands remain available.

The restricted server exposes exactly:

| Tool | Inputs and behavior |
|---|---|
| `forge_status` | No inputs; approved projects, profile summaries, ten recent items |
| `forge_projects` | No inputs; approved registered IDs/names |
| `forge_project_assets` | Registered `project=dead-mall`, optional substring, limit 1–100 |
| `forge_library` | Limit 1–100; fixed metadata fields and final PNG asset name |
| `forge_preview` | Source `library`/`project`, listed ID, relative PNG asset, scale 1/2/8; inline PNG |

For library previews, use the item ID and `asset=sprite.png`. For project previews,
use `id=dead-mall` and a relative path returned by project-assets listing.

Reads are scoped to this repo's project registry, profiles and library, and the
existing game art directory `../90s/public/assets/neon`. Registry entries must
match that approved root. Paths cannot be absolute or traverse directories;
symlinks, non-PNG previews, oversized metadata/images, and excessive list limits
are rejected. PNG previews use nearest-neighbour resizing in memory. There are
no file-write, process-launch, network, generation, key-probing or editor tools.
Tool annotations are read-only hints; the separate tool registration and path
guards enforce the boundary.

Optional separate local Codex registration:

```bash
codex mcp add pixel-forge-readonly -- /Users/markrogers/code/pixel-forge/forge.sh mcp-readonly
```

The launcher changes to its own working directory. For desktop Work, add a separate MCP server with the same command, argument
`mcp-readonly`, and working directory. Preserve the existing full connection.
Saved local registration does not prove the tools are loaded in an existing
conversation. Verify status and an existing preview from each target surface.

## Private connection for a dot

Use the [official Secure MCP Tunnel workflow](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels),
targeting **this restricted command**. A dot connection needs its own verification;
a local Work/Codex test is not sufficient.

Before proceeding, verify access in [Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels).
Installation, tunnel creation/organization and workspace associations, runtime
credentials and ongoing access require Mark's separate action-time approval.
Mark must create and enter credentials himself. Never put keys in chats,
commits, command-line arguments or logs. Do not expose public ports.

After approval, obtain the official client from the download link in Platform
tunnel settings. Follow its installed `help quickstart` for a named stdio profile
whose MCP command is `/Users/markrogers/code/pixel-forge/forge.sh mcp-readonly`.
Use `doctor --profile <name> --explain` before running the profile. Keep the local
admin UI on loopback. Do not run the existing full `mcp` server through this tunnel.

In [ChatGPT Plugins](https://chatgpt.com/plugins), use Add → Create MCP App →
Connection → Tunnel. Choose the approved tunnel associated with the target
workspace. Verify the exact five-tool list, status, library and existing-image
preview from the dot itself, then independently in desktop Work and Codex.
Attempting `forge_make` must return an unknown-tool error. No generation is
needed for this check. Stop the client or revoke access to disable the connection.

Never install an autostart service or save credentials for ongoing operation
without approval covering that persistent access and storage.
