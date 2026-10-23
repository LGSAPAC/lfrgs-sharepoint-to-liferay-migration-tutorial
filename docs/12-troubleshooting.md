# 12. Troubleshooting

## SharePoint side

| Symptom | Cause | Fix |
|---|---|---|
| `AADSTS7000218: request body must contain client_assertion or client_secret` | Public client flows disabled | App registration → Authentication → **Allow public client flows = Yes** |
| `AADSTS65001: consent required` | Admin consent not granted | Grant admin consent for all the permissions in Chapter 1 |
| `403` on `/_api/web/roleassignments` | Signed-in user isn't a site collection admin | Add the migration account as Site Collection Administrator |
| `401` on SharePoint REST with app-only | Token obtained with a client secret | Use a certificate (`SP_AUTH_MODE=certificate`) |
| MCP tool error *"No cached SharePoint token"* | The MCP server can't show a device-code prompt | Run `sp2lr login` in a terminal |
| Many `429` / long pauses | SharePoint throttling | Expected on large sites, since the client honours `Retry-After`. Run off-hours |
| Person column empty in Liferay | The user left the company (no Entra profile) | The e-mail is still stored as text. The account itself isn't created |
| Page has no `canvasLayout` | Classic or publishing page | Out of scope. Rebuild it, or export its HTML by hand |
| `Term set … could not be read` warning | `TermStore.Read.All` missing, or the token was cached before it was added | Grant it, delete `~/.sp2lr/token_cache.json`, run `sp2lr login`, re-extract. Without it, vocabularies are built flat from tagged values |
| Library files listed as `larger than N MB` | `--max-file-mb` limit | Re-extract with a higher limit, or move very large media to a DAM |

## Liferay side

| Symptom | Cause | Fix |
|---|---|---|
| `/o/mcp` returns `404` | MCP server not enabled for this virtual instance | Instance Settings → Platform → MCP Server → Enabled. Check flag `LPD-63311` |
| MCP `401 invalid_token` with OAuth | Token audience doesn't include `/o/mcp` | Request the token with `resource=<host>/o/mcp` |
| `ObjectDefinitionScopeException` | Content structure not depot-scoped | Leave `target: cms` (scope `depot`), or use `target: objects` |
| `ObjectDefinitionObjectFolderIdException` | Object folder not a CMS folder | `objectFolderExternalReferenceCode` must be `L_CMS_CONTENT_STRUCTURES` |
| `Duplicate field name` / `reserved name` | Column display name collides | Rename it in `mapping.yaml` (`skipFields`), or rely on the `sp` prefix |
| Entries created but invisible to members | Custom structures have VIEW disabled | Grant View on the structure ([Chapter 10](10-permissions.md)) |
| Picklist value rejected | Item value not in the column's `Choices` (SharePoint "fill-in" choices) | Add the value to the list type definition and re-run `entries` |
| `/api/jsonws/invoke` returns `403` | JSON-WS disabled or blocked by a Service Access Policy | Assign memberships in the UI, or allow `UserGroupService`/`UserGroupGroupRoleService` for the migration user |
| `413` on attachments | Upload limit | `dl.file.max.size` / `upload.servlet.request.impl.max.size` in `portal-ext.properties` |
| Page created but empty | Fragment key or editable ID differs in your build | See [VALIDATION-CHECKLIST.md](VALIDATION-CHECKLIST.md) items P1–P2 |
| Manual step "Create document type …" | The data-engine call was rejected | Files are already uploaded, with metadata in the description. Create the type in the UI and re-run with `libraries.metadata: documentType` after deleting those documents, or keep the description mode |
| Every record shows the migration user as creator | `audit.impersonate` is off, or `doAsUserId` is ignored by your build | The original values are in `spCreatedBy`/`spCreated`. See checklist L29 |
| Categories missing on CMS entries | Vocabulary created in the site, not the Global site | Use `taxonomy.scope: global`, or create the vocabulary in the Space |

## Resetting a sandbox

```bash
docker compose -f docker/docker-compose.yml down -v   # deletes the database volume
rm -rf out/state.json out/dry-run.jsonl
```
