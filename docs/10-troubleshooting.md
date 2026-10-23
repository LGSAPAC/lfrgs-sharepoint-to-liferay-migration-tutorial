# 10. Troubleshooting

## SharePoint side

| Symptom | Cause | Fix |
|---|---|---|
| `AADSTS7000218: request body must contain client_assertion or client_secret` | Public client flows disabled | App registration → Authentication → **Allow public client flows = Yes** |
| `AADSTS65001: consent required` | Admin consent not granted | Grant admin consent for the four permissions |
| `403` on `/_api/web/roleassignments` | Signed-in user isn't a site collection admin | Add the migration account as Site Collection Administrator |
| `401` on SharePoint REST with app-only | Token obtained with a client secret | Use a certificate (`SP_AUTH_MODE=certificate`) |
| MCP tool error *"No cached SharePoint token"* | The MCP server can't show a device-code prompt | Run `sp2lr login` in a terminal |
| Many `429` / long pauses | SharePoint throttling | Expected on large sites, since the client honours `Retry-After`. Run off-hours |
| Person column empty in Liferay | The user left the company (no Entra profile) | The e-mail is still stored as text. The account itself isn't created |
| Page has no `canvasLayout` | Classic or publishing page | Out of scope. Rebuild it, or export its HTML by hand |

## Liferay side

| Symptom | Cause | Fix |
|---|---|---|
| `/o/mcp` returns `404` | MCP server not enabled for this virtual instance | Instance Settings → Platform → MCP Server → Enabled. Check flag `LPD-63311` |
| MCP `401 invalid_token` with OAuth | Token audience doesn't include `/o/mcp` | Request the token with `resource=<host>/o/mcp` |
| `ObjectDefinitionScopeException` | Content structure not depot-scoped | Leave `target: cms` (scope `depot`), or use `target: objects` |
| `ObjectDefinitionObjectFolderIdException` | Object folder not a CMS folder | `objectFolderExternalReferenceCode` must be `L_CMS_CONTENT_STRUCTURES` |
| `Duplicate field name` / `reserved name` | Column display name collides | Rename it in `mapping.yaml` (`skipFields`), or rely on the `sp` prefix |
| Entries created but invisible to members | Custom structures have VIEW disabled | Grant View on the structure ([Chapter 8](08-permissions.md)) |
| Picklist value rejected | Item value not in the column's `Choices` (SharePoint "fill-in" choices) | Add the value to the list type definition and re-run `entries` |
| `/api/jsonws/invoke` returns `403` | JSON-WS disabled or blocked by a Service Access Policy | Assign memberships in the UI, or allow `UserGroupService`/`UserGroupGroupRoleService` for the migration user |
| `413` on attachments | Upload limit | `dl.file.max.size` / `upload.servlet.request.impl.max.size` in `portal-ext.properties` |
| Page created but empty | Fragment key or editable ID differs in your build | See [VALIDATION-CHECKLIST.md](VALIDATION-CHECKLIST.md) items P1–P2 |

## Resetting a sandbox

```bash
docker compose -f docker/docker-compose.yml down -v   # deletes the database volume
rm -rf out/state.json out/dry-run.jsonl
```
