"""Phase 3: load the plan into Liferay DXP 2026.Q3.

Phases run in dependency order and are idempotent: every Liferay record is
keyed by an External Reference Code derived from the SharePoint ID, and
out/state.json remembers the Liferay IDs so a phase can be re-run safely.

  site -> users -> groups -> structures -> entries -> pages -> permissions
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from . import transform as t
from .liferay import LiferayClient, LiferayError

log = logging.getLogger(__name__)

PHASES = ["site", "users", "groups", "structures", "entries", "pages", "permissions"]

CONTRIBUTOR_SITE_ROLE = "SP Contributor"
CONTRIBUTOR_SPACE_ROLE = "SP Space Contributor"


class Loader:
    def __init__(self, lr: LiferayClient, out: Path):
        self.lr = lr
        self.out = out
        self.plan = json.loads((out / "plan.json").read_text())
        self.state_file = out / ("state.dry-run.json" if lr.dry_run else "state.json")
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.manual: list[str] = []
        self.errors: list[str] = []

    def save(self) -> None:
        self.state["manual"] = sorted(set(self.state.get("manual", []) + self.manual))
        self.state["errors"] = self.state.get("errors", []) + self.errors
        self.errors = []
        self.state_file.write_text(json.dumps(self.state, indent=2))

    def run(self, phases: list[str]) -> None:
        for phase in PHASES:
            if phase in phases:
                log.info("== phase: %s", phase)
                getattr(self, f"phase_{phase}")()
                self.save()

    def _try(self, what: str, fn, *args, manual: str | None = None, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LiferayError as e:
            if manual and e.status in (400, 403, 404, 405):
                log.warning("%s not possible through the API (%s); recorded as manual step", what, e.status)
                self.manual.append(manual)
            else:
                log.error("%s failed: %s", what, e)
                self.errors.append(f"{what}: {e}")
            return None

    # ------------------------------------------------------------------ site
    def phase_site(self) -> None:
        s, sp = self.plan["site"], self.plan["space"]
        site = self.lr.site_by_friendly_url(s["key"])
        if not site:
            site = self._try("create site", self.lr.create_site, s["name"], s["key"], s["erc"],
                             manual=f"Create site '{s['name']}' with friendly URL /{s['key']}, then re-run phase site")
        if site:
            self.state["site"] = {"id": site["id"], "key": s["key"]}
            log.info("Site %s -> %s", s["name"], site["id"])
        space = self._try("upsert space", self.lr.upsert_space, sp["erc"], sp["key"], sp["name"], sp["description"])
        if space:
            self.state["space"] = {"id": space["id"], "erc": sp["erc"], "key": sp["key"]}
            log.info("Space %s -> %s", sp["name"], space["id"])
        # Connecting a space to a site is done in the CMS UI (Space > Settings > Sites)
        self.manual.append(f"Connect Space '{sp['name']}' to site '{s['name']}' (CMS > Spaces > {sp['name']} > Settings > Connected Sites)")

    # ----------------------------------------------------------------- users
    def phase_users(self) -> None:
        ids = self.state.setdefault("users", {})
        for u in self.plan["users"]:
            existing = self.lr.user_by_email(u["emailAddress"]) if not self.lr.dry_run else None
            if existing and existing.get("externalReferenceCode") != u["externalReferenceCode"]:
                # Pre-existing Liferay user with the same e-mail: link, don't duplicate
                ids[u["emailAddress"]] = existing["id"]
                log.info("User %s already exists (id %s), linked", u["emailAddress"], existing["id"])
                continue
            res = self._try(f"user {u['emailAddress']}", self.lr.upsert_user, u["externalReferenceCode"], u)
            if res:
                ids[u["emailAddress"]] = res["id"]
        log.info("Users: %d", len(ids))

    # ---------------------------------------------------------------- groups
    def phase_groups(self) -> None:
        site_id = self.state["site"]["id"]
        space_id = self.state["space"]["id"]
        users = self.state.get("users", {})
        roles = self.state.setdefault("roles", {})
        ugs = self.state.setdefault("userGroups", {})

        for name, rtype in ((CONTRIBUTOR_SITE_ROLE, "site"), (CONTRIBUTOR_SPACE_ROLE, "depot")):
            r = self._try(f"role {name}", self.lr.ensure_role, name, rtype,
                          "Migrated SharePoint Edit/Contribute permission level",
                          manual=f"Create {rtype} role '{name}' (Control Panel > Roles)")
            if r:
                roles[name] = r["id"]

        for p in self.plan["principals"]:
            if p["kind"] == "user":
                continue
            ug = self._try(f"user group {p['name']}", self.lr.upsert_user_group, p["erc"], p["name"], p.get("description", ""))
            if not ug:
                continue
            ugs[p["erc"]] = ug["id"]
            member_ids = [users[e] for e in p["members"] if e in users]
            if member_ids:
                self._try(f"members of {p['name']}", self.lr.add_users_to_group, ug["id"], member_ids)
            for target in p.get("roleScopes", []):
                rname = t.principal_role_name(p["name"], target)
                rtype = "depot" if target == "cms" else "site"
                r = self._try(f"role {rname}", self.lr.ensure_role, rname, rtype,
                              "Used for migrated SharePoint item-level permissions",
                              manual=f"Create {rtype} role '{rname}'")
                if r:
                    roles[rname] = r["id"]

        # Site / space membership and roles from the site-level role assignments
        for g in self.plan["siteGrants"]:
            p = next(x for x in self.plan["principals"] if x["erc"] == g["principal"])
            for group_id, role_name in ((site_id, g.get("siteRole")), (space_id, g.get("spaceRole"))):
                self._grant(p, group_id, role_name)
        # Principals that appear on item permissions get their role where the entries live
        for p in self.plan["principals"]:
            for target in p.get("roleScopes", []):
                self._grant(p, space_id if target == "cms" else site_id, t.principal_role_name(p["name"], target))

    def _role_id(self, name: str) -> int | None:
        roles = self.state.setdefault("roles", {})
        if name not in roles:
            r = self.lr.role_by_name(name) if not self.lr.dry_run else {"id": 0}
            if not r:
                self.manual.append(f"Role '{name}' not found; create it or change mapping.yaml permissions.levels")
                return None
            roles[name] = r["id"]
        return roles[name]

    def _grant(self, p: dict, group_id: int, role_name: str | None) -> None:
        users = self.state.get("users", {})
        role_ids = [rid for rid in [self._role_id(role_name)] if rid] if role_name else []
        if p["kind"] == "user":
            uid = users.get(p["members"][0])
            if not uid:
                return
            self._try(f"membership {p['name']}@{group_id}", self.lr.jsonws, "/user/add-group-users",
                      {"groupId": group_id, "userIds": [uid], "serviceContext": {}})
            if role_ids:
                self._try(f"role {role_name} for {p['name']}", self.lr.jsonws, "/usergrouprole/add-user-group-roles",
                          {"userId": uid, "groupId": group_id, "roleIds": role_ids})
            return
        ugid = self.state.get("userGroups", {}).get(p["erc"])
        if not ugid:
            return
        self._try(f"membership {p['name']}@{group_id}", self.lr.jsonws, "/usergroup/add-group-user-groups",
                  {"groupId": group_id, "userGroupIds": [ugid]})
        if role_ids:
            self._try(f"role {role_name} for {p['name']}", self.lr.jsonws,
                      "/usergroupgrouprole/add-user-group-group-roles",
                      {"userGroupId": ugid, "groupId": group_id, "roleIds": role_ids})

    # ------------------------------------------------------------ structures
    def phase_structures(self) -> None:
        defs = self.state.setdefault("definitions", {})
        for lp in self.plan["lists"]:
            for pl in lp["picklists"]:
                self._try(f"picklist {pl['name']}", self.lr.upsert_list_type, pl)
            body = dict(lp["definition"])
            if lp["target"] != "cms":
                body.pop("objectFolderExternalReferenceCode", None)
            od = self._try(f"structure {body['name']}", self.lr.upsert_object_definition, body)
            if not od:
                continue
            if od.get("status", {}).get("code") != 0:
                self._try(f"publish {body['name']}", self.lr.publish_object_definition, od["id"])
            od = (self.lr.get(f"/o/object-admin/v1.0/object-definitions/{od['id']}") if not self.lr.dry_run else od) or od
            defs[lp["listId"]] = {"id": od["id"], "erc": body["externalReferenceCode"], "name": body["name"],
                                  "restContextPath": od.get("restContextPath") or f"/o/c/{body['name'].lower()}s"}
            log.info("%s %s -> %s (%s)", "Structure" if lp["target"] == "cms" else "Object",
                     body["name"], od["id"], defs[lp["listId"]]["restContextPath"])
            if lp["target"] == "cms":
                self.manual.append(
                    f"Grant VIEW on structure '{body['label']['en_US']}' to the roles that should read it "
                    "(CMS > Content Structures > Actions > Permissions). Custom structures have VIEW disabled by default.")
            else:
                self._object_views(od["id"], lp)
                self.manual.append(
                    f"Review the layout of object '{body['label']['en_US']}' (Control Panel > Objects > "
                    f"{body['name']} > Layouts) and grant the site roles access in Site Settings > Roles")

        # Relationships for lookup columns (after every definition exists)
        for lp in self.plan["lists"]:
            src = defs.get(lp["listId"])
            for lk in lp["lookups"]:
                tgt = defs.get(lk["targetListId"]) or next(
                    (v for k, v in defs.items() if k.lower() == lk["targetListId"]), None)
                if not src or not tgt:
                    continue
                if lk["multi"]:
                    owner, o1, o2, rtype = src, src, tgt, "manyToMany"
                else:
                    owner, o1, o2, rtype = tgt, tgt, src, "oneToMany"
                rel = self._try(f"relationship {lk['relationshipName']}", self.lr.add_relationship, owner["id"], {
                    "name": lk["relationshipName"], "label": {"en_US": lk["field"]}, "type": rtype,
                    "objectDefinitionId1": o1["id"], "objectDefinitionId2": o2["id"],
                    "objectDefinitionExternalReferenceCode1": o1["erc"],
                    "objectDefinitionExternalReferenceCode2": o2["erc"],
                    "deletionType": "disassociate",
                })
                if rel:
                    src.setdefault("relationships", {})[lk["field"]] = {
                        "name": lk["relationshipName"], "type": rtype, "target": lk["targetListId"]}

    def _object_views(self, definition_id: int, lp: dict) -> None:
        path = f"/o/object-admin/v1.0/object-definitions/{definition_id}/object-views"
        existing = set()
        if not self.lr.dry_run:
            existing = {next(iter(v.get("name", {}).values()), "") for v in (self.lr.get(path) or {}).get("items", [])}
        for view in lp.get("objectViews", []):
            label = next(iter(view["name"].values()))
            if label in existing:
                continue
            self._try(f"object view {label}", self.lr.call, "POST", path, view,
                      manual=f"Create object view '{label}' on {lp['definition']['name']}")

    # --------------------------------------------------------------- entries
    def _scope_path(self, lp: dict) -> str:
        d = self.state["definitions"][lp["listId"]]
        scope = self.state["space"]["id"] if lp["target"] == "cms" else self.state["site"]["id"]
        return f"{d['restContextPath']}/scopes/{scope}"

    def phase_entries(self) -> None:
        entries = self.state.setdefault("entries", {})
        root = self.out / "extract" / "lists"
        for lp in self.plan["lists"]:
            if lp["listId"] not in self.state.get("definitions", {}):
                continue
            items = json.loads((root / f"{lp['listId']}.items.json").read_text())
            ids = entries.setdefault(lp["listId"], {})
            base = self._scope_path(lp)
            for item in items:
                body = t.item_to_entry(lp, item)
                for field, att in zip(lp["attachmentFields"], item.get("_attachments", [])):
                    body[field] = {"name": att["fileName"],
                                   "fileBase64": base64.b64encode(Path(att["localPath"]).read_bytes()).decode()}
                res = self._try(f"entry {body['externalReferenceCode']}", self.lr.call, "PUT",
                                f"{base}/by-external-reference-code/{body['externalReferenceCode']}", body)
                if res:
                    ids[str(item["Id"])] = res["id"]
            log.info("%s: %d/%d entries", lp["listTitle"], len(ids), len(items))
        self.save()
        self._link_relationships()

    def _link_relationships(self) -> None:
        defs, entries = self.state["definitions"], self.state["entries"]
        root = self.out / "extract" / "lists"
        for lp in self.plan["lists"]:
            src = defs.get(lp["listId"], {})
            rels = src.get("relationships", {})
            if not rels:
                continue
            items = json.loads((root / f"{lp['listId']}.items.json").read_text())
            rel_fields = self._relationship_fields(src) if not self.lr.dry_run else {}
            for item in items:
                src_id = entries.get(lp["listId"], {}).get(str(item["Id"]))
                if not src_id:
                    continue
                for lk in lp["lookups"]:
                    rel = rels.get(lk["field"])
                    if not rel:
                        continue
                    targets = [entries.get(rel["target"], {}).get(str(tid))
                               for tid in t.lookup_target_ids(item, lk["field"], lk["multi"])]
                    targets = [x for x in targets if x]
                    if rel["type"] == "manyToMany":
                        for tid in targets:
                            self._try("relate", self.lr.call, "PUT",
                                      f"{src['restContextPath']}/{src_id}/{rel['name']}/{tid}")
                    elif targets:
                        field = rel_fields.get(rel["name"], f"r_{rel['name']}_c_{defs[rel['target']]['name'][:1].lower()}{defs[rel['target']]['name'][1:]}Id")
                        self._try("relate", self.lr.call, "PATCH", f"{src['restContextPath']}/{src_id}",
                                  {field: targets[0]})

    def _relationship_fields(self, d: dict) -> dict[str, str]:
        od = self.lr.get(f"/o/object-admin/v1.0/object-definitions/{d['id']}") or {}
        out = {}
        for f in od.get("objectFields", []):
            if f.get("businessType") == "Relationship":
                for rel in (d.get("relationships") or {}).values():
                    if f["name"].startswith(f"r_{rel['name']}_"):
                        out[rel["name"]] = f["name"]
        return out

    # ----------------------------------------------------------------- pages
    def phase_pages(self) -> None:
        site_id = self.state["site"]["id"]
        locale = self.plan["locale"]
        done = self.state.setdefault("pages", {})
        pages = {p["id"]: p for p in json.loads((self.out / "extract" / "pages.json").read_text())}
        prefix = f"/web/{self.plan['site']['key']}"
        links = t.page_link_map(list(pages.values()), self.plan["site"]["spPath"], prefix)

        url_map: dict[str, str] = self.state.setdefault("images", {})
        folder = None
        for pp in self.plan["pages"]:
            for rel, local in pages[pp["id"]].get("_images", {}).items():
                if rel in url_map:
                    continue
                folder = folder or self._try("image folder", self.lr.ensure_document_folder, site_id, "SharePoint Images")
                doc = self._try(f"image {rel}", self.lr.upload_document, site_id, local, Path(local).name,
                                folder["id"] if folder else None)
                if doc:
                    url_map[rel] = doc.get("contentUrl") or f"/documents/d/{self.plan['site']['key']}/{Path(local).name}"
                    url_map[f"https://{self._sp_host()}{rel}"] = url_map[rel]

        for pp in self.plan["pages"]:
            if pp["name"] in done:
                continue
            if not self.lr.dry_run and self.lr.site_page(site_id, pp["friendlyUrlPath"]):
                log.info("Page %s exists, skipping", pp["friendlyUrlPath"])
                done[pp["name"]] = pp["friendlyUrlPath"]
                continue
            body, _ = t.page_to_site_page(pages[pp["id"]], locale, url_map, links)
            res = self._try(f"page {pp['name']}", self.lr.create_site_page, site_id, body)
            if res:
                done[pp["name"]] = pp["friendlyUrlPath"]
                log.info("Page %s -> %s%s", pp["name"], prefix, pp["friendlyUrlPath"])

        welcome = (self.plan["site"].get("welcomePage") or "").rsplit("/", 1)[-1]
        if welcome:
            self.manual.append(f"Set '/{t.slugify(welcome)}' as the first (home) page in Site Builder > Pages")

        nav = json.loads((self.out / "extract" / "site.json").read_text())["navigation"]
        items = t.navigation_items(nav.get("quickLaunch") or nav.get("topNavigationBar") or [], links)
        if items:
            self._try("navigation menu", self.lr.upsert_navigation_menu, site_id, self.plan["navigation"]["menuName"], items)
            self.manual.append(f"Mark navigation menu '{self.plan['navigation']['menuName']}' as Primary Navigation")

    def _sp_host(self) -> str:
        site = json.loads((self.out / "extract" / "site.json").read_text())
        return site["graph"]["id"].split(",")[0]

    # ----------------------------------------------------------- permissions
    def phase_permissions(self) -> None:
        defs, entries = self.state.get("definitions", {}), self.state.get("entries", {})
        principals = {p["erc"]: p for p in self.plan["principals"]}
        # Roles that would otherwise inherit VIEW; cleared on entries that broke inheritance
        baseline = ["Asset Library Member", "Asset Library Connected Site Member", "Site Member", "Guest", "User"]
        targets = {lp["listId"]: lp["target"] for lp in self.plan["lists"]}
        for list_id, eg in self.plan["entryGrants"].items():
            d = defs.get(list_id)
            if not d:
                continue
            target = targets.get(list_id, "cms")
            for sp_item_id, lr_id in entries.get(list_id, {}).items():
                grants = eg["items"].get(sp_item_id, eg["list"])
                if grants is None:
                    continue
                body = [{"roleName": r, "actionIds": []} for r in baseline]
                for g in grants:
                    p = principals[g["principal"]]
                    body.append({"roleName": t.principal_role_name(p["name"], target), "actionIds": g["actions"]})
                self._try(f"permissions {d['name']}/{lr_id}", self.lr.call, "PUT",
                          f"{d['restContextPath']}/{lr_id}/permissions", body)
        # Contributors may add entries to every migrated structure
        for d in defs.values():
            self._try(f"ADD_OBJECT_ENTRY on {d['name']}", self.lr.call, "PUT", f"{d['restContextPath']}/permissions",
                      [{"roleName": CONTRIBUTOR_SPACE_ROLE, "actionIds": ["ADD_OBJECT_ENTRY"]},
                       {"roleName": CONTRIBUTOR_SITE_ROLE, "actionIds": ["ADD_OBJECT_ENTRY"]}],
                      manual=f"Give '{CONTRIBUTOR_SPACE_ROLE}' the Add Entry permission on structure {d['name']}")
