# Prompt: plan review

Run after `sp2lr plan`.

---

Review `out/plan.json` and `out/plan.md` for the SharePoint → Liferay 2026.Q3 migration. Read-only:
don't change Liferay.

1. For each list in the plan, use the **sharepoint** MCP server (`get_list_items`, limit 50) to sample
   items. Convert each one with the rules in `docs/04-mapping-and-plan.md` and confirm that every value
   fits its target field: picklist keys exist, numbers fit LongInteger vs Decimal, dates parse, and
   Text values stay under 280 characters (otherwise flag them for LongText).
2. Check the field names for collisions and unclear names. Suggest `mapping.yaml` overrides where a
   name would confuse editors.
3. Compare `siteGrants` with `get_role_assignments` (web). Confirm that every non-Limited-Access
   assignment is represented and that the role mapping matches `mapping.yaml`.
4. With the **liferay** MCP server, check that the role names the plan references exist (Site Owner,
   Site Content Reviewer, Asset Library Owner, Asset Library Administrator, Asset Library Member), and
   that no object definition with the same ERC or name already exists.
5. Report blocking issues first, then warnings, then the exact `mapping.yaml` changes to make.
