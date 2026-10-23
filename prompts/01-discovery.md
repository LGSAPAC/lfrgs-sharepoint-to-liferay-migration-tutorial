# Prompt: discovery

Paste this into Claude Code from the repo root, with the `sharepoint` and `liferay` MCP servers connected.

---

You are helping migrate a SharePoint Online site to Liferay DXP 2026.Q3 (new CMS). Use only **read**
tools. Don't call any Liferay tool that creates, updates or deletes.

1. With the **sharepoint** MCP server, call `inventory_site`.
2. For every custom list, call `get_list_schema`. Classify each column as:
   - *direct* (Text, Note, Number, Currency, DateTime, Boolean, URL),
   - *transformed* (Choice/MultiChoice → picklist, User → e-mail text, Lookup → relationship), or
   - *unsupported* (anything with `supported: false`).
3. Call `get_list_items` with `limit: 20` on each list. Report any Choice value that isn't in the
   column's `Choices` (fill-in values), and the maximum number of attachments per item.
4. Call `list_site_pages`, then `get_page_content` on the home page and two other representative pages.
   List every distinct web part type (`@odata.type`, `webPartType`, `data.title`).
5. Call `get_role_assignments` (scope web), `list_site_groups`, and `find_unique_permissions` for each list.
6. With the **liferay** MCP server, list the existing sites and asset libraries (spaces), and check
   whether the planned site key, space key, or any of the users' e-mail addresses already exist.

Write the result to `out/discovery.md` with these sections: Summary counts · Lists and columns ·
Pages and web parts · Users, groups and permissions · Collisions in target · Risks and decisions needed.
End with a draft `mapping.yaml` that applies your recommendations.
