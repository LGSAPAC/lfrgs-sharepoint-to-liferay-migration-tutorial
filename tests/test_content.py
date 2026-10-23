"""Document libraries, managed metadata, news and audit preservation."""

import json

from sp2lr import transform as t
from sp2lr.config import LiferaySettings
from sp2lr.liferay import LiferayClient
from sp2lr.load import Loader
from sp2lr.plan import build_plan

from conftest import POLICIES, PRODUCTS


def test_library_folders_documents_and_permissions(extract_dir):
    plan = build_plan(extract_dir, {})
    lib = next(l for l in plan["libraries"] if l["libraryId"] == POLICIES)

    root, hr = lib["folders"]
    assert (root["name"], root["parentErc"]) == ("Policies", None)
    assert (hr["name"], hr["parentErc"], hr["permissionScope"]) == ("HR", root["erc"], "10")

    docs = {d["fileName"]: d for d in lib["documents"]}
    handbook, readme = docs["handbook.pdf"], docs["readme.txt"]
    assert handbook["folderErc"] == hr["erc"] and readme["folderErc"] == root["erc"]
    # Liferay doesn't inherit permissions: the file under the restricted folder carries the folder's scope
    assert handbook["permissionScope"] == "10" and readme["permissionScope"] is None
    assert handbook["metadata"] == {"audience": "hr", "expiry": "2027-01-01",
                                    "spCreated": "2023-05-01T08:00:00Z", "spModified": "2024-06-01T08:00:00Z",
                                    "spCreatedBy": "jane@contoso.com", "spModifiedBy": "jane@contoso.com"}
    assert handbook["terms"] == ["t-emea"]
    assert lib["skippedFiles"] == ["/sites/marketing/Policies/video.mp4: larger than 250 MB"]
    assert any("only the current version" in w for w in plan["report"]["warnings"])

    ddm = {f["name"]: f["fieldType"] for f in lib["documentType"]["dataDefinition"]["dataDefinitionFields"]}
    assert ddm["audience"] == "select" and ddm["expiry"] == "date" and ddm["spCreated"] == "date_time"
    assert "region" not in ddm  # managed metadata -> categories, not a field
    assert {g["level"] for g in lib["grants"]["10"]} == {"Full Control", "Read"}


def test_managed_metadata_to_vocabularies(extract_dir):
    plan = build_plan(extract_dir, {})
    (voc,) = plan["vocabularies"]
    assert voc["name"] == "Departments" and voc["fromItemsOnly"] is False
    cats = {c["name"]: c for c in voc["categories"]}
    assert cats["EMEA"]["parentErc"] == cats["Sales"]["erc"]  # hierarchy kept
    assert cats["Legacy"]["parentErc"] is None  # tagged on an item but gone from the term store
    assert plan["taxonomy"]["scope"] == "global"


def test_news_to_blogs(extract_dir):
    plan = build_plan(extract_dir, {})
    (post,) = plan["news"]["posts"]
    assert post["headline"] == "Q3 results are in"
    assert post["datePublished"] == "2026-09-30T07:00:00Z" and post["authorEmail"] == "jane@contoso.com"
    assert "Q3-Results.aspx" not in {p["name"] for p in plan["pages"]}
    assert plan["news"]["links"]["/sites/marketing/SitePages/Q3-Results.aspx"] == "/web/marketing/-/blogs/q3-results"


def test_news_to_structure(extract_dir):
    plan = build_plan(extract_dir, {"news": {"target": "structure"}})
    news = next(lp for lp in plan["lists"] if lp.get("news"))
    assert news["target"] == "cms" and news["definition"]["name"] == "News"
    assert {f["name"] for f in news["definition"]["objectFields"]} >= {"title", "summary", "body", "published", "spCreatedBy"}
    items = json.loads((extract_dir / "extract" / "lists" / f"{news['listId']}.items.json").read_text())
    assert items[0]["_pageId"] == "p-news"
    assert plan["news"]["posts"] == []


def test_news_stays_a_page(extract_dir):
    plan = build_plan(extract_dir, {"news": {"target": "pages"}})
    assert "Q3-Results.aspx" in {p["name"] for p in plan["pages"]}


