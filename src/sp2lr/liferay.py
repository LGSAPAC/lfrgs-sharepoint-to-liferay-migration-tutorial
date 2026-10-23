"""Thin Liferay DXP 2026.Q3 headless client used by the loader.

Every write goes through `call()`, which in --dry-run mode records the request
to out/dry-run.jsonl instead of sending it, so a whole migration can be
reviewed before touching Liferay.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .config import LiferaySettings

log = logging.getLogger(__name__)


class LiferayError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str):
        super().__init__(f"{method} {path} -> {status}: {body[:500]}")
        self.status = status
        self.body = body


class LiferayClient:
    def __init__(self, settings: LiferaySettings, dry_run: bool = False, out: Path | None = None):
        self.settings = settings
        self.dry_run = dry_run
        self.http = httpx.Client(base_url=settings.url, auth=(settings.user, settings.password), timeout=120)
        self._dry_log = (out or Path("out")) / "dry-run.jsonl"
        self._fake_id = 900000

    # ------------------------------------------------------------------ core
    def call(self, method: str, path: str, body: Any = None, *, files=None, data=None,
             ok404: bool = False, write: bool | None = None) -> Any:
        is_write = write if write is not None else method != "GET"
        if self.dry_run and is_write:
            self._fake_id += 1
            with self._dry_log.open("a") as fh:
                fh.write(json.dumps({"method": method, "path": path, "body": body, "data": data,
                                     "files": list(files) if files else None}, default=str) + "\n")
            return {"id": self._fake_id, "externalReferenceCode": (body or {}).get("externalReferenceCode")
                    if isinstance(body, dict) else None, "_dryRun": True}
        for attempt in range(5):
            r = self.http.request(method, path, json=body if files is None and data is None else None,
                                  files=files, data=data, headers={"Accept": "application/json"})
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            break
        if r.status_code == 404 and ok404:
            return None
        if r.status_code >= 400:
            raise LiferayError(method, path, r.status_code, r.text)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {"_raw": r.text}

    def get(self, path: str, ok404: bool = True) -> Any:
        return self.call("GET", path, ok404=ok404)

    def paged(self, path: str, page_size: int = 200):
        page = 1
        sep = "&" if "?" in path else "?"
        while True:
            data = self.get(f"{path}{sep}page={page}&pageSize={page_size}", ok404=False)
            yield from data.get("items", [])
            if page >= data.get("lastPage", 1):
                return
            page += 1

    def jsonws(self, command: str, params: dict) -> Any:
        """Call a JSON Web Service, e.g. jsonws('/usergroup/add-group-user-groups', {...}).

        Used only for membership operations that have no headless equivalent."""
        return self.call("POST", "/api/jsonws/invoke", {command: params}, write=True)

    def upsert_by_erc(self, collection: str, erc: str, body: dict) -> dict:
        """PUT {collection}/by-external-reference-code/{erc} (create-or-update)."""
        return self.call("PUT", f"{collection}/by-external-reference-code/{erc}", body)

    # ----------------------------------------------------------- sites/spaces
    def site_by_friendly_url(self, key: str) -> dict | None:
        return self.get(f"/o/headless-admin-user/v1.0/sites/by-friendly-url-path/{key}")

    def create_site(self, name: str, key: str, erc: str) -> dict:
        # headless-site creates a blank site; membershipType "private" means
        # only explicitly assigned members (matching a SharePoint team site).
        return self.call("POST", "/o/headless-site/v1.0/sites", {
            "externalReferenceCode": erc, "name": name, "friendlyUrlPath": f"/{key}",
            "membershipType": "private", "templateType": "site-initializer",
            "templateKey": "com.liferay.site.initializer.blank",
        })

    def space_by_erc(self, erc: str) -> dict | None:
        return self.get(f"/o/headless-asset-library/v1.0/asset-libraries/by-external-reference-code/{erc}")

    def upsert_space(self, erc: str, key: str, name: str, description: str) -> dict:
        return self.upsert_by_erc("/o/headless-asset-library/v1.0/asset-libraries", erc, {
            "assetLibraryKey": key, "name": name, "description": description,
            "settings": {}, "type": "Space",
        })

    # ----------------------------------------------------------------- users
    def upsert_user(self, erc: str, body: dict) -> dict:
        return self.upsert_by_erc("/o/headless-admin-user/v1.0/user-accounts", erc, body)

    def user_by_email(self, email: str) -> dict | None:
        return self.get(f"/o/headless-admin-user/v1.0/user-accounts/by-email-address/{email}")

    def upsert_user_group(self, erc: str, name: str, description: str) -> dict:
        return self.upsert_by_erc("/o/headless-admin-user/v1.0/user-groups", erc,
                                  {"name": name, "description": description, "externalReferenceCode": erc})

    def add_users_to_group(self, user_group_id: int, user_ids: list[int]) -> Any:
        return self.call("POST", f"/o/headless-admin-user/v1.0/user-groups/{user_group_id}/user-group-users", user_ids)

    def role_by_name(self, name: str) -> dict | None:
        for role in self.paged(f"/o/headless-admin-user/v1.0/roles?search={quote(name)}"):
            if role.get("name") == name:
                return role
        return None

    def ensure_role(self, name: str, role_type: str, description: str) -> dict:
        role = self.role_by_name(name)
        if role:
            return role
        return self.call("POST", "/o/headless-admin-user/v1.0/roles", {
            "name": name, "name_i18n": {self.settings.locale: name}, "roleType": role_type,
            "description": description, "externalReferenceCode": f"sp2lr-role-{role_type}-{name.lower().replace(' ', '-')}",
        })

    # ---------------------------------------------------------------- objects
    def object_definition_by_erc(self, erc: str) -> dict | None:
        return self.get(f"/o/object-admin/v1.0/object-definitions/by-external-reference-code/{erc}")

    def upsert_object_definition(self, body: dict) -> dict:
        existing = self.object_definition_by_erc(body["externalReferenceCode"])
        if existing:
            # Add fields that are missing; never drop fields with data.
            have = {f["name"] for f in existing.get("objectFields", [])}
            for field in body["objectFields"]:
                if field["name"] not in have:
                    self.call("POST", f"/o/object-admin/v1.0/object-definitions/{existing['id']}/object-fields", field)
            return self.get(f"/o/object-admin/v1.0/object-definitions/{existing['id']}") or existing
        return self.call("POST", "/o/object-admin/v1.0/object-definitions", body)

    def publish_object_definition(self, definition_id: int) -> Any:
        return self.call("POST", f"/o/object-admin/v1.0/object-definitions/{definition_id}/publish")

    def upsert_list_type(self, body: dict) -> dict:
        return self.upsert_by_erc("/o/headless-admin-list-type/v1.0/list-type-definitions",
                                  body["externalReferenceCode"], body)

    def add_relationship(self, definition_id: int, body: dict) -> dict:
        existing = self.get(f"/o/object-admin/v1.0/object-definitions/{definition_id}/object-relationships?search={body['name']}")
        for rel in (existing or {}).get("items", []):
            if rel["name"] == body["name"]:
                return rel
        return self.call("POST", f"/o/object-admin/v1.0/object-definitions/{definition_id}/object-relationships", body)

    # ---------------------------------------------------------------- pages
    def site_page(self, site_id: int, friendly_url: str) -> dict | None:
        return self.get(f"/o/headless-delivery/v1.0/sites/{site_id}/site-pages{friendly_url}")

    def create_site_page(self, site_id: int, body: dict) -> dict:
        return self.call("POST", f"/o/headless-delivery/v1.0/sites/{site_id}/site-pages", body)

    def upload_document(self, site_id: int, local_path: str, title: str, folder_id: int | None = None) -> dict:
        doc = {"title": title}
        path = (f"/o/headless-delivery/v1.0/document-folders/{folder_id}/documents" if folder_id
                else f"/o/headless-delivery/v1.0/sites/{site_id}/documents")
        with open(local_path, "rb") as fh:
            return self.call("POST", path, files={"file": (Path(local_path).name, fh.read())},
                             data={"document": json.dumps(doc)})

    def ensure_document_folder(self, site_id: int, name: str) -> dict:
        for f in self.paged(f"/o/headless-delivery/v1.0/sites/{site_id}/document-folders?filter=name eq '{name}'"):
            if f.get("name") == name:
                return f
        return self.call("POST", f"/o/headless-delivery/v1.0/sites/{site_id}/document-folders", {"name": name})

    def upsert_navigation_menu(self, site_id: int, name: str, items: list[dict]) -> dict:
        for menu in self.paged(f"/o/headless-delivery/v1.0/sites/{site_id}/navigation-menus"):
            if menu.get("name") == name:
                return self.call("PUT", f"/o/headless-delivery/v1.0/navigation-menus/{menu['id']}",
                                 {"name": name, "navigationMenuItems": items})
        return self.call("POST", f"/o/headless-delivery/v1.0/sites/{site_id}/navigation-menus",
                         {"name": name, "navigationMenuItems": items})
