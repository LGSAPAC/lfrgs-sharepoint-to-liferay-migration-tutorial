"""Phase 1: extract the live SharePoint site to JSON files under out/extract/."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from .sharepoint import SharePointClient, parse_login

log = logging.getLogger(__name__)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def extract(sp: SharePointClient, out: Path, with_attachments: bool = True) -> dict:
    root = out / "extract"
    summary: dict = {}

    log.info("Site")
    site = {"graph": sp.site(), "web": sp.web(), "navigation": sp.navigation()}
    _write(root / "site.json", site)

    log.info("Users and groups")
    users = sp.site_users()
    groups = sp.site_groups()
    profiles: dict[str, dict] = {}
    expanded_groups: dict[str, list[dict]] = {}

    def add_profile(upn: str) -> None:
        key = upn.lower()
        if key in profiles:
            return
        prof = sp.entra_user(upn)
        if prof:
            profiles[key] = prof
        else:
            log.warning("No Entra profile for %s (guest or deleted user?)", upn)

    def expand_principal(login: str) -> None:
        decoded = parse_login(login)
        if decoded["kind"] == "user":
            add_profile(decoded["upn"])
        elif decoded["kind"] in ("m365group", "securitygroup"):
            gid = decoded["groupId"]
            key = f"{gid}{'_o' if decoded.get('owners') else ''}"
            if key not in expanded_groups:
                members = sp.entra_group_members(gid, owners=decoded.get("owners", False))
                expanded_groups[key] = members
                for m in members:
                    if m.get("userPrincipalName"):
                        add_profile(m["userPrincipalName"])

    for u in users:
        expand_principal(u.get("LoginName", ""))
    for g in groups:
        for m in g.get("Users", []):
            expand_principal(m.get("LoginName", ""))

    _write(root / "users.json", {"siteUsers": users, "profiles": profiles, "entraGroups": expanded_groups})
    _write(root / "groups.json", groups)
    summary["users"] = len(profiles)
    summary["groups"] = len(groups)

    log.info("Permissions")
    perms = {
        "roleDefinitions": sp.role_definitions(),
        "web": sp.role_assignments("web"),
        "lists": {},
        "items": {},
        "pages": {},
    }

    log.info("Lists")
    lists_out = []
    for l in sp.list_lists():
        if l["BaseTemplate"] != 100:
            continue
        fields = sp.list_fields(l["Id"])
        items = list(sp.list_items(l["Id"], fields))
        if with_attachments:
            for item in items:
                if item.get("Attachments"):
                    item["_attachments"] = _download_attachments(sp, l, item, root)
        lists_out.append({"list": l, "fields": fields, "itemCount": len(items)})
        _write(root / "lists" / f"{l['Id']}.items.json", items)
        if l.get("HasUniqueRoleAssignments"):
            perms["lists"][l["Id"]] = sp.role_assignments("list", l["Id"])
        for item in items:
            if item.get("HasUniqueRoleAssignments"):
                perms["items"].setdefault(l["Id"], {})[str(item["Id"])] = sp.role_assignments("item", l["Id"], item["Id"])
        log.info("  %s: %d items", l["Title"], len(items))
    _write(root / "lists.json", lists_out)
    summary["lists"] = len(lists_out)
    summary["items"] = sum(x["itemCount"] for x in lists_out)

    log.info("Pages")
    pages = []
    for p in sp.list_pages():
        full = sp.get_page(p["id"])
        full["_images"] = _download_page_images(sp, full, root)
        li = sp.page_list_item(p["name"])
        if li.get("HasUniqueRoleAssignments"):
            perms["pages"][p["name"]] = sp.role_assignments("item", _site_pages_list_id(sp), li["Id"])
        pages.append(full)
    _write(root / "pages.json", pages)
    summary["pages"] = len(pages)

    _write(root / "permissions.json", perms)
    summary["uniquePermissionScopes"] = len(perms["lists"]) + sum(len(v) for v in perms["items"].values()) + len(perms["pages"])
    _write(root / "summary.json", summary)
    return summary


def _site_pages_list_id(sp: SharePointClient) -> str:
    return sp.rest("/web/lists/getbytitle('Site Pages')?$select=Id")["Id"]


def _download_attachments(sp: SharePointClient, l: dict, item: dict, root: Path) -> list[dict]:
    files = []
    for att in sp.item_attachments(l["Id"], item["Id"]):
        rel = att["ServerRelativeUrl"]
        target = root / "files" / "attachments" / l["Id"] / str(item["Id"]) / att["FileName"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sp.download(rel))
        files.append({"fileName": att["FileName"], "serverRelativeUrl": rel, "localPath": str(target)})
    return files


def _download_page_images(sp: SharePointClient, page: dict, root: Path) -> dict[str, str]:
    """Download images referenced by image web parts and <img> tags in text web parts.
    Returns {server-relative URL: local path}."""
    from .transform import find_page_image_urls

    host = urlparse(sp.settings.site_url).hostname
    images: dict[str, str] = {}
    for url in find_page_image_urls(page):
        parsed = urlparse(url)
        if parsed.hostname and parsed.hostname != host:
            continue  # external image, leave as-is
        rel = parsed.path
        target = root / "files" / "pages" / rel.lstrip("/")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(sp.download(rel))
            images[rel] = str(target)
        except Exception as e:  # noqa: BLE001 - report and continue
            log.warning("Could not download image %s: %s", rel, e)
    return images
