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
  term store      /_api/v2.1/termStore (SE), else the client object model
                  (/_vti_bin/client.svc/ProcessQuery) on 2019 and 2016
Sign-in is NTLM (DOMAIN\\user + password).

Classic pages (wiki pages in Site Pages, publishing pages in a Pages library) are
read on both platforms and returned as one-column pages with their HTML.
Fields are only $selected when the list has them, since SharePoint rejects a
$select of a missing column (e.g. CanvasContent1 on 2016).
"""

from __future__ import annotations

import re
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

# Classic page libraries and the field holding their HTML
CLASSIC_PAGE_SOURCES = {119: "WikiField", 850: "PublishingPageContent"}  # Site Pages (wiki), Pages (publishing)
TAXONOMY_SESSION_TYPE = "{981cbc68-9edc-4f8d-872f-71146fcbb84f}"  # SP.Taxonomy.TaxonomySession (client OM)

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
        self._fields_cache: dict[str, set[str]] = {}
        self._classic: dict[str, dict] | None = None

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

    def _post(self, url: str, body: bytes, headers: dict) -> httpx.Response:
        """POST used only for read requests that need it (form digest, client object model queries)."""
        for attempt in range(6):
            r = self.http.post(url, content=body, headers={**self._auth(self._sp_token()), **headers})
            if r.status_code in (429, 503):
                time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

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
        """Modern pages, then classic (wiki and publishing) pages."""
        if self.settings.is_server:
            modern = [_server_page(i, self.settings.site_url) for i in self._server_page_items()]
        else:
            modern = list(self.graph_paged(
                f"/sites/{self.site_id}/pages/microsoft.graph.sitePage"
                "?$select=id,name,title,webUrl,description,pageLayout,promotionKind,createdDateTime,lastModifiedDateTime,publishingState"
            ))
        classic = [{k: v for k, v in p.items() if k != "canvasLayout"} for p in self.classic_pages().values()]
        return modern + classic

    def _site_pages_list_id(self) -> str | None:
        return next((lst["Id"] for lst in self.list_lists() if lst["BaseTemplate"] == 119), None)

    def _field_names(self, list_id: str) -> set[str]:
        """Internal names of every field on a list, hidden ones included (to build a valid $select)."""
        if list_id not in self._fields_cache:
            fields = self.rest(f"/web/lists(guid'{list_id}')/fields?$select=InternalName").get("value", [])
            self._fields_cache[list_id] = {f["InternalName"] for f in fields}
        return self._fields_cache[list_id]

    def _select(self, list_id: str, wanted: list[str]) -> str:
        have = self._field_names(list_id) | {"Id", "Title", "Created", "Modified", "Author", "Editor",
                                              "HasUniqueRoleAssignments", "FileLeafRef", "FileRef"}
        return ",".join(w for w in wanted if w.split("/")[0] in have)

    def _server_page_items(self, item_id: str | None = None) -> list[dict]:
        list_id = self._site_pages_list_id()
        if not list_id or "CanvasContent1" not in self._field_names(list_id):
            return []  # SharePoint 2016 and older: no modern pages
        select = self._select(list_id, ["Id", "Title", "FileLeafRef", "FileRef", "Description", "PromotedState",
                                        "CanvasContent1", "Created", "Modified", "FirstPublishedDate"])
        if item_id:
            return [self.rest(f"/web/lists(guid'{list_id}')/items({int(item_id)})?$select={select}")]
        items = self.rest_paged(f"/web/lists(guid'{list_id}')/items?$select={select}&$top=500")
        # Modern pages only: classic wiki/web part pages have no canvas
        return [i for i in items if i.get("CanvasContent1")]

    def classic_pages(self) -> dict[str, dict]:
        """Wiki pages (Site Pages, WikiField) and publishing pages (Pages library, PublishingPageContent),
        as Graph-shaped pages with one text web part holding the page HTML. Cached."""
        if self._classic is not None:
            return self._classic
        self._classic = {}
        host = "/".join(self.settings.site_url.split("/", 3)[:3])
        for lst in self.list_lists():
            html_field = CLASSIC_PAGE_SOURCES.get(lst["BaseTemplate"])
            if not html_field or html_field not in self._field_names(lst["Id"]):
                continue
            select = self._select(lst["Id"], ["Id", "Title", "FileLeafRef", "FileRef", html_field, "CanvasContent1",
                                              "Created", "Modified"])
            for item in self.rest_paged(f"/web/lists(guid'{lst['Id']}')/items?$select={select}&$top=500"):
                body = item.get(html_field)
                if item.get("CanvasContent1") or not body or not (item.get("FileLeafRef") or "").endswith(".aspx"):
                    continue  # modern page, empty page, or not a page
                page_id = f"classic-{lst['Id']}-{item['Id']}"
                self._classic[page_id] = {
                    "id": page_id, "name": item["FileLeafRef"],
                    "title": item.get("Title") or item["FileLeafRef"].rsplit(".", 1)[0].replace("-", " "),
                    "description": "", "webUrl": f"{host}{item.get('FileRef', '')}", "promotionKind": "page",
                    "pageLayout": "classic", "createdDateTime": item.get("Created"),
                    "lastModifiedDateTime": item.get("Modified"), "_listId": lst["Id"],
                    "canvasLayout": {"horizontalSections": [{"layout": "oneColumn", "columns": [
                        {"width": 12, "webparts": [{"@odata.type": "#microsoft.graph.textWebPart",
                                                    "innerHtml": body}]}]}]},
                }
        return self._classic

    def get_page(self, page_id: str) -> dict:
        if page_id.startswith("classic-"):
            return dict(self.classic_pages()[page_id])
        if self.settings.is_server:
            return _server_page(self._server_page_items(page_id)[0], self.settings.site_url, with_canvas=True)
        return self.graph(
            f"/sites/{self.site_id}/pages/{page_id}/microsoft.graph.sitePage?$expand=canvasLayout"
        )

    def page_list_item(self, page_name: str, list_id: str | None = None) -> dict:
        """The list item behind a page (Created/Modified/Author and unique permissions)."""
        list_id = list_id or self._site_pages_list_id()
        if not list_id:
            return {}
        select = self._select(list_id, ["Id", "FileLeafRef", "HasUniqueRoleAssignments", "Created", "Modified",
                                        "FirstPublishedDate", "Author/EMail", "Author/Title",
                                        "Editor/EMail", "Editor/Title"])
        name = page_name.replace("'", "''")
        items = self.rest(
            f"/web/lists(guid'{list_id}')/items?$select={select}&$expand=Author,Editor"
            f"&$filter=FileLeafRef eq '{name}'"
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
            try:
                get(base)
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (400, 404):
                    raise
                return self._csom_term_set(term_set_id)  # 2019 / 2016: no v2.1 term store REST
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

    def _csom_term_set(self, term_set_id: str) -> dict:
        """Term set through the client object model (SharePoint 2013-2019): TaxonomySession ->
        default site collection term store -> GetTermSet -> GetAllTerms. The hierarchy comes from
        each term's PathOfTerm ("Sales;EMEA"). Read-only, but CSOM needs a POST and a form digest."""
        digest = self._post(f"{self.web_url}/_api/contextinfo", b"",
                            {"Accept": "application/json;odata=nometadata"}).json()["FormDigestValue"]
        xml = (
            '<Request xmlns="http://schemas.microsoft.com/sharepoint/clientquery/2009" SchemaVersion="15.0.0.0" '
            'LibraryVersion="16.0.0.0" ApplicationName="sp2lr"><Actions>'
            '<Query Id="11" ObjectPathId="5"><Query SelectAllProperties="false"><Properties>'
            '<Property Name="Id" ScalarProperty="true"/><Property Name="Name" ScalarProperty="true"/>'
            '</Properties></Query></Query>'
            '<Query Id="12" ObjectPathId="7"><Query SelectAllProperties="false"><Properties/></Query>'
            '<ChildItemQuery SelectAllProperties="false"><Properties>'
            '<Property Name="Id" ScalarProperty="true"/><Property Name="Name" ScalarProperty="true"/>'
            '<Property Name="PathOfTerm" ScalarProperty="true"/></Properties></ChildItemQuery></Query>'
            '</Actions><ObjectPaths>'
            f'<StaticMethod Id="1" Name="GetTaxonomySession" TypeId="{TAXONOMY_SESSION_TYPE}"/>'
            '<Method Id="3" ParentId="1" Name="GetDefaultSiteCollectionTermStore"/>'
            f'<Method Id="5" ParentId="3" Name="GetTermSet"><Parameters><Parameter Type="Guid">{{{term_set_id}}}'
            '</Parameter></Parameters></Method>'
            '<Method Id="7" ParentId="5" Name="GetAllTerms"/>'
            '</ObjectPaths></Request>')
        r = self._post(f"{self.web_url}/_vti_bin/client.svc/ProcessQuery", xml.encode(),
                       {"Content-Type": "text/xml", "X-RequestDigest": digest})
        return parse_csom_term_set(r.json(), term_set_id)

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


