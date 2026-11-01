"""End-to-end checks of the whole pipeline without a tenant or a Liferay instance:
extract (fake SharePoint) -> plan -> load --dry-run (CLI) -> verify (fake Liferay)."""

import json
import shutil
import types
from pathlib import Path

import httpx

from sp2lr import cli
from sp2lr.config import LiferaySettings
from sp2lr.extract import extract
from sp2lr.liferay import LiferayClient
from sp2lr.load import PHASES, Loader
from sp2lr.plan import build_plan
from sp2lr.verify import verify

from conftest import EXTRACT, ORDERS, PRODUCTS

ALL_BUT_PAGES = [p for p in PHASES if p != "pages"]


def _write_extract(out: Path, extract_data: dict) -> Path:
    root = out / "extract"
    for name, data in extract_data.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data).replace("{ROOT}", str(root)))
    (root / "files").mkdir()
    (root / "files" / "handbook.pdf").write_bytes(b"%PDF-1.7 handbook")
    (root / "files" / "readme.txt").write_text("read me")
    return out


def _dry_loader(out: Path, mapping=None, phases=ALL_BUT_PAGES) -> tuple[Loader, list[dict]]:
    build_plan(out, mapping or {})
    lr = LiferayClient(LiferaySettings("http://liferay.invalid", "u", "p"), dry_run=True, out=out)
    lr.get = lambda path, ok404=True: None
    lr.paged = lambda path, page_size=200: iter([])
    loader = Loader(lr, out)
    loader.run(phases)
    return loader, [json.loads(line) for line in (out / "dry-run.jsonl").read_text().splitlines()]


def _fresh_liferay(request: httpx.Request) -> httpx.Response:
    """An empty Liferay: lookups by key 404, collections are empty."""
    path = request.url.path
    if "by-" in path or "/site-pages/" in path or path.rstrip("/").split("/")[-1].isdigit():
        return httpx.Response(404)
    return httpx.Response(200, json={"items": [], "lastPage": 1, "totalCount": 0})


def test_cli_plan_and_full_dry_run(extract_dir, monkeypatch, capsys):
    (extract_dir / "extract" / "files" / "team.jpg").write_bytes(b"jpg")
    pages = json.loads((extract_dir / "extract" / "pages.json").read_text())
    pages[1]["_images"] = {"/sites/marketing/SiteAssets/team.jpg": str(extract_dir / "extract" / "files" / "team.jpg")}
    (extract_dir / "extract" / "pages.json").write_text(json.dumps(pages))

    monkeypatch.setenv("SP2LR_OUT", str(extract_dir))
    for k, v in {"LIFERAY_URL": "http://liferay.invalid", "LIFERAY_USER": "u", "LIFERAY_PASSWORD": "p"}.items():
        monkeypatch.setenv(k, v)
    orig_init = LiferayClient.__init__

    def init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.http = httpx.Client(base_url="http://liferay.invalid", transport=httpx.MockTransport(_fresh_liferay))

    monkeypatch.setattr(LiferayClient, "__init__", init)

    cli.main(["plan", "--mapping", str(Path(__file__).parent.parent / "mapping.example.yaml")])
    cli.main(["load", "--dry-run"])  # every phase, including pages and navigation

    assert "Done. 0 errors" in capsys.readouterr().out
    calls = [json.loads(line) for line in (extract_dir / "dry-run.jsonl").read_text().splitlines()]
    assert [c["body"]["friendlyUrlPath"] for c in calls if c["path"].endswith("/site-pages")] == ["/home", "/about-us"]
    assert any(c["path"].endswith("/navigation-menus") for c in calls)
    assert not (extract_dir / "state.json").exists()  # a dry run never touches the real state


def test_lookup_target_list_id_in_another_case(tmp_path):
    """A lookup column may reference its target list's GUID in a different case than the list's Id."""
    upper = json.loads(json.dumps(EXTRACT).replace(PRODUCTS, PRODUCTS.upper()))
    out = _write_extract(tmp_path, upper)
    # Both lists as Objects, so Liferay can relate them
    loader, calls = _dry_loader(out, {"lists": {"overrides": {"Product Catalog": {"target": "objects"}}}})

    rel = loader.state["definitions"][ORDERS]["relationships"]["Product"]
    assert rel["target"] == PRODUCTS.upper()  # the key the target's entries are stored under
    assert len([c for c in calls if c["method"] == "PATCH"]) == 2  # PO-1 -> Widget, PO-2 -> Support
    assert loader.state["errors"] == []


def test_missing_local_file_is_an_error_not_a_crash(tmp_path):
    out = _write_extract(tmp_path, EXTRACT)
    (out / "extract" / "files" / "readme.txt").unlink()
    loader, calls = _dry_loader(out)

    assert len(loader.state["errors"]) == 1 and "readme.txt" in loader.state["errors"][0]
    assert any("Employee Handbook" in (c.get("data") or {}).get("document", "") for c in calls)  # the rest went on
    assert any(c["path"].endswith("/blog-postings") for c in calls)  # and so did later phases


