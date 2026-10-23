# Prompt: post-migration validation

Run after `sp2lr load` and `sp2lr verify`. Use a read-only Liferay account in `LIFERAY_BASIC` if you can.

---

Validate the SharePoint → Liferay migration. Don't call any tool that writes to either system.
Read `out/plan.json` and `out/state.json` for the ID mappings.

**Lists:** for each list in the plan, pick 5 random SharePoint item IDs from
`out/extract/lists/<listId>.items.json`. Fetch each one live with the **sharepoint** MCP server
(`get_list_items`) and fetch the Liferay entry with the **liferay** MCP server by external reference
code `sp-item-<listId>-<itemId>` in the space from `state.space.id`. Compare every mapped field after
conversion. Check relationships too: the related entry's title must match the lookup's display value.

**Pages:** for the home page and 2 others, compare the SharePoint canvas (`get_page_content`) with the
Liferay site page (friendly URL from the plan): number of sections, columns per section, the text of
each text web part (ignore markup differences), image count, and that links point to `/web/<site>/…`.

**Permissions:** for 2 users in each SharePoint group, list their Liferay site roles and space roles
and compare them with the plan's `siteGrants`. For every entry in `plan.entryGrants`, read its
permissions in Liferay and confirm that only the listed principals' roles hold VIEW.

**Navigation:** compare `get_navigation` with the Liferay navigation menu "SharePoint Navigation".

Write `out/validation.md` with a table per area (check · expected · actual · pass/fail · evidence),
followed by a list of defects with a suggested fix for each (code change, `mapping.yaml` override or
manual step).
