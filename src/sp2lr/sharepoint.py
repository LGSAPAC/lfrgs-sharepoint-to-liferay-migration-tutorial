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
    "TaxonomyFieldType", "TaxonomyFieldTypeMulti",
}

# Libraries that SharePoint creates for its own purposes
SYSTEM_LIBRARIES = {"Site Assets", "Style Library", "Form Templates", "Preservation Hold Library",
                    "Site Pages", "Pages", "Images", "Site Collection Documents", "Site Collection Images"}

# Internal fields we never migrate as content columns
SYSTEM_FIELDS = {
    "ContentType", "Attachments", "Edit", "LinkTitle", "LinkTitleNoMenu",
    "DocIcon", "ItemChildCount", "FolderChildCount", "AppAuthor", "AppEditor",
    "_ComplianceFlags", "_ComplianceTag", "_ComplianceTagWrittenTime",
    "_ComplianceTagUserId", "_IsRecord", "ComplianceAssetId", "_ColorTag",
    # document library internals
    "_ExtendedDescription", "_dlc_DocId", "_dlc_DocIdUrl", "_dlc_DocIdPersistId", "TaxCatchAll",
    "TaxCatchAllLabel", "MediaServiceImageTags", "lcf76f155ced4ddcb4097134ff3c332f", "_CopySource",
    "CheckoutUser", "_CheckinComment", "_ModerationComments", "_ModerationStatus",
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

    def _file_url(self, server_relative_url: str) -> str:
        path = quote(server_relative_url.replace("'", "''"))
        return f"{self.web_url}/_api/web/GetFileByServerRelativeUrl('{path}')/$value"

    def download(self, server_relative_url: str) -> bytes:
        return self._request(self._file_url(server_relative_url), self.tokens.sharepoint_token(), "*/*", raw=True)

    def download_to(self, server_relative_url: str, target) -> int:
        """Stream a (possibly large) file to disk. Returns the number of bytes written."""
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(6):
            headers = {"Authorization": f"Bearer {self.tokens.sharepoint_token()}"}
            with self.http.stream("GET", self._file_url(server_relative_url), headers=headers) as r:
                if r.status_code in (429, 503):
                    time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
                    continue
                r.raise_for_status()
                size = 0
                with open(target, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
                        size += len(chunk)
                return size
        raise RuntimeError(f"Throttled downloading {server_relative_url}")

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
            "?$select=Id,FileLeafRef,HasUniqueRoleAssignments,Created,Modified,FirstPublishedDate,"
            "Author/EMail,Author/Title,Editor/EMail,Editor/Title&$expand=Author,Editor"
            f"&$filter=FileLeafRef eq '{page_name}'"
        ).get("value", [])
        return items[0] if items else {}

    # ----------------------------------------------------------------- lists
    def list_lists(self, include_hidden: bool = False) -> list[dict]:
        # BaseTemplate 100 = Custom List; 101 = Document Library; 119 = Site Pages
        select = ("Id,Title,Description,BaseTemplate,ItemCount,Hidden,HasUniqueRoleAssignments,EntityTypeName,"
                  "EnableModeration,EnableVersioning,EnableAttachments,ContentTypesEnabled,IsSystemList,IsCatalog,"
                  "RootFolder/ServerRelativeUrl")
        lists = self.rest(f"/web/lists?$select={select}&$expand=RootFolder").get("value", [])
        return [l for l in lists if include_hidden or not l.get("Hidden")]

    def list_fields(self, list_id: str) -> list[dict]:
        # No $select: subtype properties (Choices, LookupList, TermSetId, RichText...) are only
        # returned when the whole field resource is requested.
        fields = self.rest(f"/web/lists(guid'{list_id}')/fields").get("value", [])
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

    def list_items(self, list_id: str, fields: list[dict], top: int = 500,
                   library: bool = False) -> Iterator[dict]:
        select = ["Id", "Title", "Created", "Modified", "HasUniqueRoleAssignments",
                  "Author/EMail", "Author/Title", "Editor/EMail", "Editor/Title"]
        expand = {"Author", "Editor"}
        if library:
            # FSObjType 1 = folder, 0 = file
            select += ["FileLeafRef", "FileRef", "FileDirRef", "FSObjType", "File/Length", "File/UIVersionLabel"]
            expand.add("File")
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

    def list_views(self, list_id: str) -> list[dict]:
        """Public views with their columns and CAML query (sorting/filtering)."""
        views = self.rest(
            f"/web/lists(guid'{list_id}')/views?$select=Id,Title,DefaultView,PersonalView,Hidden,ViewQuery,RowLimit"
            "&$expand=ViewFields"
        ).get("value", [])
        out = []
        for v in views:
            if v.get("Hidden") or v.get("PersonalView"):
                continue
            fields = v.get("ViewFields") or {}
            v["fields"] = fields.get("Items", []) if isinstance(fields, dict) else []
            v.pop("ViewFields", None)
            out.append(v)
        return out

    # -------------------------------------------------------- managed metadata
    def term_set(self, term_set_id: str) -> dict:
        """A term set with its full term hierarchy (Graph termStore, needs TermStore.Read.All)."""
        base = f"/sites/{self.site_id}/termStore/sets/{term_set_id}"
        ts = self.graph(base)

        def children(path: str) -> list[dict]:
            out = []
            for term in self.graph_paged(f"{path}/children"):
                out.append({"id": term["id"], "labels": term.get("labels", []),
                            "children": children(f"{base}/terms/{term['id']}")})
            return out

        return {"id": ts["id"], "localizedNames": ts.get("localizedNames", []), "terms": children(base)}

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
