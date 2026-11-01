"""SharePoint Online and SharePoint Server Subscription Edition, against the mock in mock/sharepoint_mock.

The mock runs in-process (Starlette TestClient). Server mode signs in with real NTLM. Both
platforms must produce the same migration plan, apart from what genuinely differs (user
keys, AD groups, disabled-account detection).
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
from sp2lr.sharepoint import SharePointClient, parse_login

def _serve(platform: str) -> str:
    """Run the mock on a free local port in a background thread; return the site URL."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    site = f"http://127.0.0.1:{port}/sites/marketing"
    server = uvicorn.Server(uvicorn.Config(create_app(platform, site), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return site
        time.sleep(0.05)
    raise RuntimeError("mock SharePoint didn't start")


SITES = {}


def _client(platform: str, password: str = "Passw0rd!") -> SharePointClient:
    site = SITES.get(platform) or SITES.setdefault(platform, _serve(platform))
    return SharePointClient(SharePointSettings(
        tenant_id="", client_id="", hostname="127.0.0.1", site_url=site, platform=platform,
        auth_mode="none" if platform == "online" else "ntlm", graph_url=site.split("/sites/")[0] + "/v1.0",
        username="CONTOSO\\spadmin", password=password))


@pytest.fixture(scope="module")
def extracted(tmp_path_factory):
    out = {}
    for platform in ("online", "server"):
        d = tmp_path_factory.mktemp(platform)
        out[platform] = (d, extract(_client(platform), d))
        build_plan(d, {})
    return out


def test_both_platforms_extract_the_same_site(extracted):
    online, server = extracted["online"][1], extracted["server"][1]
    assert online == server == {"users": 3, "groups": 3, "lists": 2, "items": 4, "libraries": 1, "files": 3,
                                "termSets": 1, "pages": 3, "uniquePermissionScopes": 2}


def test_plans_match_where_the_platforms_match(extracted):
    on = json.loads((extracted["online"][0] / "plan.json").read_text())
    sv = json.loads((extracted["server"][0] / "plan.json").read_text())
    for key in ("lists", "vocabularies", "entryGrants", "siteGrants"):
        assert on[key] == sv[key], key
    assert [(p["name"], p["members"]) for p in on["principals"]] == [(p["name"], p["members"]) for p in sv["principals"]]
    assert [p["name"] for p in on["pages"]] == [p["name"] for p in sv["pages"]] == ["Home.aspx", "About-Us.aspx"]
    assert len(on["news"]["posts"]) == len(sv["news"]["posts"]) == 1

    # Where they differ, by design
    assert [u["externalReferenceCode"] for u in on["users"]] == ["entra-u-jane", "entra-u-bob"]
    assert [u["externalReferenceCode"] for u in sv["users"]] == ["ad-contoso-jane", "ad-contoso-bob", "ad-contoso-old"]
    assert any("Active Directory group" in w for w in sv["report"]["warnings"])
    assert not any("Active Directory group" in w for w in on["report"]["warnings"])


def test_server_pages_convert_to_the_same_liferay_pages(extracted):
    pages = {p: {pg["name"]: pg for pg in json.loads((extracted[p][0] / "extract" / "pages.json").read_text())}
             for p in extracted}
    assert "Old-Wiki.aspx" not in pages["server"]  # classic page: no canvas, not a modern page
    for name, page in pages["online"].items():
        a, _ = t.page_to_site_page(page, "en_US", {}, {})
        b, _ = t.page_to_site_page(pages["server"][name], "en_US", {}, {})
        assert a["pageDefinition"] == b["pageDefinition"], name


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


def test_dry_run_load_of_both_platforms(extracted):
    for platform, (d, _) in extracted.items():
        lr = LiferayClient(LiferaySettings("http://liferay.invalid", "u", "p"), dry_run=True, out=d)
        lr.get = lambda path, ok404=True: None
        lr.paged = lambda path, page_size=200: iter([])
        loader = Loader(lr, d)
        loader.run(PHASES)
        assert loader.state["errors"] == [], platform
        calls = [json.loads(line) for line in (d / "dry-run.jsonl").read_text().splitlines()]
        # The CMS attachment goes to the Space as a document, linked from spAttachments
        widget = next(c for c in calls if c["path"].endswith("sp-item-aaaaaaaa-0000-0000-0000-000000000001-1"))
        assert widget["body"]["spAttachments"].startswith("spec.txt: ")
        assert any(c["path"].endswith("/documents") and "asset-libraries/" in c["path"] for c in calls)


# ----------------------------------------------------------------- unit tests

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
