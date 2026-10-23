# 2. MCP servers

The migration uses one MCP server per side:

| | SharePoint MCP server | Liferay MCP server |
|---|---|---|
| Where it runs | Locally, from this repo (`sharepoint-mcp`, stdio) | Inside Liferay DXP 2026.Q3 (`/o/mcp`, Streamable HTTP) |
| Access | **Read-only** | Read/write, as the authenticated Liferay user |
| Backed by | Microsoft Graph + SharePoint REST | Liferay headless REST APIs |
| Used for | Inventory, schema and permission analysis, sampling source data | Inspecting the target, spot fixes, validating migrated content |

> **Why a custom SharePoint server?** Microsoft's *Work IQ SharePoint* MCP server (Agent 365 tooling,
> preview) requires a Microsoft 365 Copilot license and focuses on files, lists and items. It doesn't
> expose **role assignments**, SharePoint groups or the modern page **canvas layout**, and a migration
> needs all three. Community servers have the same gaps. The server in this repo is small (about 200
> lines), read-only and auditable. If your tenant already allows Work IQ, add it alongside this one for
> ad-hoc questions.

## 2.1 Enable the Liferay MCP server

1. The flags `feature.flag.LPD-63311=true` (MCP server) and `feature.flag.LPD-63415=true` (OAuth 2.0
   Authorization Server Metadata) are already set in `docker/liferay/files/portal-ext.properties`. On
   another installation, set them in *Control Panel → Instance Settings → Platform → Feature Flags*
   (Release) or in `portal-ext.properties`.
2. Go to **Control Panel → Instance Settings → Platform → MCP Server**, check **Enabled** and click
   **Save**. The setting applies per virtual instance and is off by default. While it is disabled,
   `/o/mcp` returns `404`.
3. Test it:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/o/mcp            # 401 (needs auth)
   curl -s -u test@liferay.com:learn -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://localhost:8080/o/mcp | head -c 600
   ```

The server acts **as the authenticated user** with that user's permissions. Use the migration admin
for the migration itself and a lower-privileged account for read-only validation sessions.

GET tools accept a `fields` parameter, as the headless REST APIs do. Use it in prompts, for example
"only return id, title and dateModified", so large payloads stay out of the model's context.

### OAuth 2.0 (recommended outside a sandbox) <a id="oauth"></a>

1. **Control Panel → Security → OAuth 2 Administration → Add**, with client profile *Native
   Application* (for interactive clients) or *Headless Server* (for the CLI).
2. Request tokens with `resource=http://<host>/o/mcp`. Liferay rejects MCP tokens whose audience
   doesn't include the MCP resource URI, even when they work for the REST APIs.
3. MCP clients that support discovery find the authorization server on their own: an unauthenticated
   request returns `401` with a `WWW-Authenticate` header whose `resource_metadata` points to
   `/o/.well-known/oauth-protected-resource/mcp`.

## 2.2 Run the SharePoint MCP server

Sign in once in a terminal. The MCP server is non-interactive and reuses the cached token from
`~/.sp2lr/token_cache.json`:

```bash
sp2lr login
# To sign in, use a web browser to open https://microsoft.com/devicelogin and enter the code ABCD1234...
# Signed in. Source site: Marketing (https://contoso.sharepoint.com/sites/marketing)
```

Smoke-test it with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector .venv/bin/sharepoint-mcp
```

Tools exposed:

| Tool | Returns |
|---|---|
| `inventory_site` | Counts and findings for the whole site. **Start here.** |
| `get_site`, `search_sites` | Site and web properties, tenant site search |
| `list_site_pages`, `get_page_content`, `get_navigation` | Modern pages, canvas layout (sections → columns → web parts), Quick Launch and Top nav |
| `list_lists`, `get_list_schema`, `get_list_items` | Lists, migratable columns with a `supported` flag, sample items |
| `get_library_contents` | Folders and files of a document library, with metadata, author/editor and unique-permission flags |
| `get_term_set` | A managed-metadata term set with its full term hierarchy |
| `list_site_groups`, `list_site_users`, `get_entra_user` | SharePoint groups and members, decoded claims logins, Entra profiles |
| `list_role_definitions`, `get_role_assignments`, `find_unique_permissions` | Permission levels, web/list/item role assignments, items that break inheritance |

## 2.3 Wire both into Claude Code

[`.mcp.json`](../.mcp.json) at the repo root defines both servers:

```json
{
  "mcpServers": {
    "sharepoint": { "type": "stdio", "command": ".venv/bin/sharepoint-mcp", "args": [] },
    "liferay": {
      "type": "http",
      "url": "${LIFERAY_URL:-http://localhost:8080}/o/mcp",
      "headers": { "Authorization": "Basic ${LIFERAY_BASIC}" }
    }
  }
}
```

Claude Code expands `${VAR}` from the **shell environment** and doesn't read `.env`, so export it first:

```bash
set -a; source .env; set +a
claude
```

Then run `/mcp`. Both `sharepoint` and `liferay` should show as connected. Approve the project servers
when prompted.

### Other clients

<details><summary>Claude Desktop</summary>

`claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "sharepoint": {
      "command": "/absolute/path/to/repo/.venv/bin/sharepoint-mcp",
      "env": { "SP_TENANT_ID": "...", "SP_CLIENT_ID": "...", "SP_SITE_URL": "https://contoso.sharepoint.com/sites/marketing" }
    },
    "liferay": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:8080/o/mcp", "--header", "Authorization: Basic <LIFERAY_BASIC>"]
    }
  }
}
```
</details>

<details><summary>Cursor / VS Code (GitHub Copilot)</summary>

Cursor `~/.cursor/mcp.json`:

```json
{ "mcpServers": {
    "liferay": { "url": "http://localhost:8080/o/mcp", "type": "http",
                 "headers": { "Authorization": "Basic <LIFERAY_BASIC>", "Content-Type": "application/json" } },
    "sharepoint": { "command": "/absolute/path/to/repo/.venv/bin/sharepoint-mcp" } } }
```

In VS Code `.vscode/mcp.json`, use the top-level key `servers` instead of `mcpServers`.
</details>

## 2.4 First conversation

Ask the agent:

> Use the sharepoint MCP server to run `inventory_site`, then use the liferay MCP server to list the
> sites and spaces (asset libraries) that already exist. Tell me whether anything in the target would
> collide with the source: same site friendly URL, same space key, or existing users with the same
> e-mails.

Next: [3. Discovery and inventory](03-discovery.md)
