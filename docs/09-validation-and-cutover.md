# 9. Validation and cutover

## 9.1 Automated reconciliation

```bash
sp2lr verify
```

`out/verify.md`:

```
| Area                                         | SharePoint | Liferay | OK  |
|----------------------------------------------|-----------:|--------:|-----|
| Users                                        |         57 |      57 | yes |
| User groups                                  |          4 |       4 | yes |
| List 'Product Catalog' -> ProductCatalog     |        412 |     412 | yes |
| List 'Support Requests' -> SupportRequests   |       1893 |    1893 | yes |
| Pages                                        |         18 |      18 | yes |

## Manual follow-ups
- [ ] Connect Space 'Marketing Content' to site 'Marketing' …
- [ ] Grant VIEW on structure 'Product Catalog' …
- [ ] Page Home.aspx: rebuild web part 'Quick links' …
```

Every API call that failed is listed under *Errors during load*, and `out/state.json` keeps the full
list. Fix the cause, then re-run only the affected phase. All phases are idempotent.

## 9.2 Agent-assisted validation (both MCP servers)

Count reconciliation proves completeness, not fidelity. Use
[`prompts/03-validate.md`](../prompts/03-validate.md) to have the agent sample and compare. It:

1. Picks 5 random items per list with the **sharepoint** MCP server (`get_list_items`).
2. Fetches the same entries by ERC `sp-item-<listId>-<itemId>` with the **liferay** MCP server.
3. Compares every mapped field using the conversion rules from [Chapter 4](04-mapping-and-plan.md#42-field-mapping).
4. Compares the 3 most-visited pages, section by section.
5. Checks permissions for 2 users per SharePoint group by listing their roles in the site and the Space.
6. Writes `out/validation.md` with pass/fail per check and the evidence.

Keep the validation session **read-only**: run it with a Liferay account that has view permissions
only, or tell the agent not to call any POST, PUT, PATCH or DELETE tools.

## 9.3 User acceptance

| Who | Checks |
|---|---|
| Site owner | Page content and layout, navigation, rebuilt web parts |
| List owners | Entry counts, a few records end to end, attachments open |
| A Read-only user | Can view, cannot edit |
| A restricted user | Cannot see the entries they couldn't see in SharePoint |
| Search | CMS search and site search find migrated entries and pages |

## 9.4 Delta runs and cutover

SharePoint stays live during the project, so plan for at least one delta:

```bash
sp2lr extract          # fresh snapshot
sp2lr plan
sp2lr load --phase users --phase groups --phase structures --phase entries --phase permissions
sp2lr verify
```

* **New and changed items** are upserted by ERC (`PUT …/by-external-reference-code/…`), so edits in
  SharePoint overwrite the Liferay copy. Freeze editing in Liferay until cutover.
* **Deleted items** aren't removed automatically. Compare `out/state.json` entry IDs with the new
  extract and delete the orphans deliberately.
* **New pages** are created. **Changed pages** are skipped, because pages may have been rebuilt by
  hand in Liferay. Delete and re-run a page only if it hasn't been edited.

Cutover runbook:

1. Announce a content freeze in SharePoint, for example by making the site read-only in the
   SharePoint admin center (*Active sites → Site → Settings → Lock state → Read-only*).
2. Run the final delta and `sp2lr verify`.
3. Close every manual follow-up in `verify.md`.
4. Switch navigation or DNS to Liferay. Optionally, add a SharePoint banner that links to the new site.
5. Keep the SharePoint site read-only for a retention period, then archive it.
6. Disable the migration account in Liferay. Revoke the Entra app's consent, or remove the app.

Next: [10. Troubleshooting](10-troubleshooting.md)
