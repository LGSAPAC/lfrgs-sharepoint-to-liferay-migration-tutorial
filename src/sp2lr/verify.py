"""Phase 4: reconcile SharePoint (extract) against Liferay and write out/verify.md."""

from __future__ import annotations

import json
from pathlib import Path

from .liferay import LiferayClient


def verify(lr: LiferayClient, out: Path) -> dict:
    plan = json.loads((out / "plan.json").read_text())
    state = json.loads((out / "state.json").read_text())
    rows = []

    def row(area: str, expected: int, actual: int, note: str = "") -> None:
        rows.append({"area": area, "expected": expected, "actual": actual,
                     "ok": expected == actual, "note": note})

    found_users = sum(1 for u in plan["users"] if lr.user_by_email(u["emailAddress"]))
    row("Users", len(plan["users"]), found_users)

    groups = [p for p in plan["principals"] if p["kind"] != "user"]
    found_groups = sum(1 for p in groups
                       if lr.get(f"/o/headless-admin-user/v1.0/user-groups/by-external-reference-code/{p['erc']}"))
    row("User groups", len(groups), found_groups)

    for lp in plan["lists"]:
        d = state.get("definitions", {}).get(lp["listId"])
        if not d:
            row(f"List '{lp['listTitle']}'", lp["itemCount"], 0, "structure missing")
            continue
        scope = state["space"]["id"] if lp["target"] == "cms" else state["site"]["id"]
        data = lr.get(f"{d['restContextPath']}/scopes/{scope}?pageSize=1") or {}
        row(f"List '{lp['listTitle']}' -> {d['name']}", lp["itemCount"], data.get("totalCount", 0))

    site_id = state["site"]["id"]
    found_pages = sum(1 for p in plan["pages"] if lr.site_page(site_id, p["friendlyUrlPath"]))
    row("Pages", len(plan["pages"]), found_pages)

    report = {"rows": rows, "manual": state.get("manual", []), "errors": state.get("errors", [])}
    md = ["# Verification report", "", "| Area | SharePoint | Liferay | OK |", "|---|---|---|---|"]
    md += [f"| {r['area']} | {r['expected']} | {r['actual']} | {'yes' if r['ok'] else '**NO** ' + r['note']} |" for r in rows]
    md += ["", "## Manual follow-ups", ""] + [f"- [ ] {m}" for m in report["manual"] or ["None"]]
    md += ["", "## Errors during load", ""] + [f"- {e}" for e in report["errors"] or ["None"]]
    (out / "verify.md").write_text("\n".join(md) + "\n")
    return report
