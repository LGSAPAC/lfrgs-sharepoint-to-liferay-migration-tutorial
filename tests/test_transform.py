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


def test_auto_target_per_use_case(extract_dir):
    plan = build_plan(extract_dir, {})
    by_id = {lp["listId"]: lp for lp in plan["lists"]}
    # Product Catalog = editorial content -> CMS structure in the Space
    assert by_id[PRODUCTS]["target"] == "cms"
    assert by_id[PRODUCTS]["definition"]["scope"] == "depot"
    assert by_id[PRODUCTS]["classification"]["decidedBy"] == "auto"
    # Orders = business process (status, owner, row-level security) -> classic site Object
    orders = by_id[ORDERS]
    assert orders["target"] == "objects"
    assert orders["definition"]["scope"] == "site"
    assert "objectFolderExternalReferenceCode" not in orders["definition"]
    assert any("status" in r for r in orders["classification"]["reasons"])
    owners = next(p for p in plan["principals"] if p["name"] == "SP Marketing Owners")
    assert owners["roleScopes"] == ["objects"]

    # mapping.yaml always wins
    forced = build_plan(extract_dir, {"lists": {"overrides": {"Orders": {"target": "cms"}}}})
    assert next(lp for lp in forced["lists"] if lp["listId"] == ORDERS)["classification"]["decidedBy"] == "mapping.yaml"


def test_object_views_and_field_extras(extract_dir):
    plan = build_plan(extract_dir, {})
    orders = next(lp for lp in plan["lists"] if lp["listId"] == ORDERS)
    all_items, open_orders = orders["objectViews"]
    assert all_items["defaultObjectView"] is True
    assert [c["objectFieldName"] for c in all_items["objectViewColumns"]] == ["title", "quantity", "orderStatus"]
    assert all_items["objectViewSortColumns"] == [{"objectFieldName": "dateCreated", "priority": 0, "sortOrder": "desc"}]
    assert any("Open orders" in m and "filter" in m for m in plan["report"]["manual"])
    status = next(f for f in orders["definition"]["objectFields"] if f["name"] == "orderStatus")
    assert {"name": "defaultValue", "value": "new"} in status["objectFieldSettings"]
    # CMS lists never get object views
    assert next(lp for lp in plan["lists"] if lp["listId"] == PRODUCTS)["objectViews"] == []


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
    assert sum(p.endswith("/object-views") for p in paths) == 2  # Orders is an Object
    entry_perms = [c for c in calls if c["method"] == "PUT" and "/permissions" in c["path"]
                   and c["path"].split("/")[-2].isdigit()]
    assert entry_perms and any(b["roleName"] == "SP Site Principal - SP Marketing Owners"
                               for b in entry_perms[0]["body"])
    # Orders entries go to the site scope, Product Catalog entries to the Space
    assert any("/scopes/" in p and "sp-item-" + ORDERS in p for p in paths)
