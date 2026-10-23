"""Phase 2: build out/plan.json (+ a human-readable out/plan.md) from the extract.

The plan is the contract between extraction and loading: review it (or ask an
agent to review it through the MCP servers) before running `sp2lr load`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from . import libraries as libs
from . import taxonomy as tx
from . import transform as t
from .sharepoint import parse_login


def _read(root: Path, name: str, default=None):
    path = root / name
    if not path.exists() and default is not None:
        return default  # extracts made before a feature existed
    return json.loads(path.read_text())


def load_mapping(path: str | Path | None) -> dict:
    if path and Path(path).exists():
        return yaml.safe_load(Path(path).read_text()) or {}
    return {}


def screen_name(upn: str) -> str:
    local = upn.split("@", 1)[0].lower()
    local = re.sub(r"[^a-z0-9._-]", "-", local)
    if local.isdigit():
        local = f"u{local}"
    return local[:75]


def build_plan(out: Path, mapping: dict) -> dict:
    root = out / "extract"
    site = _read(root, "site.json")
    users = _read(root, "users.json")
    groups = _read(root, "groups.json")
    lists = _read(root, "lists.json")
    pages = _read(root, "pages.json")
    perms = _read(root, "permissions.json")
    libraries = _read(root, "libraries.json", [])
    termsets = _read(root, "termsets.json", {})

    web = site["web"]
    sp_path = web["ServerRelativeUrl"].rstrip("/")
    site_key = (mapping.get("site") or {}).get("key") or t.slugify(sp_path.rsplit("/", 1)[-1] or web["Title"])
    site_name = (mapping.get("site") or {}).get("name") or web["Title"]
    space_cfg = mapping.get("space") or {}
    locale = (mapping.get("locale") or "en_US")
    levels = {**t.DEFAULT_LEVEL_MAP, **((mapping.get("permissions") or {}).get("levels") or {})}
    report: dict[str, list] = {"warnings": [], "manual": []}
    acfg = mapping.get("audit") or {}
    audit = {"fields": acfg.get("fields", True), "impersonate": acfg.get("impersonate", False)}

    # ------------------------------------------------------------- users
    ucfg = mapping.get("users") or {}
    exclude = {e.lower() for e in ucfg.get("exclude", [])}
    plan_users = []
    by_upn: dict[str, dict] = {}
    for upn, prof in users["profiles"].items():
        email = (prof.get("mail") or prof.get("userPrincipalName") or "").lower()
        if not email or upn in exclude or email in exclude:
            continue
        if ucfg.get("skipDisabled", True) and prof.get("accountEnabled") is False:
            report["warnings"].append(f"Skipped disabled account {email}")
            continue
        if "#ext#" in (prof.get("userPrincipalName") or "").lower():
            report["warnings"].append(f"Guest user {email} migrated as a regular Liferay user; review access")
        u = {
            "externalReferenceCode": t.erc_user(prof["id"]),
            "emailAddress": email,
            "alternateName": screen_name(prof.get("userPrincipalName") or email),
            "givenName": prof.get("givenName") or (prof.get("displayName") or email).split(" ")[0],
            "familyName": prof.get("surname") or "-",
            "jobTitle": prof.get("jobTitle") or "",
        }
        plan_users.append(u)
        by_upn[upn.lower()] = u
        by_upn[email] = u

    def members_of_login(login: str) -> list[str]:
        d = parse_login(login)
        if d["kind"] == "user":
            u = by_upn.get(d["upn"].lower())
            return [u["emailAddress"]] if u else []
        if d["kind"] in ("m365group", "securitygroup"):
            key = d["groupId"] + ("_o" if d.get("owners") else "")
            return sorted({by_upn[m["userPrincipalName"].lower()]["emailAddress"]
                           for m in users["entraGroups"].get(key, [])
                           if m.get("userPrincipalName") and m["userPrincipalName"].lower() in by_upn})
        if d["kind"] == "everyone":
            report["warnings"].append(
                "'Everyone except external users' has access - in Liferay, make the site/space open or "
                "grant the Site Member role broadly (see docs/08-permissions.md)")
        return []

    # ----------------------------------------------------------- principals
    sp_groups = {g["Id"]: g for g in groups}
    principals: dict[str, dict] = {}

    def principal_for(member: dict) -> dict | None:
        pid, ptype = member["Id"], member.get("PrincipalType")
        if ptype == 8 and pid in sp_groups:  # SharePoint group
            g = sp_groups[pid]
            emails = sorted({e for m in g.get("Users", []) for e in members_of_login(m.get("LoginName", ""))})
            key = t.erc_group(pid, site_key)
            return principals.setdefault(key, {"erc": key, "kind": "spgroup", "name": f"SP {g['Title']}",
                                               "description": g.get("Description") or "", "members": emails})
        if ptype == 4:  # Entra security group / M365 group
            key = f"sp-entra-{site_key}-{pid}"
            return principals.setdefault(key, {"erc": key, "kind": "entragroup", "name": f"SP {member['Title']}",
                                               "description": "Entra group from SharePoint",
                                               "members": members_of_login(member.get("LoginName", ""))})
        if ptype == 1:
            emails = members_of_login(member.get("LoginName", ""))
            if not emails:
                return None
            key = f"sp-user-{emails[0]}"
            return principals.setdefault(key, {"erc": key, "kind": "user", "name": emails[0], "members": emails})
        return None

    site_grants = []
    for ra in perms["web"]:
        p = principal_for(ra["Member"])
        level = t.strongest_level(ra.get("RoleDefinitionBindings", []))
        if not p or not level:
            continue
        lvl = levels.get(level)
        if lvl is None and level not in levels:
            report["warnings"].append(f"Custom permission level '{level}' for {p['name']} mapped to Read; adjust mapping.yaml")
            lvl = levels["Read"]
        if lvl is None:
            continue
        site_grants.append({"principal": p["erc"], "level": level, **lvl})

    # ----------------------------------------------------------------- lists
    lcfg = mapping.get("lists") or {}
    default_target = lcfg.get("target", "auto")
    plan_lists = []
    for entry in lists:
        info, fields = entry["list"], entry["fields"]
        ov = (lcfg.get("overrides") or {}).get(info["Title"], {})
        if ov.get("skip"):
            report["warnings"].append(f"List '{info['Title']}' skipped by mapping.yaml")
            continue
        items = json.loads((root / "lists" / f"{info['Id']}.items.json").read_text())
        max_att = max((len(i.get("_attachments", [])) for i in items), default=0)
        has_unique = info["Id"] in perms["items"] or info["Id"] in perms["lists"]
        classification = t.classify_list(info, fields, has_unique)
        target = ov.get("target", default_target)
        if target == "auto":
            target = classification["target"]
            decided_by = "auto"
        else:
            decided_by = "mapping.yaml" if "target" in ov else "lists.target"
        lp = t.plan_list(info, fields, max_att, target, {**ov, "audit": audit["fields"]})
        lp["classification"] = {**classification, "decidedBy": decided_by}

        title = info["Title"]
        if target == "objects":
            lp["objectViews"], view_manual = t.views_to_object_views(entry.get("views", []), lp["fields"], locale)
            report["manual"] += [f"List '{title}': {m}" for m in view_manual]
        else:
            lp["objectViews"] = []
            if len(entry.get("views", [])) > 1:
                report["manual"].append(
                    f"List '{title}' has {len(entry['views'])} views; in the CMS, recreate them as "
                    "filters/collections (object views apply to classic Objects only)")
        if info.get("EnableModeration"):
            report["manual"].append(
                f"List '{title}' uses content approval: " +
                ("assign a workflow to the object (Process Builder > Configuration)" if target == "objects"
                 else "set the workflow on the content structure or override it on the Space"))
        if info.get("EnableVersioning") and target == "objects":
            report["warnings"].append(
                f"List '{title}' keeps version history; classic Objects don't version entries (CMS structures do)")
        for f in fields:
            if f.get("ValidationFormula"):
                report["manual"].append(
                    f"List '{title}': column '{f.get('Title')}' validation {f['ValidationFormula']!r} -> "
                    "add an object validation (Expression Builder)")
        for s in lp["skipped"]:
            report["warnings"].append(f"List '{info['Title']}': column {s['field']} not migrated ({s['reason']})")
        for lk in lp["lookups"]:
            if lk["targetListId"] not in {e["list"]["Id"].lower() for e in lists}:
                report["warnings"].append(
                    f"List '{info['Title']}': lookup {lk['field']} targets a list that is not migrated; values dropped")
        lp["itemCount"] = len(items)
        plan_lists.append(lp)

    # Unique list/item permissions -> entry-level grants
    entry_grants: dict[str, dict[str, list]] = {}

    def grants_for(assignments: list[dict], target: str) -> list[dict]:
        out = []
        for ra in assignments:
            p = principal_for(ra["Member"])
            level = t.strongest_level(ra.get("RoleDefinitionBindings", []))
            if p and level and level in t.LEVEL_ENTRY_ACTIONS:
                p["needsRole"] = True
                # CMS entries live in the Space (depot role); everything else lives in the site (site role)
                scopes = p.setdefault("roleScopes", [])
                if t.role_scope(target) not in scopes:
                    scopes.append(t.role_scope(target))
                out.append({"principal": p["erc"], "level": level, "actions": t.LEVEL_ENTRY_ACTIONS[level]})
        return out

    for lp in plan_lists:
        lid = lp["listId"]
        list_level = grants_for(perms["lists"].get(lid, []), lp["target"]) if lid in perms["lists"] else None
        item_level = {iid: grants_for(ras, lp["target"]) for iid, ras in perms["items"].get(lid, {}).items()}
        if list_level is not None or item_level:
            entry_grants[lid] = {"list": list_level, "items": item_level}

    # ------------------------------------------------------------- libraries
    libcfg = mapping.get("libraries") or {}
    lib_skip = set(libcfg.get("skip", []))
    plan_libs = []
    for entry in libraries:
        info = entry["list"]
        if info["Title"] in lib_skip:
            report["warnings"].append(f"Library '{info['Title']}' skipped by mapping.yaml")
            continue
        items = json.loads((root / "libraries" / f"{info['Id']}.items.json").read_text())
        lib = libs.plan_library(entry, items, perms, audit["fields"])
        lib["metadataStrategy"] = libcfg.get("metadata", "documentType")
        grants = {}
        if info["Id"] in perms["lists"]:
            grants["library"] = grants_for(perms["lists"][info["Id"]], "site")
        for iid, ras in perms["items"].get(info["Id"], {}).items():
            grants[iid] = grants_for(ras, "site")
        lib["grants"] = grants
        lib["itemCount"] = len(items)
        for sk in lib["skippedFiles"]:
            report["warnings"].append(f"Library '{info['Title']}': file not migrated: {sk}")
        for col in lib["documentType"]["skipped"]:
            report["warnings"].append(f"Library '{info['Title']}': column {col} not migrated")
        if lib["moderation"]:
            report["manual"].append(f"Library '{info['Title']}' uses content approval: enable a workflow for "
                                    "Documents and Media in Site Settings > Workflow (or on the folder)")
        if lib["versioning"]:
            report["warnings"].append(f"Library '{info['Title']}': only the current version of each file is migrated")
        plan_libs.append(lib)

    # -------------------------------------------------------------- taxonomy
    tcfg = mapping.get("taxonomy") or {}
    sources = []
    for lp in plan_lists:
        if lp["taxonomyFields"]:
            sources.append((lp, json.loads((root / "lists" / f"{lp['listId']}.items.json").read_text())))
    for lib in plan_libs:
        if lib["taxonomyFields"]:
            sources.append((lib, json.loads((root / "libraries" / f"{lib['libraryId']}.items.json").read_text())))
    vocabularies, tax_warnings = tx.plan_vocabularies(termsets, sources)
    report["warnings"] += tax_warnings

    # ----------------------------------------------------------------- news
    ncfg = mapping.get("news") or {}
    news_target = ncfg.get("target", "blogs")
    news_pages = [p for p in pages if t.is_news(p)]
    news: dict = {"target": news_target, "posts": []}
    if news_pages and news_target == "blogs":
        for p in news_pages:
            li = p.get("_listItem") or {}
            news["posts"].append({
                "pageId": p["id"], "erc": f"sp-news-{p['id']}", "headline": p.get("title") or p["name"],
                "description": p.get("description") or "", "friendlyUrlPath": t.slugify(p["name"]),
                "datePublished": t.iso_utc(li.get("FirstPublishedDate") or li.get("Created") or p.get("createdDateTime")),
                "authorEmail": ((li.get("Author") or {}).get("EMail") or "").lower()})
        if not audit["impersonate"]:
            report["manual"].append("News posts are authored by the migration user; enable audit.impersonate "
                                    "or edit the author in Blogs if the byline matters")
    elif news_pages and news_target == "structure":
        news_id = f"news-{web.get('Id', 'site')}"
        info = {"Id": news_id, "Title": ncfg.get("structureName", "News"), "ItemCount": len(news_pages)}
        nfields = [
            {"Id": f"{news_id}-title", "InternalName": "Title", "Title": "Title", "TypeAsString": "Text", "Required": True},
            {"Id": f"{news_id}-summary", "InternalName": "Description", "Title": "Summary", "TypeAsString": "Note"},
            {"Id": f"{news_id}-body", "InternalName": "Body", "Title": "Body", "TypeAsString": "Note", "RichText": True},
            {"Id": f"{news_id}-published", "InternalName": "FirstPublishedDate", "Title": "Published",
             "TypeAsString": "DateTime", "DisplayFormat": 1},
        ]
        nitems = []
        for i, p in enumerate(news_pages, 1):
            li = p.get("_listItem") or {}
            nitems.append({"Id": li.get("Id") or i, "Title": p.get("title") or p["name"],
                           "Description": p.get("description") or "", "Body": "", "_pageId": p["id"],
                           "FirstPublishedDate": li.get("FirstPublishedDate") or li.get("Created"),
                           "Created": li.get("Created"), "Modified": li.get("Modified"),
                           "Author": li.get("Author"), "Editor": li.get("Editor")})
        (root / "lists").mkdir(parents=True, exist_ok=True)
        (root / "lists" / f"{news_id}.items.json").write_text(json.dumps(nitems, indent=2))
        lp = t.plan_list(info, nfields, 0, "cms", {"audit": audit["fields"], "name": "News"})
        lp.update({"news": True, "itemCount": len(nitems), "objectViews": [],
                   "classification": {"target": "cms", "reasons": ["SharePoint news posts"], "decidedBy": "news.target"}})
        plan_lists.append(lp)
        news["listId"] = news_id

    # ----------------------------------------------------------------- pages
    pcfg = mapping.get("pages") or {}
    skip_pages = set(pcfg.get("skip", []))
    lr_prefix = f"/web/{site_key}"
    links = t.page_link_map(pages, sp_path, lr_prefix)
    for post in news["posts"]:
        page = next(p for p in pages if p["id"] == post["pageId"])
        for k in [k for k, v in links.items() if v == f"{lr_prefix}/{t.slugify(page['name'])}"]:
            links[k] = f"{lr_prefix}/-/blogs/{post['friendlyUrlPath']}"
    news["links"] = links
    plan_pages = []
    for p in pages:
        if p.get("name") in skip_pages or (t.is_news(p) and news_target != "pages"):
            continue
        unmigrated = []
        _, unmigrated = t.page_to_site_page(p, locale, {}, links)
        plan_pages.append({"id": p["id"], "name": p["name"], "title": p.get("title"),
                           "friendlyUrlPath": "/" + t.slugify(p["name"]), "unmigratedWebParts": unmigrated,
                           "images": len(p.get("_images", {}))})
        for w in unmigrated:
            report["manual"].append(f"Page {p['name']}: rebuild web part '{w}' with a Liferay fragment/widget")
    for name in perms.get("pages", {}):
        report["manual"].append(f"Page {name} has unique permissions; set page permissions in Liferay manually")

    plan = {
        "locale": locale,
        "site": {"name": site_name, "key": site_key, "erc": f"sp-site-{site['graph']['id'].split(',')[1]}",
                 "spPath": sp_path, "welcomePage": web.get("WelcomePage")},
        "space": {"name": space_cfg.get("name") or f"{site_name} Content",
                  "key": space_cfg.get("key") or f"{site_key}-content",
                  "erc": f"sp-space-{site['graph']['id'].split(',')[1]}",
                  "description": f"Content migrated from SharePoint site {web['Url']}"},
        "users": plan_users,
        "principals": list(principals.values()),
        "siteGrants": site_grants,
        "lists": plan_lists,
        "entryGrants": entry_grants,
        "pages": plan_pages,
        "libraries": plan_libs,
        "vocabularies": vocabularies,
        "taxonomy": {"scope": tcfg.get("scope", "global")},
        "news": news,
        "audit": audit,
        "navigation": {"menuName": pcfg.get("navigationMenuName", "SharePoint Navigation")},
        "report": report,
    }
    (out / "plan.json").write_text(json.dumps(plan, indent=2))
    (out / "plan.md").write_text(render_plan_md(plan))
    return plan


def render_plan_md(plan: dict) -> str:
    lines = [f"# Migration plan: {plan['site']['name']}", "",
             f"- Liferay site: **{plan['site']['name']}** (`/web/{plan['site']['key']}`)",
             f"- CMS Space: **{plan['space']['name']}** (`{plan['space']['key']}`)",
             f"- Users: {len(plan['users'])}",
             f"- User groups / principals: {len(plan['principals'])}",
             f"- Lists -> structures: {len(plan['lists'])}",
             f"- Pages: {len(plan['pages'])}",
             f"- Document libraries: {len(plan.get('libraries', []))} "
             f"({sum(len(l['documents']) for l in plan.get('libraries', []))} files)",
             f"- Vocabularies: {len(plan.get('vocabularies', []))}",
             f"- News posts: {len((plan.get('news') or {}).get('posts', []))}", "", "## Site-level permissions", "",
             "| Principal | SharePoint level | Liferay site role | Liferay space role |", "|---|---|---|---|"]
    names = {p["erc"]: p["name"] for p in plan["principals"]}
    for g in plan["siteGrants"]:
        lines.append(f"| {names.get(g['principal'], g['principal'])} | {g['level']} | "
                     f"{g.get('siteRole') or 'Site Member'} | {g.get('spaceRole') or 'Asset Library Member'} |")
    lines += ["", "## Lists", ""]
    lines += ["| SharePoint list | Target | Decided by | Why |", "|---|---|---|---|"]
    for lp in plan["lists"]:
        c = lp.get("classification", {})
        label = "CMS content structure (Space)" if lp["target"] == "cms" else "Liferay Object (site)"
        lines.append(f"| {lp['listTitle']} | {label} | {c.get('decidedBy', '-')} | {'; '.join(c.get('reasons', []))} |")
    lines.append("")
    for lp in plan["lists"]:
        d = lp["definition"]
        lines += [f"### {lp['listTitle']} -> `{d['name']}` ({lp['target']}, {lp['itemCount']} items)", ""]
        if lp.get("objectViews"):
            lines += ["Object views: " + ", ".join(v["name"][next(iter(v["name"]))] for v in lp["objectViews"]), ""]
        lines += ["| SharePoint column | Type | Liferay field | Business type |", "|---|---|---|---|"]
        for m in lp["fields"]:
            if m["objectField"]:
                bt = m["objectField"]["businessType"]
            elif m.get("taxonomy"):
                bt = "Categories (taxonomyCategoryIds)"
            else:
                bt = "Relationship (many-to-many)" if m["lookup"]["multi"] else "Relationship (one-to-many)"
            lines.append(f"| {m['sp']} | {m['kind']} | `{m['name']}` | {bt} |")
        for a in lp["attachmentFields"]:
            lines.append(f"| Attachments | Attachments | `{a}` | Attachment |")
        lines.append("")
    if plan.get("libraries"):
        lines += ["## Document libraries -> Documents and Media", "",
                  "| Library | Folders | Files | Document type fields | Categories from | Unique permission scopes |",
                  "|---|---|---|---|---|---|"]
        for lib in plan["libraries"]:
            lines.append(f"| {lib['title']} | {len(lib['folders'])} | {len(lib['documents'])} | "
                         f"{', '.join(m['name'] for m in lib['documentType']['fields']) or '-'} | "
                         f"{', '.join(tf['label'] for tf in lib['taxonomyFields']) or '-'} | {len(lib['grants'])} |")
        lines.append("")
    if plan.get("vocabularies"):
        lines += [f"## Managed metadata -> categories ({plan['taxonomy']['scope']} scope)", "",
                  "| Term set -> vocabulary | Categories | Source |", "|---|---|---|"]
        for v in plan["vocabularies"]:
            lines.append(f"| {v['name']} | {len(v['categories'])} | "
                         f"{'values on items' if v['fromItemsOnly'] else 'term store'} |")
        lines.append("")
    news = plan.get("news") or {}
    if news.get("posts") or news.get("listId"):
        lines += [f"## News -> {news['target']}", ""]
        for post in news.get("posts", []):
            lines.append(f"- {post['headline']} -> `/-/blogs/{post['friendlyUrlPath']}` ({post['datePublished']})")
        if news.get("listId"):
            lines.append("- News posts become entries of the CMS structure `News`")
        lines.append("")
    a = plan.get("audit") or {}
    lines += ["## Created / Modified / Author", "",
              f"- Audit fields (spCreated, spModified, spCreatedBy, spModifiedBy): {'yes' if a.get('fields') else 'no'}",
              f"- Impersonate original authors (doAsUserId): {'yes' if a.get('impersonate') else 'no'}", ""]
    lines += ["## Pages", "", "| SharePoint page | Liferay URL | Unmigrated web parts |", "|---|---|---|"]
    for p in plan["pages"]:
        lines.append(f"| {p['name']} | `/web/{plan['site']['key']}{p['friendlyUrlPath']}` | {', '.join(p['unmigratedWebParts']) or '-'} |")
    lines += ["", "## Warnings", ""] + [f"- {w}" for w in plan["report"]["warnings"] or ["None"]]
    lines += ["", "## Manual follow-ups", ""] + [f"- [ ] {m}" for m in plan["report"]["manual"] or ["None"]]
    return "\n".join(lines) + "\n"