def _csom_guid(value: str) -> str:
    """CSOM serialises GUIDs as "/Guid(…)/"."""
    m = re.search(r"Guid\(([^)]+)\)", value or "")
    return (m.group(1) if m else value or "").lower()


def parse_csom_term_set(response: list, term_set_id: str) -> dict:
    """ProcessQuery response -> the Graph term set shape {id, localizedNames, terms: [{id, labels, children}]}.

    The response is [header, actionId, result, actionId, result, …]; the header's ErrorInfo is set on failure."""
    header = response[0] if response else {}
    if header.get("ErrorInfo"):
        raise RuntimeError(f"Client object model error: {header['ErrorInfo'].get('ErrorMessage')}")
    results = dict(zip(response[1::2], response[2::2]))
    ts = results.get(11) or {}
    terms = (results.get(12) or {}).get("_Child_Items_", [])
    nodes: dict[str, dict] = {}
    roots: list[dict] = []
    # Parents come before children once sorted by depth; PathOfTerm uses ";" between levels
    for t in sorted(terms, key=lambda x: (x.get("PathOfTerm") or "").count(";")):
        path = t.get("PathOfTerm") or t.get("Name") or ""
        node = {"id": _csom_guid(t.get("Id", "")), "labels": [{"name": t.get("Name"), "isDefault": True}],
                "children": []}
        nodes[path] = node
        parent = nodes.get(path.rsplit(";", 1)[0]) if ";" in path else None
        (parent["children"] if parent else roots).append(node)
    return {"id": _csom_guid(ts.get("Id", "")) or term_set_id.lower(),
            "localizedNames": [{"name": ts.get("Name"), "languageTag": "en-US"}] if ts.get("Name") else [],
            "terms": roots}
