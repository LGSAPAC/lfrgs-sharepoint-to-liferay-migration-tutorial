"""SharePoint Online MCP server (read-only, stdio).

Gives an AI client (Claude Code, Claude Desktop, Cursor...) the same view of the
source site that the migrator uses, so discovery, mapping decisions and
post-migration validation can be done conversationally. It never writes to
SharePoint.

Run:  sharepoint-mcp            (reads settings from .env / environment)
"""

from __future__ import annotations

import json
from typing import Any

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

from .auth import TokenProvider
from .config import sharepoint_settings
from .sharepoint import SharePointClient, SUPPORTED_FIELD_TYPES, parse_login

mcp = MCPServer(
    "sharepoint",
    instructions="Read-only access to the SharePoint Online site being migrated to Liferay. "
                 "Start with inventory_site.",
)
_client: SharePointClient | None = None


def sp() -> SharePointClient:
    global _client
    if _client is None:
        settings = sharepoint_settings()
        _client = SharePointClient(settings, TokenProvider(settings, interactive=False))
    return _client


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
def get_site() -> str:
    """Return the configured source site: Graph site resource plus web properties
    (title, language, welcome page, whether permissions are unique)."""
    return _dump({"graph": sp().site(), "web": sp().web()})


@mcp.tool()
def search_sites(query: str = "*") -> str:
    """Search SharePoint sites in the tenant by keyword ('*' lists all you can see)."""
    return _dump([
        {"id": s["id"], "name": s.get("displayName"), "webUrl": s.get("webUrl")}
        for s in sp().search_sites(query)
    ])


@mcp.tool()
def inventory_site() -> str:
    """One-call inventory of the source site: counts of pages, lists, items, groups,
    users, plus anything that needs a migration decision (unsupported column types,
    unique permissions, document libraries)."""
    c = sp()
    lists = c.list_lists()
    custom = [l for l in lists if l["BaseTemplate"] == 100]
    findings: list[str] = []
    list_summ = []
    for l in custom:
        fields = c.list_fields(l["Id"])
        unsupported = [f"{f['InternalName']} ({f['TypeAsString']})" for f in fields
                       if f["TypeAsString"] not in SUPPORTED_FIELD_TYPES]
        if unsupported:
            findings.append(f"List '{l['Title']}' has unsupported columns: {', '.join(unsupported)}")
        if l.get("HasUniqueRoleAssignments"):
            findings.append(f"List '{l['Title']}' has unique (broken-inheritance) permissions")
        list_summ.append({"title": l["Title"], "id": l["Id"], "items": l["ItemCount"],
                          "columns": len(fields), "uniquePermissions": l.get("HasUniqueRoleAssignments")})
    libraries = [l["Title"] for l in lists if l["BaseTemplate"] == 101]
    if libraries:
        findings.append(f"Document libraries are not migrated by this tutorial: {', '.join(libraries)}")
    other = [f"{l['Title']} (template {l['BaseTemplate']})" for l in lists if l["BaseTemplate"] not in (100, 101, 119)]
    if other:
        findings.append(f"Non-custom lists (tasks, calendars, ...) need review: {', '.join(other)}")
    pages = c.list_pages()
    groups = c.site_groups()
    users = [u for u in c.site_users() if u["PrincipalType"] == 1 and u.get("Email")]
    return _dump({
        "web": c.web(),
        "counts": {"pages": len(pages), "customLists": len(custom),
                   "listItems": sum(l["ItemCount"] for l in custom),
                   "sharePointGroups": len(groups), "users": len(users)},
        "lists": list_summ,
        "pages": [{"title": p.get("title"), "name": p.get("name"), "layout": p.get("pageLayout")} for p in pages],
        "groups": [{"title": g["Title"], "members": len(g.get("Users", [])), "associatedAs": g.get("associatedAs")} for g in groups],
        "findings": findings,
    })


@mcp.tool()
def list_site_pages() -> str:
    """List modern site pages (id, name, title, layout, promotion kind, dates)."""
    return _dump(sp().list_pages())


@mcp.tool()
def get_page_content(page_id: str) -> str:
    """Return a modern page including its canvasLayout: horizontal sections ->
    columns -> web parts (text web parts carry innerHtml)."""
    return _dump(sp().get_page(page_id))


@mcp.tool()
def get_navigation() -> str:
    """Return Quick Launch and Top Navigation nodes (with children)."""
    return _dump(sp().navigation())


@mcp.tool()
def list_lists(include_hidden: bool = False) -> str:
    """List the site's lists and libraries with template, item count and whether
    they have unique permissions. BaseTemplate 100 = custom list, 101 = library."""
    return _dump(sp().list_lists(include_hidden))


@mcp.tool()
def get_list_schema(list_id: str) -> str:
    """Return migratable columns of a list (internal name, type, required, choices,
    lookup target...) and flag column types the migrator does not support."""
    fields = sp().list_fields(list_id)
    for f in fields:
        f["supported"] = f["TypeAsString"] in SUPPORTED_FIELD_TYPES
    return _dump(fields)


@mcp.tool()
def get_list_items(list_id: str, limit: int = 50) -> str:
    """Return up to `limit` items of a list with person and lookup columns expanded."""
    c = sp()
    fields = c.list_fields(list_id)
    out = []
    for item in c.list_items(list_id, fields, top=min(limit, 500)):
        out.append(item)
        if len(out) >= limit:
            break
    return _dump(out)


@mcp.tool()
def list_site_groups() -> str:
    """Return SharePoint groups with their members, marking the associated
    Owners / Members / Visitors groups."""
    return _dump(sp().site_groups())


@mcp.tool()
def list_site_users() -> str:
    """Return site users (PrincipalType 1 = user, 4 = security group, 8 = SharePoint group)
    with decoded claims login (user UPN / M365 group / Entra security group)."""
    users = sp().site_users()
    for u in users:
        u["decoded"] = parse_login(u.get("LoginName", ""))
    return _dump(users)


@mcp.tool()
def get_entra_user(upn_or_id: str) -> str:
    """Look up an Entra ID user profile (used to build Liferay user accounts)."""
    return _dump(sp().entra_user(upn_or_id))


@mcp.tool()
def get_role_assignments(scope: str = "web", list_id: str = "", item_id: int = 0) -> str:
    """Return role assignments (principal + permission levels).
    scope = 'web' (site), 'list' (needs list_id) or 'item' (needs list_id and item_id)."""
    return _dump(sp().role_assignments(scope, list_id or None, item_id or None))


@mcp.tool()
def list_role_definitions() -> str:
    """Return the site's permission levels (Full Control, Edit, Contribute, Read, custom...)."""
    return _dump(sp().role_definitions())


@mcp.tool()
def find_unique_permissions(list_id: str) -> str:
    """Return the IDs of list items that break permission inheritance."""
    return _dump(sp().items_with_unique_permissions(list_id))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