def test_audit_fields(extract_dir):
    plan = build_plan(extract_dir, {})
    products = next(lp for lp in plan["lists"] if lp["listId"] == PRODUCTS)
    item = json.loads((extract_dir / "extract" / "lists" / f"{PRODUCTS}.items.json").read_text())[0]
    e = t.item_to_entry(products, item)
    assert (e["spCreated"], e["spModified"], e["spCreatedBy"], e["spModifiedBy"]) == (
        "2024-03-01T09:00:00Z", "2025-02-01T10:30:00Z", "jane@contoso.com", "bob@contoso.com")

    off = build_plan(extract_dir, {"audit": {"fields": False}})
    products = next(lp for lp in off["lists"] if lp["listId"] == PRODUCTS)
    assert "spCreated" not in {f["name"] for f in products["definition"]["objectFields"]}


def _dry_run(extract_dir, monkeypatch, mapping, phases):
    build_plan(extract_dir, mapping)
    lr = LiferayClient(LiferaySettings("http://liferay.invalid", "u", "p"), dry_run=True, out=extract_dir)
    monkeypatch.setattr(lr, "get", lambda path, ok404=True: None)
    monkeypatch.setattr(lr, "paged", lambda path, page_size=200: iter([]))
    Loader(lr, extract_dir).run(phases)
    return [json.loads(l) for l in (extract_dir / "dry-run.jsonl").read_text().splitlines()]


def test_dry_run_documents_taxonomy_news(extract_dir, monkeypatch):
    calls = _dry_run(extract_dir, monkeypatch, {"audit": {"impersonate": True}},
                     ["site", "users", "groups", "taxonomy", "structures", "entries", "documents", "news", "permissions"])
    paths = [c["path"] for c in calls]

    # Vocabulary + categories, EMEA created under Sales
    assert sum(p.endswith("/taxonomy-vocabularies") for p in paths) == 1
    assert sum("/taxonomy-categories" in p for p in paths) == 3  # Sales, EMEA (child), Legacy
    assert any("/taxonomy-categories/" in p and p.split("/")[-2].isdigit() for p in paths)

    # Product entry tagged with its category, created as the original author
    widget = next(c for c in calls if c["path"].endswith(f"sp-item-{PRODUCTS}-1"))
    assert widget["body"]["taxonomyCategoryIds"] and widget["params"] == {"doAsUserId": widget["params"]["doAsUserId"]}

    # Document type, folders, documents with metadata
    assert any("/data-definitions/by-content-type/document-library" in p for p in paths)
    assert sum(p.endswith("/document-folders") for p in paths) == 2
    docs = [c for c in calls if c["path"].endswith("/documents") and "document-folders/" in c["path"]]
    assert len(docs) == 2
    handbook = next(json.loads(c["data"]["document"]) for c in docs if "Employee Handbook" in c["data"]["document"])
    assert handbook["externalReferenceCode"] == f"sp-doc-{POLICIES}-11"
    assert handbook["documentType"]["contentFields"][0] == {"name": "audience", "contentFieldValue": {"data": "hr"}}
    assert handbook["taxonomyCategoryIds"]

    # Restricted folder + the document inside it get explicit permissions; the other document doesn't
    perm_calls = [c for c in calls if c["method"] == "PUT" and "/headless-delivery/v1.0/" in c["path"]
                  and c["path"].endswith("/permissions")]
    assert {c["path"].split("/")[-3] for c in perm_calls} == {"document-folders", "documents"}
    folder_perms = next(c for c in perm_calls if "/document-folders/" in c["path"])
    owners = next(b for b in folder_perms["body"] if b["roleName"] == "SP Site Principal - SP Marketing Owners")
    assert "ADD_DOCUMENT" in owners["actionIds"]

    # News post -> blog posting with the original publish date, as the original author
    blog = next(c for c in calls if c["path"].endswith("/blog-postings"))
    assert blog["body"]["datePublished"] == "2026-09-30T07:00:00Z"
    assert "Revenue up 20%" in blog["body"]["articleBody"] and blog["params"]
