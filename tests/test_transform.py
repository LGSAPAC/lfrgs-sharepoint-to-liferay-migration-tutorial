import json

from sp2lr import transform as t
from sp2lr.liferay import LiferayClient
from sp2lr.config import LiferaySettings
from sp2lr.load import Loader
from sp2lr.plan import build_plan
from sp2lr.sharepoint import parse_login

from conftest import ORDERS, PRODUCTS


def test_names():
    assert t.pascal("Product Catalog") == "ProductCatalog"
    assert t.camel("Launch_x0020_Date") == "launchDate"
    assert t.camel("Status") == "spStatus"
    assert t.camel("2nd Owner") == "sp2ndOwner"
    assert t.slugify("About-Us.aspx") == "about-us"
    assert t.picklist_key("Software & Services") == "softwareServices"


def test_parse_login():
    assert parse_login("i:0#.f|membership|jane@contoso.com") == {"kind": "user", "upn": "jane@contoso.com"}
    assert parse_login("c:0o.c|federateddirectoryclaimprovider|abc_o") == {"kind": "m365group", "groupId": "abc", "owners": True}
    assert parse_login("c:0t.c|tenant|g1")["kind"] == "securitygroup"
    assert parse_login("c:0-.f|rolemanager|spo-grid-all-users/x")["kind"] == "everyone"


def test_plan(extract_dir):
    plan = build_plan(extract_dir, {})
    assert plan["site"]["key"] == "marketing"
    emails = {u["emailAddress"] for u in plan["users"]}
    assert emails == {"jane@contoso.com", "bob@contoso.com"}  # disabled user skipped

    members = next(p for p in plan["principals"] if p["name"] == "SP Marketing Members")
    assert members["members"] == ["bob@contoso.com"]  # expanded from the Entra group
    grants = {g["level"]: g for g in plan["siteGrants"]}
    assert grants["Full Control"]["siteRole"] == "Site Owner"
    assert grants["Edit"]["siteRole"] == "SP Contributor"  # Limited Access ignored

    products = next(lp for lp in plan["lists"] if lp["listId"] == PRODUCTS)
    d = products["definition"]
    assert d["scope"] == "depot" and d["objectFolderExternalReferenceCode"] == "L_CMS_CONTENT_STRUCTURES"
    types = {f["name"]: f["businessType"] for f in d["objectFields"]}
    assert types == {"title": "Text", "price": "PrecisionDecimal", "category": "Picklist",
                     "launchDate": "Date", "description": "RichText", "spStatus": "Text"}
    assert products["skipped"][0]["field"] == "Calc"

    orders = next(lp for lp in plan["lists"] if lp["listId"] == ORDERS)
    assert orders["lookups"][0]["targetListId"] == PRODUCTS
    assert set(plan["entryGrants"][ORDERS]["items"]) == {"2"}
    assert (extract_dir / "plan.md").exists()


def test_entries(extract_dir):
    plan = build_plan(extract_dir, {})
    orders = next(lp for lp in plan["lists"] if lp["listId"] == ORDERS)
    items = json.loads((extract_dir / "extract" / "lists" / f"{ORDERS}.items.json").read_text())
    e = t.item_to_entry(orders, items[0])
    assert e == {"externalReferenceCode": f"sp-item-{ORDERS}-1", "title": "PO-1", "owner": "jane@contoso.com",
                 "quantity": 3, "tags": [{"key": "urgent"}, {"key": "gift"}], "shipped": True}
    products = next(lp for lp in plan["lists"] if lp["listId"] == PRODUCTS)
    p = t.item_to_entry(products, json.loads((extract_dir / "extract" / "lists" / f"{PRODUCTS}.items.json").read_text())[0])
    assert p["launchDate"] == "2026-01-15" and p["category"] == {"key": "hardware"}


def test_page_definition(extract_dir):
    pages = json.loads((extract_dir / "extract" / "pages.json").read_text())
    links = t.page_link_map(pages, "/sites/marketing", "/web/marketing")
    body, unmigrated = t.page_to_site_page(pages[0], "en_US", {}, links)
    assert body["friendlyUrlPath"] == "/home"
    row = body["pageDefinition"]["pageElement"]["pageElements"][1]["pageElements"][0]
    assert [c["definition"]["size"] for c in row["pageElements"]] == [6, 6]
    text = row["pageElements"][0]["pageElements"][0]["definition"]["fragmentFields"][0]["value"]["text"]["value_i18n"]["en_US"]
    assert 'href="/web/marketing/about-us"' in text
    assert unmigrated == ["Quick links"]

    body, _ = t.page_to_site_page(pages[1], "en_US", {"/sites/marketing/SiteAssets/team.jpg": "/documents/d/marketing/team-jpg"}, links)
    assert '<img src="/documents/d/marketing/team-jpg" alt="Team" />' in str(body)


def test_dry_run_load(extract_dir, monkeypatch):
    build_plan(extract_dir, {})
    lr = LiferayClient(LiferaySettings("http://liferay.invalid", "u", "p"), dry_run=True, out=extract_dir)
    monkeypatch.setattr(lr, "get", lambda path, ok404=True: None)
    monkeypatch.setattr(lr, "paged", lambda path, page_size=200: iter([]))
    loader = Loader(lr, extract_dir)
    loader.run(["site", "users", "groups", "structures", "entries", "permissions"])
    calls = [json.loads(l) for l in (extract_dir / "dry-run.jsonl").read_text().splitlines()]
    paths = [c["path"] for c in calls]
    assert "/o/headless-asset-library/v1.0/asset-libraries/by-external-reference-code/" + loader.plan["space"]["erc"] in paths
    assert sum(p.startswith("/o/headless-admin-user/v1.0/user-accounts/") for p in paths) == 2
    assert any(c["path"] == "/api/jsonws/invoke" and "/usergroup/add-group-user-groups" in c["body"] for c in calls)
    assert sum(p == "/o/object-admin/v1.0/object-definitions" for p in paths) == 2
    assert any("/object-relationships" in p for p in paths)
    assert sum("/by-external-reference-code/sp-item-" in p for p in paths) == 4
    assert any(p.endswith("/permissions") and c["method"] == "PUT" for p, c in zip(paths, calls))
