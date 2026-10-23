# 3. Discovery and inventory

Before moving anything, build an accurate picture of the live site. Do it twice: once
conversationally through the SharePoint MCP server, and once deterministically with `sp2lr extract`.

## 3.1 Agent-assisted inventory

In Claude Code, with both servers connected, run the prompt in
[`prompts/01-discovery.md`](../prompts/01-discovery.md). It asks the agent to:

1. Call `inventory_site` for counts and findings.
2. Call `get_list_schema` for every custom list and classify columns as *direct*, *transformed* (choice →
   picklist, person → e-mail, lookup → relationship) or *unsupported* (calculated, managed metadata,
   location, image, thumbnail).
3. Call `get_page_content` on three representative pages, typically the home page, a news article and a
   landing page, and list every web-part type it finds.
4. Call `get_role_assignments` at web scope and `find_unique_permissions` per list.
5. Write `out/discovery.md` with a risk table.

Typical findings and what to do about them:

| Finding | Decision |
|---|---|
| Calculated or managed-metadata columns | Recompute in Liferay (object formula field or validation) or map to categories by hand |
| Lists over 5,000 items | Fine. The extractor pages through SharePoint REST (`$top=500` + `odata.nextLink`) |
| Hidden or system lists (`Hidden = true`) | Skipped by default |
| Document libraries (`BaseTemplate 101`) | Out of scope. Plan a Documents and Media migration |
| Highlighted Content, News, Events, SPFx web parts | Rebuild with Liferay fragments or Collection Display. Each one shows up in `plan.md` |
| "Everyone except external users" in permissions | Decide whether the Liferay site should be *open* or *restricted* |
| Guests (`#EXT#` in the UPN) | Migrated as regular users, so review their access |

## 3.2 Extract

```bash
sp2lr extract
```

```
INFO Site
INFO Users and groups
INFO Permissions
INFO Lists
INFO   Product Catalog: 412 items
INFO   Support Requests: 1893 items
INFO Pages
{ "users": 57, "groups": 4, "lists": 2, "items": 2305, "pages": 18, "uniquePermissionScopes": 6 }
```

Output layout:

```
out/extract/
  site.json             Graph site + web properties + navigation
  users.json            site users, Entra profiles, expanded Entra/M365 groups
  groups.json           SharePoint groups with members and Owners/Members/Visitors flags
  permissions.json      role definitions, web/list/item/page role assignments
  lists.json            list metadata + migratable field schema
  lists/<id>.items.json all items (person and lookup columns expanded)
  pages.json            modern pages with canvasLayout
  files/attachments/... list item attachments
  files/pages/...       images referenced by pages
  summary.json
```

The extract is a **point-in-time snapshot**. Everything after this step reads only from `out/`, so you
can re-plan and re-load without going back to SharePoint. For a delta run, re-run `sp2lr extract`.
See [Chapter 9](09-validation-and-cutover.md).

### How the extractor calls SharePoint

| Data | API | Call |
|---|---|---|
| Site ID | Graph | `GET /sites/{hostname}:/{path}` |
| Pages + canvas | Graph | `GET /sites/{id}/pages/microsoft.graph.sitePage`, then `/{pageId}/microsoft.graph.sitePage?$expand=canvasLayout` |
| Lists, fields | SP REST | `/_api/web/lists`, `/_api/web/lists(guid'…')/fields` (`TypeAsString`, `Choices`, `LookupList`…) |
| Items | SP REST | `/_api/web/lists(guid'…')/items?$select=…,Person/EMail&$expand=Person,Lookup` |
| Groups, users | SP REST | `/_api/web/sitegroups?$expand=Users`, `/_api/web/siteusers` |
| Permissions | SP REST | `/_api/web/roleassignments?$expand=Member,RoleDefinitionBindings` (also on lists and items) |
| Profiles | Graph | `GET /users/{upn}`, `GET /groups/{id}/transitiveMembers` |

The extractor uses SharePoint REST for lists because its field metadata (`TypeAsString`, `RichText`,
`DisplayFormat`, `LookupList`) is more precise than Graph's column definitions, and because only REST
exposes role assignments. Requests that get a `429` or `503` back are retried after the `Retry-After`
delay.

Next: [4. Mapping and plan](04-mapping-and-plan.md)