def test_verify_all_present(tmp_path):
    out = _write_extract(tmp_path, EXTRACT)
    _dry_loader(out, phases=PHASES[:-1])
    shutil.copy(out / "state.dry-run.json", out / "state.json")

    def everything_exists(request: httpx.Request) -> httpx.Response:
        if "/scopes/" in request.url.path:
            return httpx.Response(200, json={"items": [], "totalCount": 2})
        return httpx.Response(200, json={"id": 1})

    lr = LiferayClient(LiferaySettings("http://liferay.invalid", "u", "p"))
    lr.http = httpx.Client(base_url="http://liferay.invalid", transport=httpx.MockTransport(everything_exists))
    report = verify(lr, out)

    assert [r["area"] for r in report["rows"] if not r["ok"]] == []
    assert len(report["rows"]) == 11  # incl. Site and CMS Space
    assert "| Pages | 2 | 2 | yes |" in (out / "verify.md").read_text()


class FakeSharePoint:
    """Serves the conftest extract the way SharePointClient returns it from the live APIs."""

    settings = types.SimpleNamespace(site_url="https://contoso.sharepoint.com/sites/marketing")
    _all = EXTRACT["lists.json"] + EXTRACT["libraries.json"]

    def site(self): return EXTRACT["site.json"]["graph"]
    def web(self): return EXTRACT["site.json"]["web"]
    def navigation(self): return EXTRACT["site.json"]["navigation"]
    def site_users(self): return [{"LoginName": "i:0#.f|membership|jane@contoso.com"}]
    def site_groups(self): return EXTRACT["groups.json"]
    def entra_user(self, upn): return EXTRACT["users.json"]["profiles"].get(upn.lower())
    def entra_group_members(self, gid, owners=False): return EXTRACT["users.json"]["entraGroups"].get(gid, [])
    def role_definitions(self): return []
    def list_lists(self): return [x["list"] for x in self._all]
    def list_fields(self, lid): return next(x["fields"] for x in self._all if x["list"]["Id"] == lid)
    def list_views(self, lid): return next(x.get("views", []) for x in EXTRACT["lists.json"] if x["list"]["Id"] == lid)
    def term_set(self, tid): return EXTRACT["termsets.json"][tid]
    def list_pages(self): return [{"id": p["id"], "name": p["name"]} for p in EXTRACT["pages.json"]]
    def page_list_item(self, name, list_id=None): return {"Id": 1}
    def download(self, rel): return b"img"

    def role_assignments(self, scope="web", list_id=None, item_id=None):
        if scope == "web":
            return EXTRACT["permissions.json"]["web"]
        return EXTRACT["permissions.json"]["items"].get(list_id, {}).get(str(item_id), [])

    def list_items(self, lid, fields, library=False):
        items = EXTRACT[f"{'libraries' if library else 'lists'}/{lid}.items.json"]
        return [{k: v for k, v in i.items() if not k.startswith("_")}
                | ({"File": {"Length": 10}} if i.get("FSObjType") == 0 else {}) for i in items]

    def download_to(self, ref, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"0123456789")
        return 10

    def get_page(self, pid):
        page = next(p for p in EXTRACT["pages.json"] if p["id"] == pid)
        return {k: v for k, v in page.items() if not k.startswith("_")}


def test_extract_then_plan(tmp_path):
    summary = extract(FakeSharePoint(), tmp_path)
    assert summary == {"users": 2, "groups": 2, "lists": 2, "items": 4, "libraries": 1, "files": 3,
                       "termSets": 1, "pages": 3, "uniquePermissionScopes": 2}

    plan = build_plan(tmp_path, {})
    assert [lp["listTitle"] for lp in plan["lists"]] == ["Product Catalog", "Orders"]
    (lib,) = plan["libraries"]
    assert len(lib["documents"]) == 3 and all(Path(d["localPath"]).is_absolute() for d in lib["documents"])
    assert len(plan["pages"]) == 2 and len(plan["news"]["posts"]) == 1


def test_dry_run_never_reads_records_it_only_pretended_to_create(tmp_path):
    """On a fresh Liferay the dry-run site doesn't exist, so reads under its fake ID must not reach Liferay."""
    seen = []

    def fresh(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _fresh_liferay(request)

    out = _write_extract(tmp_path, EXTRACT)
    image = out / "extract" / "files" / "team.jpg"
    image.write_bytes(b"jpg")
    pages = json.loads((out / "extract" / "pages.json").read_text())
    pages[1]["_images"] = {"/sites/marketing/SiteAssets/team.jpg": str(image)}
    (out / "extract" / "pages.json").write_text(json.dumps(pages))
    build_plan(out, {})
    lr = LiferayClient(LiferaySettings("http://liferay.invalid", "u", "p"), dry_run=True, out=out)
    lr.http = httpx.Client(base_url="http://liferay.invalid", transport=httpx.MockTransport(fresh))
    loader = Loader(lr, out)
    loader.run(PHASES)

    assert loader.state["errors"] == []
    assert not any("/9000" in p for p in seen)


def test_verify_reports_a_missing_site_instead_of_crashing(tmp_path):
    out = _write_extract(tmp_path, EXTRACT)
    build_plan(out, {})
    (out / "state.json").write_text(json.dumps({"errors": ["create site: POST /o/headless-site/v1.0/sites -> 500"]}))
    lr = LiferayClient(LiferaySettings("http://liferay.invalid", "u", "p"))
    lr.http = httpx.Client(base_url="http://liferay.invalid", transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    report = verify(lr, out)
    site = next(r for r in report["rows"] if r["area"] == "Site")
    assert not site["ok"] and "not created" in site["note"]
    assert "create site" in (out / "verify.md").read_text()
