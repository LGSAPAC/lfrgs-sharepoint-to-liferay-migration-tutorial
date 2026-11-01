"""SharePoint Online and SharePoint Server (Subscription Edition, 2019, 2016), against the mock in
mock/sharepoint_mock.

The mock runs in a background thread on a local port; Server modes sign in with real NTLM. Every
platform must produce the same migration plan, apart from what genuinely differs (user keys, AD
groups, disabled-account detection, and on 2016 the lack of modern pages and news).
"""

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from sharepoint_mock.app import create_app

from sp2lr import transform as t
from sp2lr.config import LiferaySettings, SharePointSettings
from sp2lr.extract import extract
from sp2lr.liferay import LiferayClient
from sp2lr.load import PHASES, Loader
from sp2lr.plan import build_plan
from sp2lr.sharepoint import SharePointClient, parse_csom_term_set, parse_login

MODES = ("online", "server", "server2019", "server2016")
SERVER_MODES = MODES[1:]


def _serve(mode: str) -> str:
    """Run the mock on a free local port in a background thread; return the site URL."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    site = f"http://127.0.0.1:{port}/sites/marketing"
    server = uvicorn.Server(uvicorn.Config(create_app(mode, site), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return site
        time.sleep(0.05)
    raise RuntimeError("mock SharePoint didn't start")


SITES: dict[str, str] = {}


def _client(mode: str, password: str = "Passw0rd!") -> SharePointClient:
    site = SITES.get(mode) or SITES.setdefault(mode, _serve(mode))
    platform = "online" if mode == "online" else "server"
    return SharePointClient(SharePointSettings(
        tenant_id="", client_id="", hostname="127.0.0.1", site_url=site, platform=platform,
        auth_mode="none" if platform == "online" else "ntlm", graph_url=site.split("/sites/")[0] + "/v1.0",
        username="CONTOSO\\spadmin", password=password))


@pytest.fixture(scope="module")
def extracted(tmp_path_factory):
    out = {}
    for mode in MODES:
        d = tmp_path_factory.mktemp(mode)
        out[mode] = (d, extract(_client(mode), d))
        build_plan(d, {})
    return out


def _plan(extracted, mode):
    return json.loads((extracted[mode][0] / "plan.json").read_text())


def _pages(extracted, mode):
    return {pg["name"]: pg for pg in json.loads((extracted[mode][0] / "extract" / "pages.json").read_text())}


def test_every_platform_extracts_the_same_site(extracted):
    for mode in MODES:
        assert extracted[mode][1] == {"users": 3, "groups": 3, "lists": 2, "items": 4, "libraries": 1, "files": 3,
                                      "termSets": 1, "pages": 5, "uniquePermissionScopes": 2}, mode


def test_plans_match_where_the_platforms_match(extracted):
    on = _plan(extracted, "online")
    for mode in SERVER_MODES:
        sv = _plan(extracted, mode)
        for key in ("lists", "vocabularies", "entryGrants", "siteGrants"):
            assert on[key] == sv[key], (mode, key)
        assert [(p["name"], p["members"]) for p in on["principals"]] == \
            [(p["name"], p["members"]) for p in sv["principals"]], mode
        # Where they differ, by design
        assert [u["externalReferenceCode"] for u in sv["users"]] == ["ad-contoso-jane", "ad-contoso-bob", "ad-contoso-old"]
        assert any("Active Directory group" in w for w in sv["report"]["warnings"]), mode
    assert [u["externalReferenceCode"] for u in on["users"]] == ["entra-u-jane", "entra-u-bob"]
    assert not any("Active Directory group" in w for w in on["report"]["warnings"])


def test_pages_and_news_per_version(extracted):
    modern = ["Home.aspx", "About-Us.aspx", "Old-Wiki.aspx", "Press-Release.aspx"]
    for mode in ("online", "server", "server2019"):
        plan = _plan(extracted, mode)
        assert [p["name"] for p in plan["pages"]] == modern, mode
        assert len(plan["news"]["posts"]) == 1, mode
    # 2016 has no modern pages and no news: the same pages exist as wiki pages
    plan = _plan(extracted, "server2016")
    assert [p["name"] for p in plan["pages"]] == ["Home.aspx", "About-Us.aspx", "Q3-Results.aspx",
                                                  "Old-Wiki.aspx", "Press-Release.aspx"]
    assert plan["news"]["posts"] == []


def test_modern_pages_convert_to_the_same_liferay_pages(extracted):
    online = _pages(extracted, "online")
    for mode in ("server", "server2019"):
        pages = _pages(extracted, mode)
        for name, page in online.items():
            a, _ = t.page_to_site_page(page, "en_US", {}, {})
            b, _ = t.page_to_site_page(pages[name], "en_US", {}, {})
            assert a["pageDefinition"] == b["pageDefinition"], (mode, name)


def test_classic_pages(extracted):
    for mode in MODES:
        pages = _pages(extracted, mode)
        wiki, press = pages["Old-Wiki.aspx"], pages["Press-Release.aspx"]
        assert wiki["id"].startswith("classic-") and press["id"].startswith("classic-"), mode
        body, _ = t.page_to_site_page(wiki, "en_US", {}, {})
        assert "Team wiki" in json.dumps(body["pageDefinition"]), mode
        assert "/sites/marketing/SiteAssets/team.png" in wiki["_images"], mode  # <img> in wiki HTML downloaded
        assert press["_listItem"]["Author"]["EMail"] == "bob@contoso.com", mode  # item from the Pages library
        body, _ = t.page_to_site_page(press, "en_US", {}, {})
        assert "Widget 2.0 launches" in json.dumps(body["pageDefinition"]), mode


def test_term_hierarchy_on_every_version(extracted):
    """SE reads /_api/v2.1/termStore; 2019 and 2016 only have the client object model."""
    for mode in MODES:
        ts = json.loads((extracted[mode][0] / "extract" / "termsets.json").read_text())
        (term_set,) = ts.values()
        assert not term_set.get("unavailable"), mode
        assert [(x["labels"][0]["name"], [c["labels"][0]["name"] for c in x["children"]])
                for x in term_set["terms"]] == [("Sales", ["EMEA"])], mode


def test_2016_rejects_modern_page_columns():
    """The mock answers 400 for a $select of a column the list doesn't have, as SharePoint does; the client
    must therefore only select CanvasContent1 where it exists."""
    sp = _client("server2016")
    with pytest.raises(httpx.HTTPStatusError) as e:
        sp.rest(f"/web/lists(guid'{sp._site_pages_list_id()}')/items?$select=Id,CanvasContent1")
    assert e.value.response.status_code == 400
    assert sp._server_page_items() == []


def test_odata_paging_and_downloads(extracted):
    d, _ = extracted["server"]
    items = json.loads((d / "extract" / "lists" / "aaaaaaaa-0000-0000-0000-000000000001.items.json").read_text())
    assert [i["Id"] for i in items] == [1, 2]  # the mock pages one item at a time
    assert (d / "extract" / "files" / "attachments").exists()
    lib = json.loads((d / "extract" / "libraries" / "bbbbbbbb-0000-0000-0000-000000000001.items.json").read_text())
    assert {i["FileLeafRef"]: bool(i.get("_localPath")) for i in lib if i["FSObjType"] == 0} == \
        {"handbook.pdf": True, "readme.txt": True, "video.mp4": False}


def test_ntlm_rejects_a_wrong_password():
    with pytest.raises(httpx.HTTPStatusError) as e:
        _client("server", password="wrong").web()
    assert e.value.response.status_code == 401


def test_server_has_no_graph():
    with pytest.raises(RuntimeError, match="Graph"):
        _client("server").graph("/me")


def test_dry_run_load_of_every_platform(extracted):
    for mode, (d, _) in extracted.items():
        lr = LiferayClient(LiferaySettings("http://liferay.invalid", "u", "p"), dry_run=True, out=d)
        lr.get = lambda path, ok404=True: None
        lr.paged = lambda path, page_size=200: iter([])
        loader = Loader(lr, d)
        loader.run(PHASES)
        assert loader.state["errors"] == [], mode
        calls = [json.loads(line) for line in (d / "dry-run.jsonl").read_text().splitlines()]
        # The CMS attachment goes to the Space as a document, linked from spAttachments
        widget = next(c for c in calls if c["path"].endswith("sp-item-aaaaaaaa-0000-0000-0000-000000000001-1"))
        assert widget["body"]["spAttachments"].startswith("spec.txt: ")
        assert any(c["path"].endswith("/documents") and "asset-libraries/" in c["path"] for c in calls)


# ----------------------------------------------------------------- unit tests

def test_csom_term_set_parsing():
    resp = [{"SchemaVersion": "15.0.0.0", "ErrorInfo": None},
            11, {"_ObjectType_": "SP.Taxonomy.TermSet", "Id": "/Guid(CCCC-1)/", "Name": "Departments"},
            12, {"_ObjectType_": "SP.Taxonomy.TermCollection", "_Child_Items_": [
                {"Id": "/Guid(T-EMEA)/", "Name": "EMEA", "PathOfTerm": "Sales;EMEA"},  # child before parent
                {"Id": "/Guid(T-SALES)/", "Name": "Sales", "PathOfTerm": "Sales"},
                {"Id": "/Guid(T-HR)/", "Name": "HR", "PathOfTerm": "HR"}]}]
    ts = parse_csom_term_set(resp, "cccc-1")
    assert ts["id"] == "cccc-1" and ts["localizedNames"] == [{"name": "Departments", "languageTag": "en-US"}]
    assert [(x["id"], [c["id"] for c in x["children"]]) for x in ts["terms"]] == [("t-sales", ["t-emea"]), ("t-hr", [])]
    with pytest.raises(RuntimeError, match="security validation"):
        parse_csom_term_set([{"ErrorInfo": {"ErrorMessage": "The security validation for this page is invalid."}}], "x")


def test_windows_logins():
    assert parse_login("i:0#.w|contoso\\jane") == {"kind": "user", "upn": "contoso\\jane", "windows": True}
    assert parse_login("c:0+.w|s-1-5-21-1-2-3-1105") == {"kind": "adgroup", "groupId": "s-1-5-21-1-2-3-1105"}
    assert parse_login("c:0!.s|windows") == {"kind": "everyone"}
    assert t.erc_user("ad-contoso-jane") == "ad-contoso-jane" and t.erc_user("u-1") == "entra-u-1"


def test_canvas_to_layout():
    canvas = json.dumps([
        {"controlType": 4, "position": {"zoneIndex": 2, "sectionIndex": 1, "controlIndex": 1, "sectionFactor": 0},
         "innerHTML": "<p>full width</p>"},
        {"controlType": 4, "position": {"zoneIndex": 1, "sectionIndex": 2, "controlIndex": 1, "sectionFactor": 8},
         "innerHTML": "<p>right</p>"},
        {"position": {"zoneIndex": 1, "sectionIndex": 1, "sectionFactor": 4}, "displayMode": 2},  # empty column
        {"controlType": 3, "webPartId": "img", "position": {"zoneIndex": 1, "sectionIndex": 2, "controlIndex": 0,
                                                             "sectionFactor": 8},
         "webPartData": {"title": "Image", "serverProcessedContent": {"imageSources": {"imageSource": "/a.png"}}}},
        {"controlType": 0, "pageSettingsSlice": {}},
    ])
    first, second = t.canvas_to_layout(canvas)["horizontalSections"]
    assert first["layout"] == "oneThirdLeftColumn" and [c["width"] for c in first["columns"]] == [4, 8]
    assert first["columns"][0]["webparts"] == []
    right = first["columns"][1]["webparts"]
    assert right[0]["data"]["serverProcessedContent"]["imageSources"] == [{"key": "imageSource", "value": "/a.png"}]
    assert right[1]["innerHtml"] == "<p>right</p>"  # ordered by controlIndex
    assert second["layout"] == "fullWidth"
    assert t.canvas_to_layout("not json") == {"horizontalSections": []}
