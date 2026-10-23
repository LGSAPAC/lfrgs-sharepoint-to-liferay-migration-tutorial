"""Managed metadata (term store) -> Liferay vocabularies and categories. Pure functions."""

from __future__ import annotations

from . import transform as t


def erc_vocabulary(term_set_id: str) -> str:
    return f"sp-termset-{term_set_id.lower()}"


def erc_category(term_guid: str) -> str:
    return f"sp-term-{term_guid.lower()}"


def _label(labels: list[dict], fallback: str = "") -> str:
    for lab in labels or []:
        if lab.get("isDefault"):
            return lab.get("name") or fallback
    return (labels[0].get("name") if labels else None) or fallback


def _flatten(terms: list[dict], parent: str | None, out: list[dict]) -> None:
    for term in terms:
        erc = erc_category(term["id"])
        out.append({"erc": erc, "termGuid": term["id"].lower(), "name": _label(term.get("labels", []), term["id"]),
                    "parentErc": parent})
        _flatten(term.get("children", []), erc, out)


def plan_vocabularies(termsets: dict[str, dict], sources: list[tuple[dict, list[dict]]]) -> tuple[list[dict], list[str]]:
    """Build vocabularies from term sets, completed with any term seen on items.

    sources: [(list plan, items)] for every migrated list/library with taxonomy fields.
    Terms that are no longer in the term store (deleted/deprecated) but still tagged on
    items are added as top-level categories so no tag is lost.
    Returns (vocabularies, warnings). Categories are ordered parents first.
    """
    warnings: list[str] = []
    vocabs: dict[str, dict] = {}
    for ts_id, ts in termsets.items():
        name = _label([{"name": n.get("name"), "isDefault": True} for n in ts.get("localizedNames", [])][:1], "")
        cats: list[dict] = []
        _flatten(ts.get("terms", []), None, cats)
        vocabs[ts_id] = {"erc": erc_vocabulary(ts_id), "termSetId": ts_id, "name": name, "categories": cats,
                         "fromItemsOnly": bool(ts.get("unavailable"))}
        if ts.get("unavailable"):
            warnings.append(f"Term set {ts_id} could not be read (TermStore.Read.All?); "
                            "its vocabulary is built flat from the values tagged on items")

    for lp, items in sources:
        for tf in lp.get("taxonomyFields", []):
            v = vocabs.setdefault(tf["termSetId"], {"erc": erc_vocabulary(tf["termSetId"]), "termSetId": tf["termSetId"],
                                                    "name": "", "categories": [], "fromItemsOnly": True})
            if not v["name"]:
                v["name"] = tf["label"]
            known = {c["termGuid"] for c in v["categories"]}
            for item in items:
                for tv in t.term_values(item, tf["field"]):
                    if tv["guid"] not in known:
                        known.add(tv["guid"])
                        v["categories"].append({"erc": erc_category(tv["guid"]), "termGuid": tv["guid"],
                                                "name": tv["label"] or tv["guid"], "parentErc": None})
    for v in vocabs.values():
        v["name"] = (v["name"] or f"Term set {v['termSetId'][:8]}")[:75]
    return list(vocabs.values()), warnings
