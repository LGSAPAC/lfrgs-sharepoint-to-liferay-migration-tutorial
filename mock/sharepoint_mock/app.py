"""Mock SharePoint for developing and testing sp2lr without a tenant or a farm.

  MOCK_MODE=online   SharePoint Online: Microsoft Graph under /v1.0 + SharePoint REST,
                     bearer token "sp2lr-mock-token" (sp2lr: SP_AUTH_MODE=none)
  MOCK_MODE=server   SharePoint Server Subscription Edition: SharePoint REST only, NTLM sign-in
                     checked for real against MOCK_NTLM_USER (DOMAIN\\user) / MOCK_NTLM_PASSWORD

Read-only, like the real client: every route is a GET. Responses follow the shapes in
scenario.py. Lists page at MOCK_PAGE_SIZE items (default 1) so OData paging is exercised.
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
from urllib.parse import quote, unquote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import scenario

MOCK_TOKEN = "sp2lr-mock-token"


def _odata(value) -> JSONResponse:
    return JSONResponse(value)


def _not_found(msg: str = "Not found") -> JSONResponse:
    return JSONResponse({"odata.error": {"code": "-2147024894, System.IO.FileNotFoundException",
                                         "message": {"lang": "en-US", "value": msg}}}, status_code=404)


def create_app(mode: str | None = None, site_url: str | None = None) -> Starlette:
    mode = mode or os.environ.get("MOCK_MODE", "online")
    site_url = site_url or os.environ.get("MOCK_SITE_URL", "http://localhost:8091/sites/marketing")
    page_size = int(os.environ.get("MOCK_PAGE_SIZE", "1"))
    data = scenario.build(mode, site_url)
    path = data["path"]
    server = mode == "server"
    ntlm = _NtlmGate() if server else None

    def paged(request: Request, items: list[dict]) -> JSONResponse:
        """OData paging the way SharePoint REST does it: $skiptoken=Paged=TRUE&p_ID=<last id>."""
        top = min(int(request.query_params.get("$top", page_size)), page_size)
        token = request.query_params.get("$skiptoken", "")
        after = int(re.search(r"p_ID=(\d+)", token).group(1)) if "p_ID=" in token else 0
        rest = [i for i in items if i.get("Id", 0) > after]
        page = rest[:top]
        out = {"value": page}
        if len(rest) > top:
            q = {k: v for k, v in request.query_params.items() if k != "$skiptoken"}
            qs = "&".join(f"{k}={quote(v, safe=',/()$=')}" for k, v in q.items())
            out["odata.nextLink"] = (f"{str(request.url).split('?')[0]}?{qs}"
                                     f"&$skiptoken={quote('Paged=TRUE&p_ID=' + str(page[-1]['Id']))}")
        return _odata(out)

    def list_by(guid: str) -> dict | None:
        return next((x for x in data["lists"] if x["Id"].lower() == guid.lower()), None)

    def at(request: Request, name: str = "@v") -> str:
        return unquote(request.query_params.get(name, "")).strip("'").replace("''", "'")

    # ---------------------------------------------------------------- REST
    def rest(request: Request, p: str) -> Response:
        if p == "/site":
            return _odata({"Id": data["siteId"], "Url": data["web"]["Url"]})
        if p == "/web":
            return _odata(data["web"])
        if m := re.fullmatch(r"/web/navigation/(QuickLaunch|TopNavigationBar)", p):
            return _odata({"value": data["navigation"][m.group(1)]})
        if p == "/web/lists":
            return _odata({"value": data["lists"]})
        if p == "/web/siteusers":
            return _odata({"value": data["siteUsers"]})
        if p == "/web/siteusers(@v)":
            u = next((x for x in data["siteUsers"] if x["LoginName"].lower() == at(request).lower()), None)
            return _odata(u) if u else _not_found("User cannot be found.")
        if p == "/web/sitegroups":
            return _odata({"value": data["groups"]})
        if m := re.fullmatch(r"/web/(Associated(?:Owner|Member|Visitor)Group)", p):
            return _odata({"Id": data["associated"][m.group(1)]})
        if p == "/web/roledefinitions":
            return _odata({"value": data["roleDefinitions"]})
        if p == "/web/roleassignments":
            return _odata({"value": data["roleAssignments"]["web"]})
        if p == "/SP.UserProfiles.PeopleManager/GetPropertiesFor(accountName=@v)" and server:
            key = at(request).rsplit("\\", 1)[-1].lower()
            prof = data["profiles"].get(key)
            if not prof:
                return _not_found()
            props = {"FirstName": prof["givenName"], "LastName": prof["surname"], "Department": prof["department"],
                     "SPS-JobTitle": prof["jobTitle"], "WorkEmail": prof["mail"]}
            return _odata({"AccountName": at(request), "DisplayName": prof["displayName"], "Email": prof["mail"],
                           "Title": prof["jobTitle"],
                           "UserProfileProperties": [{"Key": k, "Value": v, "ValueType": "Edm.String"}
                                                     for k, v in props.items()]})
        # Site Pages by title
        if m := re.fullmatch(r"/web/lists/getbytitle\('Site Pages'\)(/items(?:\((\d+)\))?)?", p):
            if not m.group(1):
                return _odata({"Id": scenario.SITE_PAGES})
            if m.group(2):
                it = next((i for i in data["sitePageItems"] if i["Id"] == int(m.group(2))), None)
                return _odata(it) if it else _not_found()
            items = data["sitePageItems"]
            f = re.search(r"FileLeafRef eq '([^']*)'", request.query_params.get("$filter", ""))
            if f:
                items = [i for i in items if i["FileLeafRef"] == f.group(1)]
            return paged(request, items)
        # Lists by GUID
        if m := re.fullmatch(r"/web/lists\(guid'([0-9a-fA-F-]+)'\)(/.*)?", p):
            lst, sub = list_by(m.group(1)), m.group(2) or ""
            if not lst:
                return _not_found("List does not exist.")
            lid = lst["Id"]
            if sub == "/fields":
                return _odata({"value": data["fields"].get(lid, [])})
            if sub == "/views":
                return _odata({"value": data["views"].get(lid, [])})
            if sub == "/items":
                return paged(request, data["items"].get(lid, []))
            if sub == "/roleassignments":
                return _odata({"value": data["roleAssignments"].get(lid, [])})
            if mi := re.fullmatch(r"/items\((\d+)\)(/AttachmentFiles|/roleassignments)?", sub):
                iid = int(mi.group(1))
                if mi.group(2) == "/AttachmentFiles":
                    return _odata({"value": data["attachments"].get((lid, iid), [])})
                if mi.group(2) == "/roleassignments":
                    return _odata({"value": data["roleAssignments"].get((lid, iid), [])})
                it = next((i for i in data["items"].get(lid, []) if i["Id"] == iid), None)
                return _odata(it) if it else _not_found()
            return _odata(lst)
        if m := re.fullmatch(r"/web/GetFileByServerRelativeUrl\('(.*)'\)/\$value", p):
            body = data["files"].get(unquote(m.group(1)).replace("''", "'"))
            return Response(body, media_type="application/octet-stream") if body is not None else _not_found("File Not Found.")
        # Term store over REST (SharePoint Server); same resource shapes as Graph
        if server and (m := re.fullmatch(r"/v2\.1/termStore/sets/([0-9a-fA-F-]+)(/.*)?", p)):
            return termstore(m.group(1), m.group(2) or "")
        return _not_found(f"Unknown REST resource {p}")

    def termstore(set_id: str, sub: str) -> Response:
        ts = data["termSet"]
        if set_id.lower() != ts["id"].lower():
            return _not_found("Term set not found")
        if sub == "":
            return _odata({"id": ts["id"], "localizedNames": ts["localizedNames"]})

        def strip(t):
            return {"id": t["id"], "labels": t["labels"]}

        if sub == "/children":
            return _odata({"value": [strip(t) for t in ts["terms"]]})
        if mt := re.fullmatch(r"/terms/([^/]+)/children", sub):
            def find(terms):
                for t in terms:
                    if t["id"] == mt.group(1):
                        return t
                    if (hit := find(t["children"])) is not None:
                        return hit
                return None
            t = find(ts["terms"])
            return _odata({"value": [strip(c) for c in t["children"]]}) if t else _not_found()
        return _not_found()

    # --------------------------------------------------------------- Graph
    def graph(request: Request, p: str) -> Response:
        site = data["site"]
        if p == f"/sites/{data['host']}:{path}":
            return _odata(site)
        if p == "/sites":
            return _odata({"value": [site]})
        sid = re.escape(site["id"])
        if re.fullmatch(rf"/sites/{sid}/pages/microsoft\.graph\.sitePage", p):
            return _odata({"value": [{k: v for k, v in pg.items() if k != "canvasLayout"} for pg in data["pages"]]})
        if m := re.fullmatch(rf"/sites/{sid}/pages/([^/]+)/microsoft\.graph\.sitePage", p):
            pg = next((x for x in data["pages"] if x["id"] == m.group(1)), None)
            return _odata(pg) if pg else _not_found()
        if m := re.fullmatch(rf"/sites/{sid}/termStore/sets/([0-9a-fA-F-]+)(/.*)?", p):
            return termstore(m.group(1), m.group(2) or "")
        if m := re.fullmatch(r"/users/([^/]+)", p):
            key = unquote(m.group(1)).split("@")[0].lower()
            prof = data["profiles"].get(key)
            return _odata(prof) if prof else JSONResponse({"error": {"code": "Request_ResourceNotFound"}}, 404)
        if m := re.fullmatch(r"/groups/([^/]+)/(transitiveMembers|owners)", p):
            members = data["entraGroups"].get(m.group(1), []) if m.group(2) == "transitiveMembers" else []
            return _odata({"value": [{"@odata.type": "#microsoft.graph.user", "id": data["profiles"][k]["id"],
                                      "displayName": data["profiles"][k]["displayName"],
                                      "mail": data["profiles"][k]["mail"],
                                      "userPrincipalName": data["profiles"][k]["userPrincipalName"]}
                                     for k in members]})
        return JSONResponse({"error": {"code": "BadRequest", "message": f"Unknown Graph resource {p}"}}, 400)

    # ------------------------------------------------------------- dispatch
    async def handle(request: Request) -> Response:
        if ntlm:
            challenge = ntlm.check(request)
            if challenge is not None:
                return challenge
        elif request.headers.get("authorization") != f"Bearer {MOCK_TOKEN}":
            return JSONResponse({"error": "invalid_token"}, status_code=401,
                                headers={"WWW-Authenticate": 'Bearer realm="mock"'})
        p = request.url.path
        if p.startswith(f"{path}/_api/"):
            return rest(request, p[len(f"{path}/_api"):])
        if p.startswith("/v1.0/") and not server:
            return graph(request, p[len("/v1.0"):])
        return _not_found(f"Nothing at {p}" + (" (no Microsoft Graph on SharePoint Server)" if server else ""))

    async def health(request: Request) -> Response:
        return JSONResponse({"mode": mode, "site": site_url})

    return Starlette(routes=[Route("/_health", health), Route("/{rest:path}", handle)])


class _NtlmGate:
    """Real NTLM, as IIS does it for SharePoint Server: 401 + challenge per connection, credentials
    verified by pyspnego against MOCK_NTLM_USER / MOCK_NTLM_PASSWORD."""

    def __init__(self) -> None:
        user = os.environ.get("MOCK_NTLM_USER", "CONTOSO\\spadmin")
        password = os.environ.get("MOCK_NTLM_PASSWORD", "Passw0rd!")
        domain, _, name = user.partition("\\")
        fd, self._user_file = tempfile.mkstemp(prefix="ntlm-users-")
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{domain}:{name}:{password}\n")
        os.environ["NTLM_USER_FILE"] = self._user_file
        self._contexts: dict = {}
        self._authenticated: set = set()

    def check(self, request: Request) -> Response | None:
        import spnego

        conn = (request.client.host, request.client.port) if request.client else None
        header = request.headers.get("authorization", "")
        if not header.lower().startswith(("ntlm ", "negotiate ")):
            if conn in self._authenticated:
                return None
            return Response(status_code=401, headers={"WWW-Authenticate": "NTLM"})
        token = base64.b64decode(header.split(" ", 1)[1])
        ctx = self._contexts.get(conn)
        if ctx is None or token[8:9] == b"\x01":  # a NEGOTIATE message starts a new handshake
            ctx = self._contexts[conn] = spnego.server(protocol="ntlm")
        try:
            out = ctx.step(token)
        except Exception:  # wrong password / unknown user
            self._contexts.pop(conn, None)
            return JSONResponse({"error": "Access denied"}, status_code=401, headers={"WWW-Authenticate": "NTLM"})
        if ctx.complete:
            self._contexts.pop(conn, None)
            self._authenticated.add(conn)
            return None
        return Response(status_code=401, headers={"WWW-Authenticate": "NTLM " + base64.b64encode(out).decode()})
