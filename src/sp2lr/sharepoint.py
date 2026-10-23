"""Read-only client for a SharePoint Online site.

Microsoft Graph is used for site resolution, modern pages (canvasLayout) and
Entra users/groups. The SharePoint REST API is used where Graph is incomplete:
list field schemas, list items with person/lookup expansion, SharePoint groups
and role assignments (permissions).
"""

from __future__ import annotations

import time
from typing import Any, Iterator
from urllib.parse import quote

import httpx

from .auth import TokenProvider
from .config import SharePointSettings

GRAPH = "https://graph.microsoft.com/v1.0"

# SharePoint REST field types we know how to migrate (TypeAsString)
SUPPORTED_FIELD_TYPES = {
    "Text", "Note", "Number", "Currency", "DateTime", "Boolean", "Choice",
    "MultiChoice", "Lookup", "LookupMulti", "User", "UserMulti", "URL",
}

# Internal fields we never migrate as content columns
SYSTEM_FIELDS = {
    "ContentType", "Attachments", "Edit", "LinkTitle", "LinkTitleNoMenu",
    "DocIcon", "ItemChildCount", "FolderChildCount", "AppAuthor", "AppEditor",
    "_ComplianceFlags", "_ComplianceTag", "_ComplianceTagWrittenTime",
    "_ComplianceTagUserId", "_IsRecord", "ComplianceAssetId", "_ColorTag",
}


