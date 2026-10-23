# Validation checklist

This tutorial was written against the Liferay DXP 2026.Q3 documentation and Microsoft Graph v1.0.
The calls below are either undocumented or documented only in pieces for the new CMS. Confirm each
one against a real 2026.Q3 instance and a real tenant, then tick it, or fix the code and the chapter
that references it.

Use the API Explorer (`http://localhost:8080/o/api`) and the Liferay MCP server's tool list
(`tools/list`) as the source of truth.

**Status:** ✅ confirmed · ⚠️ works with a change · ❌ broken · ☐ not yet tested

## Environment

| # | Check | How | Status |
|---|---|---|---|
| E1 | Docker tag `liferay/dxp:2026.q3.0` exists and starts with PostgreSQL | `docker compose up`, look for "Server startup in" | ☐ |
| E2 | `feature.flag.LPD-63311` makes *Instance Settings → Platform → MCP Server* appear | UI | ☐ |
| E3 | `/o/mcp` answers `tools/list` with Basic auth | curl in [Chapter 2](02-mcp-servers.md#21-enable-the-liferay-mcp-server) | ☐ |
| E4 | Claude Code connects to both servers from `.mcp.json` | `/mcp` | ☐ |

## SharePoint (source)

| # | Check | Code | Status |
|---|---|---|---|
| S1 | Device-code sign-in with delegated `AllSites.FullControl` returns role assignments | `sharepoint.py: role_assignments` | ☐ |
| S2 | `pages/microsoft.graph.sitePage?$expand=canvasLayout` returns `columns[].width` and `webparts[]` | `sharepoint.py: get_page` | ☐ |
| S3 | Image web part exposes `data.serverProcessedContent.imageSources[].value` | `transform.py: find_page_image_urls` | ☐ |
| S4 | SP REST items with `$expand` of person/lookup columns page correctly via `odata.nextLink` on lists over 5,000 items | `sharepoint.py: list_items` | ☐ |
| S5 | `MultiChoice` values come back as a JSON array with `odata=nometadata` | `transform.py: convert_value` | ☐ |
| S6 | `/web/AssociatedOwnerGroup` etc. resolve for group-connected (M365) team sites | `sharepoint.py: site_groups` | ☐ |

## Liferay (target)

| # | Check | Code | Status |
|---|---|---|---|
| L1 | `POST /o/headless-site/v1.0/sites` with `templateKey: com.liferay.site.initializer.blank` creates a site | `liferay.py: create_site` (falls back to a manual step) | ☐ |
| L2 | `PUT /o/headless-asset-library/v1.0/asset-libraries/by-external-reference-code/{erc}` with `"type": "Space"` creates a CMS Space | `liferay.py: upsert_space` | ☐ |
| L3 | The asset library `id` is the depot **groupId** (used for JSON-WS membership and `/scopes/{id}`) | `load.py: phase_groups, _scope_path` | ☐ |
| L4 | Headless or JSON-WS operation to **connect a Space to a site** (the `connect-sites` action) | currently a manual step | ☐ |
| L5 | `PUT /o/headless-admin-user/v1.0/user-accounts/by-external-reference-code/{erc}` creates users without a password | `liferay.py: upsert_user` | ☐ |
| L6 | `POST /o/headless-admin-user/v1.0/roles` with `roleType: "depot"` creates a space role (or which `roleType` value?) | `liferay.py: ensure_role` | ☐ |
| L7 | Default space role names: *Asset Library Owner / Administrator / Member / Connected Site Member* | `transform.py: DEFAULT_LEVEL_MAP` | ☐ |
| L8 | JSON-WS `/usergroup/add-group-user-groups`, `/usergroupgrouprole/add-user-group-group-roles`, `/user/add-group-users` (with `serviceContext: {}`), `/usergrouprole/add-user-group-roles` work through `/api/jsonws/invoke` | `load.py: _grant` | ☐ |
| L9 | Object definition with `scope: depot` + `objectFolderExternalReferenceCode: L_CMS_CONTENT_STRUCTURES` shows up as a CMS content structure | `transform.py: plan_list` | ☐ |
| L10 | Including our own `title` field doesn't clash with a Title field the CMS adds automatically | `transform.py: plan_list` | ☐ |
| L11 | Entry upsert `PUT /o/c/{plural}/scopes/{spaceId}/by-external-reference-code/{erc}` works for depot scope | `load.py: phase_entries` | ☐ |
| L12 | Attachment field accepts `{"name", "fileBase64"}` | `load.py: phase_entries` | ☐ |
| L13 | One-to-many relationship field name is `r_{relationship}_c_{object}Id` (the loader reads it from the definition, with that as fallback) | `load.py: _relationship_fields` | ☐ |
| L14 | `PUT /o/c/{plural}/{id}/permissions` accepts `[{"roleName", "actionIds"}]` and an empty `actionIds` removes inherited VIEW | `load.py: phase_permissions` | ☐ |
| L15 | `PUT /o/c/{plural}/permissions` grants `ADD_OBJECT_ENTRY` for depot-scoped structures | `load.py: phase_permissions` (falls back to a manual step) | ☐ |
| L16 | Granting VIEW on a content structure through the API (to remove the manual step) | currently a manual step | ☐ |
| L17 | One-to-many relationship between a site-scoped Object and a depot-scoped CMS structure is accepted | `load.py: phase_structures` | ☐ |
| L18 | `POST /o/object-admin/v1.0/object-definitions/{id}/object-views` with `objectViewColumns`/`objectViewSortColumns`; system fields `dateCreated`, `creator`, `id` allowed as columns | `load.py: _object_views` | ☐ |
| L19 | Field settings `uniqueValues`, `defaultValueType`/`defaultValue` accepted on object fields (and whether CMS structures accept them too) | `transform.py: field_extras` | ☐ |
| L20 | `panelCategoryKey: site_administration.content` puts site-scoped Objects in the site menu | `transform.py: plan_list` | ☐ |
| L21 | Which classic Object features (views, validations, actions, state manager) also work on CMS content structures in 2026.Q3 | docs/06 table | ☐ |
| L22 | SP REST `/views?$expand=ViewFields` returns `ViewFields.Items` with `odata=nometadata` | `sharepoint.py: list_views` | ☐ |

## Pages

| # | Check | Code | Status |
|---|---|---|---|
| P1 | Fragment keys `BASIC_COMPONENT-heading` / `BASIC_COMPONENT-paragraph` with editable ID `element-text` | `transform.py: _fragment` | ☐ |
| P2 | `fragmentFields[].value.text.value_i18n` renders HTML (links and `<img>`) in a paragraph | `transform.py: _fragment` | ☐ |
| P3 | `POST /o/headless-delivery/v1.0/sites/{id}/site-pages` accepts `externalReferenceCode` and `pageSettings.seoSettings` | `liferay.py: create_site_page` | ☐ |
| P4 | `GET …/site-pages/{friendlyUrlPath}` returns `404` for a missing page (idempotency check) | `liferay.py: site_page` | ☐ |
| P5 | Document `contentUrl` works as an `<img src>` for site members | `load.py: phase_pages` | ☐ |
| P6 | Navigation menu items of `type: "url"` with nested `navigationMenuItems` | `liferay.py: upsert_navigation_menu` | ☐ |

## End-to-end scenario

Seed a test SharePoint site with:

* 2 custom lists, one per scenario:
  * *Product Catalog* (text, currency, choice, date, rich text, attachment), which should auto-classify as **CMS**
  * *Orders* (lookup to products, person "Owner", number, multi-choice, yes/no, choice "Order Status"
    with a default value, two views, one with a filter), which should auto-classify as **Objects**
* 1 item in *Orders* with unique permissions (Owners only)
* 3 pages: Home (two columns, text + Quick links), About Us (image), News article
* Owners / Members / Visitors groups, with one Entra security group nested in Members

Then run chapters 3–9 and record the results here:

| Step | Result | Notes |
|---|---|---|
| extract | ☐ | |
| plan | ☐ | |
| load --dry-run | ☐ | |
| load | ☐ | |
| verify | ☐ | |
| prompts/03-validate.md | ☐ | |
