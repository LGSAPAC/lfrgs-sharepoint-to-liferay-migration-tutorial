"""Read-only client for a SharePoint site, Online or Server.

SharePoint Online (SP_PLATFORM=online): Microsoft Graph is used for site
resolution, modern pages (canvasLayout), Entra users/groups and the term store.
The SharePoint REST API is used where Graph is incomplete: list field schemas,
list items with person/lookup expansion, SharePoint groups and role assignments.

SharePoint Server Subscription Edition (SP_PLATFORM=server) has no Graph, so the
same methods read everything through REST and return it in the Graph shape, so
extract, plan and load work unchanged:
  site            /_api/site + /_api/web
  modern pages    Site Pages items, CanvasContent1 converted to canvasLayout
  users           site users + PeopleManager (Active Directory, not Entra ID)
  AD groups       not expandable over REST: kept as empty user groups (warning)
  term store      /_api/v2.1/termStore (falls back to labels seen on items)
Sign-in is NTLM (DOMAIN\\user + password).
"""

from __future__ import annotations

import time
from typing import Any, Iterator
from urllib.parse import quote

import httpx

from .auth import TokenProvider
from .config import SharePointSettings

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
        auth = None
        if settings.is_server and settings.auth_mode == "ntlm":
            from httpx_ntlm import HttpNtlmAuth
            auth = HttpNtlmAuth(settings.username, settings.password)
        self.http = httpx.Client(timeout=60, auth=auth)
        self._site_id: str | None = None

    def _auth(self, token: str | None) -> dict:
        # SharePoint Server signs in with NTLM on the client; Online sends a bearer token
        return {} if self.settings.is_server or not token else {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------ http
    def _request(self, url: str, token: str | None, accept: str, raw: bool = False) -> Any:
        for attempt in range(6):
            r = self.http.get(url, headers={**self._auth(token), "Accept": accept})
            if r.status_code in (429, 503):
                # Honour throttling (SharePoint and Graph both send Retry-After)
                time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            r.raise_for_status()
            return r.content if raw else r.json()
        r.raise_for_status()

    def graph(self, path: str) -> Any:
        if self.settings.is_server:
            raise RuntimeError("Microsoft Graph isn't available on SharePoint Server")
        url = path if path.startswith("http") else f"{self.settings.graph_url}{path}"
        return self._request(url, self.tokens.graph_token(), "application/json")

    def graph_paged(self, path: str) -> Iterator[dict]:
        url: str | None = path if path.startswith("http") else f"{self.settings.graph_url}{path}"
        while url:
            page = self.graph(url)
            yield from page.get("value", [])
            url = page.get("@odata.nextLink")

    @property
    def web_url(self) -> str:
        return self.settings.site_url.rstrip("/")

    def rest(self, path: str) -> Any:
        url = path if path.startswith("http") else f"{self.web_url}/_api{path}"
        token = None if self.settings.is_server else self.tokens.sharepoint_token()
        return self._request(url, token, "application/json;odata=nometadata")

    def rest_paged(self, path: str) -> Iterator[dict]:
        url: str | None = path if path.startswith("http") else f"{self.web_url}/_api{path}"
        while url:
            page = self.rest(url)
            yield from page.get("value", [])
            url = page.get("odata.nextLink")

    def _file_url(self, server_relative_url: str) -> str:
        path = quote(server_relative_url.replace("'", "''"))
        return f"{self.web_url}/_api/web/GetFileByServerRelativeUrl('{path}')/$value"

    def _sp_token(self) -> str | None:
        return None if self.settings.is_server else self.tokens.sharepoint_token()

    def download(self, server_relative_url: str) -> bytes:
        return self._request(self._file_url(server_relative_url), self._sp_token(), "*/*", raw=True)

    def download_to(self, server_relative_url: str, target) -> int:
        """Stream a (possibly large) file to disk. Returns the number of bytes written."""
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(6):
            headers = self._auth(self._sp_token())
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
        if self.settings.is_server:
            # Same "host,siteId,webId" shape as Graph's site id, so ERCs work the same way
            site_id = self.rest("/site?$select=Id")["Id"]
            web = self.rest("/web?$select=Id,Title,Url")
            data = {"id": f"{self.settings.hostname},{site_id},{web['Id']}", "displayName": web["Title"],
                    "webUrl": web["Url"], "name": self.settings.site_path.rsplit("/", 1)[-1]}
        else:
            data = self.graph(f"/sites/{self.settings.hostname}:{self.settings.site_path}")
        self._site_id = data["id"]
        return data

    @property
    def site_id(self) -> str:
        if not self._site_id:
            self.site()
        return self._site_id  # type: ignore[return-value]

    def search_sites(self, query: str) -> list[dict]:
        if self.settings.is_server:
            return [self.site()]  # no tenant-wide site search without Graph: the configured site
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
        if self.settings.is_server:
            return [_server_page(i, self.settings.site_url) for i in self._server_page_items()]
        return list(self.graph_paged(
            f"/sites/{self.site_id}/pages/microsoft.graph.sitePage"
            "?$select=id,name,title,webUrl,description,pageLayout,promotionKind,createdDateTime,lastModifiedDateTime,publishingState"
        ))

    def _server_page_items(self, item_id: str | None = None) -> list[dict]:
        select = ("Id,Title,FileLeafRef,FileRef,Description,PromotedState,CanvasContent1,Created,Modified,"
                  "FirstPublishedDate,ClientSideApplicationId")
        if item_id:
            return [self.rest(f"/web/lists/getbytitle('Site Pages')/items({int(item_id)})?$select={select}")]
        items = self.rest_paged(f"/web/lists/getbytitle('Site Pages')/items?$select={select}&$top=500")
        # Modern pages only: classic wiki/web part pages have no canvas
        return [i for i in items if i.get("CanvasContent1") is not None]

    def get_page(self, page_id: str) -> dict:
        if self.settings.is_server:
            return _server_page(self._server_page_items(page_id)[0], self.settings.site_url, with_canvas=True)
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
        """A term set with its full term hierarchy (Graph termStore, needs TermStore.Read.All).
        SharePoint Server: the same resource shape from /_api/v2.1/termStore."""
        if self.settings.is_server:
            base = f"{self.web_url}/_api/v2.1/termStore/sets/{term_set_id}"
            get, paged = self.rest, self.rest_paged
        else:
            base = f"/sites/{self.site_id}/termStore/sets/{term_set_id}"
            get, paged = self.graph, self.graph_paged
        ts = get(base)

        def children(path: str) -> list[dict]:
            out = []
            for term in paged(f"{path}/children"):
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
        """Profile of a user: Entra ID on Online, Active Directory (via SharePoint) on Server."""
        if self.settings.is_server:
            return self._server_user(upn_or_id)
        try:
            return self.graph(
                f"/users/{quote(upn_or_id)}?$select=id,displayName,givenName,surname,mail,"
                "userPrincipalName,jobTitle,department,accountEnabled,preferredLanguage"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def _server_user(self, account: str) -> dict | None:
        """DOMAIN\\user -> a Graph-shaped profile from the site user and the user profile service."""
        login = account if "|" in account else f"i:0#.w|{account}"
        enc = quote(login.replace("'", "''"))
        try:
            u = self.rest(f"/web/siteusers(@v)?@v='{enc}'&$select=Id,Title,Email,LoginName,UserPrincipalName")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        props: dict[str, str] = {}
        try:  # needs the User Profile Service; optional
            pm = self.rest(f"/SP.UserProfiles.PeopleManager/GetPropertiesFor(accountName=@v)?@v='{enc}'")
            props = {p["Key"]: p.get("Value") or "" for p in pm.get("UserProfileProperties", [])}
            props.setdefault("Title", pm.get("Title") or "")
        except httpx.HTTPStatusError:
            pass
        name = (u.get("Title") or "").split(" ", 1)
        acct = login.split("|", 1)[-1]
        return {
            "id": "ad-" + "".join(c if c.isalnum() else "-" for c in acct.lower()),
            "displayName": u.get("Title"), "mail": u.get("Email") or None,
            "userPrincipalName": u.get("UserPrincipalName") or u.get("Email") or acct,
            "givenName": props.get("FirstName") or name[0], "surname": props.get("LastName") or (name[1] if len(name) > 1 else ""),
            "jobTitle": props.get("Title") or props.get("SPS-JobTitle") or "", "department": props.get("Department") or "",
            "accountEnabled": True, "onPremisesSamAccountName": acct,
        }

    def entra_group_members(self, group_id: str, owners: bool = False) -> list[dict]:
        if self.settings.is_server:
            return []  # AD group membership isn't exposed over SharePoint REST
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
    i:0#.w|contoso\\jane                           -> user (SharePoint Server, Windows)
    c:0+.w|s-1-5-21-…                              -> Active Directory group (SharePoint Server)
    c:0!.s|windows                                 -> "All Authenticated Users" (SharePoint Server)
    """
    if login_name.startswith("i:0#.f|membership|"):
        return {"kind": "user", "upn": login_name.split("|", 2)[2]}
    if login_name.startswith("i:0#.w|"):  # SharePoint Server: Windows user, DOMAIN\user
        return {"kind": "user", "upn": login_name.split("|", 1)[1], "windows": True}
    if login_name.startswith("c:0+.w|"):  # SharePoint Server: Active Directory group, by SID
        return {"kind": "adgroup", "groupId": login_name.split("|", 1)[1]}
    if login_name in ("c:0!.s|windows", "c:0!.s|forms:aspnetsqlmembershipprovider"):
        return {"kind": "everyone"}  # "All Authenticated Users"
    if "|federateddirectoryclaimprovider|" in login_name:
        gid = login_name.rsplit("|", 1)[1]
        owners = gid.endswith("_o")
        return {"kind": "m365group", "groupId": gid[:-2] if owners else gid, "owners": owners}
    if login_name.startswith("c:0t.c|tenant|"):
        return {"kind": "securitygroup", "groupId": login_name.rsplit("|", 1)[1]}
    if "spo-grid-all-users" in login_name or login_name == "c:0(.s|true":
        return {"kind": "everyone"}
    return {"kind": "other", "login": login_name}


def _server_page(item: dict, site_url: str, with_canvas: bool = False) -> dict:
    """A Site Pages list item from SharePoint Server, in the Graph sitePage shape."""
    from .transform import canvas_to_layout

    host = "/".join(site_url.split("/", 3)[:3])
    page = {
        "id": str(item["Id"]), "name": item.get("FileLeafRef"), "title": item.get("Title"),
        "description": item.get("Description"), "webUrl": f"{host}{item.get('FileRef', '')}",
        "promotionKind": "newsPost" if item.get("PromotedState") == 2 else "page",
        "createdDateTime": item.get("Created"), "lastModifiedDateTime": item.get("Modified"),
    }
    if with_canvas:
        page["canvasLayout"] = canvas_to_layout(item.get("CanvasContent1") or "")
    return page