class SharePointClient:
    def __init__(self, settings: SharePointSettings, tokens: TokenProvider | None = None):
        self.settings = settings
        self.tokens = tokens or TokenProvider(settings)
        self.http = httpx.Client(timeout=60)
        self._site_id: str | None = None

    # ------------------------------------------------------------------ http
    def _request(self, url: str, token: str, accept: str, raw: bool = False) -> Any:
        for attempt in range(6):
            r = self.http.get(url, headers={"Authorization": f"Bearer {token}", "Accept": accept})
            if r.status_code in (429, 503):
                # Honour throttling (SharePoint and Graph both send Retry-After)
                time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            r.raise_for_status()
            return r.content if raw else r.json()
        r.raise_for_status()

    def graph(self, path: str) -> Any:
        url = path if path.startswith("http") else f"{GRAPH}{path}"
        return self._request(url, self.tokens.graph_token(), "application/json")

    def graph_paged(self, path: str) -> Iterator[dict]:
        url: str | None = path if path.startswith("http") else f"{GRAPH}{path}"
        while url:
            page = self.graph(url)
            yield from page.get("value", [])
            url = page.get("@odata.nextLink")

    @property
    def web_url(self) -> str:
        return self.settings.site_url.rstrip("/")

    def rest(self, path: str) -> Any:
        url = path if path.startswith("http") else f"{self.web_url}/_api{path}"
        return self._request(url, self.tokens.sharepoint_token(), "application/json;odata=nometadata")

    def rest_paged(self, path: str) -> Iterator[dict]:
        url: str | None = path if path.startswith("http") else f"{self.web_url}/_api{path}"
        while url:
            page = self.rest(url)
            yield from page.get("value", [])
            url = page.get("odata.nextLink")

    def download(self, server_relative_url: str) -> bytes:
        path = quote(server_relative_url.replace("'", "''"))
        url = f"{self.web_url}/_api/web/GetFileByServerRelativeUrl('{path}')/$value"
        return self._request(url, self.tokens.sharepoint_token(), "*/*", raw=True)

    # ------------------------------------------------------------------ site
    def site(self) -> dict:
        data = self.graph(f"/sites/{self.settings.hostname}:{self.settings.site_path}")
        self._site_id = data["id"]
        return data

    @property
    def site_id(self) -> str:
        if not self._site_id:
            self.site()
        return self._site_id  # type: ignore[return-value]

    def search_sites(self, query: str) -> list[dict]:
        return list(self.graph_paged(f"/sites?search={quote(query or '*')}"))

    def web(self) -> dict:
        return self.rest("/web?$select=Id,Title,Description,Url,ServerRelativeUrl,Language,HasUniqueRoleAssignments,WelcomePage")

    def navigation(self) -> dict:
        expand = "$expand=Children,Children/Children"
        return {
            "quickLaunch": self.rest(f"/web/navigation/QuickLaunch?{expand}").get("value", []),
            "topNavigationBar": self.rest(f"/web/navigation/TopNavigationBar?{expand}").get("value", []),
        }

    # ----------------------------------------------------------------- pages
    def list_pages(self) -> list[dict]:
        return list(self.graph_paged(
            f"/sites/{self.site_id}/pages/microsoft.graph.sitePage"
            "?$select=id,name,title,webUrl,description,pageLayout,promotionKind,createdDateTime,lastModifiedDateTime,publishingState"
        ))

    def get_page(self, page_id: str) -> dict:
        return self.graph(
            f"/sites/{self.site_id}/pages/{page_id}/microsoft.graph.sitePage?$expand=canvasLayout"
        )

    def page_list_item(self, page_name: str) -> dict:
        """The Site Pages list item behind a page (for unique permissions)."""
        items = self.rest(
            "/web/lists/getbytitle('Site Pages')/items"
            f"?$select=Id,FileLeafRef,HasUniqueRoleAssignments&$filter=FileLeafRef eq '{page_name}'"
        ).get("value", [])
        return items[0] if items else {}

    # ----------------------------------------------------------------- lists
    def list_lists(self, include_hidden: bool = False) -> list[dict]:
        # BaseTemplate 100 = Custom List; 101 = Document Library (out of scope, see docs)
        select = "Id,Title,Description,BaseTemplate,ItemCount,Hidden,HasUniqueRoleAssignments,EntityTypeName,RootFolder/ServerRelativeUrl"
        lists = self.rest(f"/web/lists?$select={select}&$expand=RootFolder").get("value", [])
        return [l for l in lists if include_hidden or not l.get("Hidden")]

    def list_fields(self, list_id: str) -> list[dict]:
        select = (
            "Id,InternalName,Title,TypeAsString,Required,Hidden,ReadOnlyField,Description,"
            "Choices,LookupList,LookupField,AllowMultipleValues,RichText,DisplayFormat,"
            "MaxLength,MinimumValue,MaximumValue,Decimals,Indexed,EnforceUniqueValues,FromBaseType,DefaultValue"
        )
        fields = self.rest(f"/web/lists(guid'{list_id}')/fields?$select={select}").get("value", [])
        out = []
        for f in fields:
            if f.get("Hidden") or f["InternalName"] in SYSTEM_FIELDS:
                continue
            if f.get("ReadOnlyField") and f["InternalName"] != "Title":
                continue
            if f.get("FromBaseType") and f["InternalName"] != "Title":
                continue
            out.append(f)
        return out

    def list_items(self, list_id: str, fields: list[dict], top: int = 500) -> Iterator[dict]:
        select = ["Id", "Title", "Created", "Modified", "HasUniqueRoleAssignments",
                  "Author/EMail", "Author/Title", "Editor/EMail", "Editor/Title"]
        expand = {"Author", "Editor"}
        for f in fields:
            name, kind = f["InternalName"], f["TypeAsString"]
            if kind in ("User", "UserMulti"):
                select += [f"{name}/EMail", f"{name}/Title", f"{name}/Id"]
                expand.add(name)
            elif kind in ("Lookup", "LookupMulti"):
                select += [f"{name}/Id", f"{name}/{f.get('LookupField') or 'Title'}"]
                expand.add(name)
            elif name not in select:
                select.append(name)
        query = f"$top={top}&$select={','.join(select)}&$expand={','.join(sorted(expand))}"
        yield from self.rest_paged(f"/web/lists(guid'{list_id}')/items?{query}")

    def item_attachments(self, list_id: str, item_id: int) -> list[dict]:
        return self.rest(f"/web/lists(guid'{list_id}')/items({item_id})/AttachmentFiles").get("value", [])

    # ---------------------------------------------------------- users/groups
    def site_users(self) -> list[dict]:
        return self.rest(
            "/web/siteusers?$select=Id,Title,Email,LoginName,PrincipalType,IsSiteAdmin,UserPrincipalName"
        ).get("value", [])

    def site_groups(self) -> list[dict]:
        groups = self.rest(
            "/web/sitegroups?$select=Id,Title,Description,LoginName,OwnerTitle&$expand=Users"
        ).get("value", [])
        assoc = {}
        for key in ("AssociatedOwnerGroup", "AssociatedMemberGroup", "AssociatedVisitorGroup"):
            try:
                assoc[self.rest(f"/web/{key}?$select=Id")["Id"]] = key
            except httpx.HTTPStatusError:
                pass
        for g in groups:
            g["associatedAs"] = assoc.get(g["Id"])
        return groups

    def entra_user(self, upn_or_id: str) -> dict | None:
        try:
            return self.graph(
                f"/users/{quote(upn_or_id)}?$select=id,displayName,givenName,surname,mail,"
                "userPrincipalName,jobTitle,department,accountEnabled,preferredLanguage"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def entra_group_members(self, group_id: str, owners: bool = False) -> list[dict]:
        rel = "owners" if owners else "transitiveMembers"
        return [
            m for m in self.graph_paged(
                f"/groups/{group_id}/{rel}?$select=id,displayName,mail,userPrincipalName"
            )
            if m.get("@odata.type") == "#microsoft.graph.user"
        ]

    # ----------------------------------------------------------- permissions
    def role_definitions(self) -> list[dict]:
        return self.rest("/web/roledefinitions?$select=Id,Name,RoleTypeKind,Hidden,Description").get("value", [])

    def role_assignments(self, scope: str = "web", list_id: str | None = None, item_id: int | None = None) -> list[dict]:
        """scope: 'web' | 'list' | 'item'."""
        base = "/web"
        if scope in ("list", "item"):
            base += f"/lists(guid'{list_id}')"
        if scope == "item":
            base += f"/items({item_id})"
        data = self.rest(
            f"{base}/roleassignments?$expand=Member,RoleDefinitionBindings"
            "&$select=PrincipalId,Member/Id,Member/Title,Member/LoginName,Member/PrincipalType,"
            "RoleDefinitionBindings/Name,RoleDefinitionBindings/RoleTypeKind"
        )
        return data.get("value", [])

    def items_with_unique_permissions(self, list_id: str) -> list[int]:
        ids = []
        for item in self.rest_paged(
            f"/web/lists(guid'{list_id}')/items?$select=Id,HasUniqueRoleAssignments&$top=2000"
        ):
            if item.get("HasUniqueRoleAssignments"):
                ids.append(item["Id"])
        return ids


def parse_login(login_name: str) -> dict:
    """Decode SharePoint claims login names.

    i:0#.f|membership|jane@contoso.com            -> user
    c:0o.c|federateddirectoryclaimprovider|<gid>   -> M365 group members
    c:0o.c|federateddirectoryclaimprovider|<gid>_o -> M365 group owners
    c:0t.c|tenant|<gid>                            -> Entra security group
    c:0-.f|rolemanager|spo-grid-all-users/<tid>    -> "Everyone except external users"
    """
    if login_name.startswith("i:0#.f|membership|"):
        return {"kind": "user", "upn": login_name.split("|", 2)[2]}
    if "|federateddirectoryclaimprovider|" in login_name:
        gid = login_name.rsplit("|", 1)[1]
        owners = gid.endswith("_o")
        return {"kind": "m365group", "groupId": gid[:-2] if owners else gid, "owners": owners}
    if login_name.startswith("c:0t.c|tenant|"):
        return {"kind": "securitygroup", "groupId": login_name.rsplit("|", 1)[1]}
    if "spo-grid-all-users" in login_name or login_name == "c:0(.s|true":
        return {"kind": "everyone"}
    return {"kind": "other", "login": login_name}
