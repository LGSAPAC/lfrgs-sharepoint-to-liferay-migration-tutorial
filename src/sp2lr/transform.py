"""Phase 2: pure transformations from SharePoint shapes to Liferay payloads.

Nothing in this module does I/O, so everything here is unit-tested
(tests/test_transform.py) and can be reviewed without a live tenant.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------- naming / ERCs

# Object field names Liferay reserves (system fields) - collisions get an "sp" prefix
RESERVED_FIELD_NAMES = {
    "companyid", "createdate", "creator", "datecreated", "datemodified",
    "externalreferencecode", "groupid", "id", "lastpublishdate", "modifieddate",
    "objectdefinitionid", "objectentryid", "status", "statusbyuserid",
    "statusbyusername", "statusdate", "userid", "username", "uuid", "scopekey",
    "keywords", "taxonomycategoryids", "actions", "friendlyurlpath",
}


def erc_list(list_id: str) -> str:
    return f"SP_LIST_{list_id.replace('-', '').upper()}"


def erc_item(list_id: str, item_id: int) -> str:
    return f"sp-item-{list_id}-{item_id}"


def erc_picklist(list_id: str, field: str) -> str:
    return f"SP_CHOICE_{list_id.replace('-', '').upper()[:12]}_{field.upper()}"[:75]


def erc_user(entra_id: str) -> str:
    return f"entra-{entra_id}"


def erc_group(sp_group_id: int, site_key: str) -> str:
    return f"sp-group-{site_key}-{sp_group_id}"


def erc_page(page_id: str) -> str:
    return f"sp-page-{page_id}"


def _words(text: str) -> list[str]:
    # SharePoint encodes non-alphanumerics in internal names as _x0020_ etc.
    text = re.sub(r"_x([0-9a-fA-F]{4})_", lambda m: chr(int(m.group(1), 16)), text)
    return [w for w in re.split(r"[^0-9A-Za-z]+", text) if w]


def pascal(text: str, max_len: int = 41) -> str:
    words = _words(text) or ["List"]
    name = "".join(w[:1].upper() + w[1:] for w in words)
    if not name[0].isalpha():
        name = "Sp" + name
    return name[:max_len]


def camel(text: str, max_len: int = 41) -> str:
    p = pascal(text, max_len)
    # Lower-case a leading acronym too: HR -> hr, HRPolicy -> hrPolicy, LaunchDate -> launchDate
    name = re.sub(r"^[A-Z]+(?=[A-Z][a-z])|^[A-Z]+$|^[A-Z]", lambda m: m.group(0).lower(), p)
    if name.lower() in RESERVED_FIELD_NAMES:
        name = "sp" + p
    return name[:max_len]


def slugify(text: str) -> str:
    text = re.sub(r"\.aspx$", "", text, flags=re.I)
    s = re.sub(r"[^0-9a-z]+", "-", text.lower()).strip("-")
    return s or "page"


def picklist_key(choice: str) -> str:
    return camel(choice, 75) if _words(choice) else "empty"


# ------------------------------------------------------------- lists -> objects

def map_field(f: dict, list_id: str) -> dict | None:
    """Map a SharePoint REST field to a Liferay object field.

    Returns {"sp": internal name, "kind": SP type, "objectField": {...},
             "picklist": {...}|None, "lookup": {...}|None} or None if unsupported.
    """
    sp_name, kind = f["InternalName"], f["TypeAsString"]
    name = "title" if sp_name == "Title" else camel(f.get("Title") or sp_name)
    label = {"en_US": f.get("Title") or sp_name}
    base = {"name": name, "label": label, "required": bool(f.get("Required")),
            "indexed": True, "indexedAsKeyword": False, "indexedLanguageId": "",
            "externalReferenceCode": f"sp-field-{f['Id']}"}
    picklist = None
    lookup = None
    taxonomy = None

    if kind == "Text":
        of = {**base, "businessType": "Text", "DBType": "String"}
    elif kind == "Note":
        if f.get("RichText"):
            of = {**base, "businessType": "RichText", "DBType": "Clob"}
        else:
            of = {**base, "businessType": "LongText", "DBType": "Clob"}
    elif kind == "Number":
        if f.get("Decimals") == 0:
            of = {**base, "businessType": "LongInteger", "DBType": "Long"}
        else:
            of = {**base, "businessType": "Decimal", "DBType": "Double"}
    elif kind == "Currency":
        of = {**base, "businessType": "PrecisionDecimal", "DBType": "BigDecimal"}
    elif kind == "DateTime":
        if f.get("DisplayFormat") == 0:
            of = {**base, "businessType": "Date", "DBType": "Date"}
        else:
            of = {**base, "businessType": "DateTime", "DBType": "DateTime",
                  "objectFieldSettings": [{"name": "timeStorage", "value": "convertToUTC"}]}
    elif kind == "Boolean":
        of = {**base, "businessType": "Boolean", "DBType": "Boolean", "required": False}
    elif kind in ("Choice", "MultiChoice"):
        perc = erc_picklist(list_id, name)
        choices = f.get("Choices") or []
        if isinstance(choices, dict):  # odata=verbose shape
            choices = choices.get("results", [])
        picklist = {
            "externalReferenceCode": perc,
            "name": f"{f.get('Title') or sp_name} ({list_id[:8]})",
            "name_i18n": {"en_US": f"{f.get('Title') or sp_name}"},
            "listTypeEntries": [
                {"key": picklist_key(c), "name": c, "name_i18n": {"en_US": c},
                 "externalReferenceCode": f"{perc}_{picklist_key(c)}"[:75]}
                for c in choices
            ],
        }
        of = {**base,
              "businessType": "MultiselectPicklist" if kind == "MultiChoice" else "Picklist",
              "DBType": "String", "listTypeDefinitionExternalReferenceCode": perc,
              "state": False}
    elif kind == "URL":
        of = {**base, "businessType": "Text", "DBType": "String"}
    elif kind in ("User", "UserMulti"):
        # People are stored as e-mail addresses; Liferay users carry the same e-mail.
        of = {**base, "businessType": "LongText" if kind == "UserMulti" else "Text",
              "DBType": "Clob" if kind == "UserMulti" else "String"}
    elif kind in ("TaxonomyFieldType", "TaxonomyFieldTypeMulti"):
        # Managed metadata becomes Liferay categories (taxonomyCategoryIds), not a field
        taxonomy = {"termSetId": (f.get("TermSetId") or "").lower(), "multi": kind.endswith("Multi"),
                    "label": f.get("Title") or sp_name}
        of = None
    elif kind in ("Lookup", "LookupMulti"):
        # Becomes an object relationship, created after all definitions exist.
        lookup = {"targetListId": (f.get("LookupList") or "").strip("{}").lower(),
                  "multi": kind == "LookupMulti",
                  "relationshipName": camel(f.get("Title") or sp_name, 30)}
        of = None
    else:
        return None
    return {"sp": sp_name, "kind": kind, "name": name, "objectField": of,
            "picklist": picklist, "lookup": lookup, "taxonomy": taxonomy}


# ----------------------------------------------------------- audit (who/when)

AUDIT_FIELDS = [
    # (SharePoint item key, Liferay field, kind, label, businessType, DBType)
    ("Created", "spCreated", "AuditDate", "Created (SharePoint)", "DateTime", "DateTime"),
    ("Modified", "spModified", "AuditDate", "Modified (SharePoint)", "DateTime", "DateTime"),
    ("Author", "spCreatedBy", "AuditUser", "Created by (SharePoint)", "Text", "String"),
    ("Editor", "spModifiedBy", "AuditUser", "Modified by (SharePoint)", "Text", "String"),
]


def audit_mappings() -> list[dict]:
    """Field mappings that keep SharePoint's Created/Modified/Author/Editor as explicit fields.

    Liferay sets dateCreated/dateModified/creator itself, so the original values
    are preserved next to them (and optionally via impersonation, see load.py).
    """
    return [{"sp": sp, "kind": kind, "name": name, "picklist": None, "lookup": None, "taxonomy": None,
             "objectField": {"name": name, "label": {"en_US": label}, "businessType": bt, "DBType": db,
                             "required": False, "indexed": True, "indexedAsKeyword": False,
                             "indexedLanguageId": ""}}
            for sp, name, kind, label, bt, db in AUDIT_FIELDS]


def iso_utc(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M:%SZ")


def term_values(item: dict, sp_field: str) -> list[dict]:
    """Managed metadata values of an item: [{"guid", "label"}]."""
    v = item.get(sp_field)
    if not v:
        return []
    vals = v.get("results", v) if isinstance(v, dict) and "results" in v else v
    vals = vals if isinstance(vals, list) else [vals]
    out = []
    for x in vals:
        if isinstance(x, dict) and x.get("TermGuid"):
            out.append({"guid": x["TermGuid"].lower(), "label": x.get("Label") or ""})
    return out


_APP_TITLE = re.compile(r"request|ticket|issue|task|approval|registration|booking|reservation|"
                        r"incident|order|claim|expense|leave|onboarding|tracker|log\b|risk|action", re.I)
_CONTENT_TITLE = re.compile(r"news|faq|product|catalog|policy|policies|article|directory|glossary|"
                            r"announcement|event|knowledge|guide|resource|link|location|team|profile|testimonial", re.I)
_STATUS_FIELD = re.compile(r"status|state|stage|priority|severity|outcome|decision", re.I)
_PERSON_ROLE = re.compile(r"assign|owner|requester|request(ed)? by|approver|reviewer|manager|agent", re.I)


def classify_list(list_info: dict, fields: list[dict], has_unique_items: bool = False) -> dict:
    """Recommend 'cms' (editorial content) or 'objects' (business application data).

    A transparent score, not magic: every signal that fired is returned as a
    reason, and the result lands in plan.md where people can override it.
    """
    app, content = [], []
    title = list_info.get("Title", "")
    if _APP_TITLE.search(title):
        app.append((2, f"list name '{title}' suggests a business process"))
    if _CONTENT_TITLE.search(title):
        content.append((2, f"list name '{title}' suggests published content"))
    for f in fields:
        kind, label = f["TypeAsString"], f.get("Title") or f["InternalName"]
        if kind in ("Choice",) and _STATUS_FIELD.search(label):
            app.append((2, f"'{label}' is a status/priority column"))
        elif kind in ("User", "UserMulti") and _PERSON_ROLE.search(label):
            app.append((2, f"'{label}' assigns work to people"))
        elif kind == "DateTime" and re.search(r"due|deadline|sla|resolved|closed", label, re.I):
            app.append((1, f"'{label}' tracks a deadline"))
        elif kind == "Note" and f.get("RichText"):
            content.append((2, f"'{label}' is rich text for readers"))
        if f.get("ValidationFormula"):
            app.append((1, f"'{label}' has a validation rule"))
    if has_unique_items or list_info.get("HasUniqueRoleAssignments"):
        app.append((2, "row-level (item) permissions"))
    if list_info.get("ItemCount", 0) > 5000:
        app.append((1, "high-volume list"))
    a, c = sum(s for s, _ in app), sum(s for s, _ in content)
    target = "objects" if a > c else "cms"
    return {"target": target, "appScore": a, "contentScore": c,
            "reasons": [r for _, r in (app if target == "objects" else content)] or
                       ["no strong signal; defaulting to CMS content structure"]}


# SharePoint view column (internal name) -> Liferay system field usable in object views
SYSTEM_VIEW_FIELDS = {"ID": "id", "Created": "dateCreated", "Modified": "dateModified",
                      "Author": "creator", "LinkTitle": "title", "LinkTitleNoMenu": "title", "Title": "title"}
_FIELD_REF = re.compile(r"<FieldRef\b([^>]*)/?>", re.I)
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def views_to_object_views(views: list[dict], plan_fields: list[dict], locale: str = "en_US") -> tuple[list[dict], list[str]]:
    """Convert SharePoint list views to Liferay object views (classic Objects only).

    Columns and sort order carry over. CAML <Where> filters are reported as
    manual work: they rarely translate 1:1 to object view filters.
    """
    names = {m["sp"]: m["name"] for m in plan_fields if m.get("objectField")}
    names.update(SYSTEM_VIEW_FIELDS)
    out, manual = [], []
    for v in views:
        cols = []
        for sp_name in v.get("fields", []):
            lr = names.get(sp_name)
            if lr and lr not in cols:
                cols.append(lr)
        if not cols:
            continue
        query = v.get("ViewQuery") or ""
        order = re.search(r"<OrderBy[^>]*>(.*?)</OrderBy>", query, re.I | re.S)
        sorts = []
        for attrs in _FIELD_REF.findall(order.group(1) if order else ""):
            a = dict(_ATTR.findall(attrs))
            sp_name, asc = a.get("Name", ""), a.get("Ascending", "TRUE")
            if sp_name in names:
                sorts.append({"objectFieldName": names[sp_name], "priority": len(sorts),
                              "sortOrder": "desc" if (asc or "").upper() == "FALSE" else "asc"})
        if re.search(r"<Where>", query, re.I):
            manual.append(f"View '{v['Title']}' has a filter; recreate it as an object view filter: {query[:200]}")
        out.append({
            "name": {locale: v["Title"]},
            "defaultObjectView": bool(v.get("DefaultView")),
            "objectViewColumns": [{"objectFieldName": n, "priority": i} for i, n in enumerate(cols)],
            "objectViewSortColumns": sorts,
            "objectViewFilterColumns": [],
        })
    return out, manual


def field_extras(f: dict, m: dict) -> list[dict]:
    """Unique-value and default-value settings for an object field (VERIFY: setting names)."""
    of = m.get("objectField")
    if not of:
        return []
    settings = []
    if f.get("EnforceUniqueValues") and of["businessType"] in ("Text", "Integer", "LongInteger"):
        settings.append({"name": "uniqueValues", "value": "true"})
    default = f.get("DefaultValue")
    if default and not str(default).startswith("["):  # skip [today], [me] tokens
        if of["businessType"] == "Picklist":
            default = picklist_key(default)
        elif of["businessType"] == "Boolean":
            default = "true" if str(default) in ("1", "true", "True") else "false"
        if of["businessType"] in ("Text", "Picklist", "Boolean", "LongInteger", "Decimal", "PrecisionDecimal"):
            settings += [{"name": "defaultValueType", "value": "inputAsValue"},
                         {"name": "defaultValue", "value": str(default)}]
    return settings


def plan_list(list_info: dict, fields: list[dict], max_attachments: int = 0,
              target: str = "cms", overrides: dict | None = None) -> dict:
    """Build the object definition + field map for one SharePoint list.

    target = "cms"     -> depot-scoped content structure inside the CMS Space
             "objects" -> classic site-scoped Liferay object
    """
    overrides = overrides or {}
    list_id = list_info["Id"]
    title = overrides.get("label") or list_info["Title"]
    name = overrides.get("name") or pascal(title, 35)
    mapped, skipped = [], []
    has_title = False
    for f in fields:
        if f["InternalName"] in overrides.get("skipFields", []):
            skipped.append({"field": f["InternalName"], "reason": "skipped by mapping.yaml"})
            continue
        m = map_field(f, list_id)
        if m is None:
            reason = f"unsupported type {f['TypeAsString']}"
            if f["TypeAsString"] == "Calculated" and f.get("Formula"):
                reason += f" (formula {f['Formula']}; rebuild as a formula field)"
            skipped.append({"field": f["InternalName"], "reason": reason})
            continue
        extras = field_extras(f, m)
        if extras:
            m["objectField"].setdefault("objectFieldSettings", []).extend(extras)
        has_title |= m["name"] == "title"
        mapped.append(m)
    if overrides.get("audit", True):
        taken = {m["name"] for m in mapped}
        mapped += [a for a in audit_mappings() if a["name"] not in taken]
    if not has_title:
        mapped.insert(0, {"sp": "Title", "kind": "Text", "name": "title", "picklist": None, "lookup": None,
                          "objectField": {"name": "title", "label": {"en_US": "Title"}, "businessType": "Text",
                                          "DBType": "String", "required": False, "indexed": True,
                                          "indexedAsKeyword": False, "indexedLanguageId": ""}})
    # De-duplicate field names
    seen: dict[str, int] = {}
    for m in mapped:
        n = m["name"]
        if n in seen:
            seen[n] += 1
            m["name"] = f"{n}{seen[n]}"
            if m["objectField"]:
                m["objectField"]["name"] = m["name"]
        else:
            seen[n] = 1
    attachments = []
    for i in range(1, min(max_attachments, 10) + 1):
        attachments.append({
            "name": f"attachment{i}", "label": {"en_US": f"Attachment {i}"},
            "businessType": "Attachment", "DBType": "Long", "required": False,
            "indexed": False, "indexedAsKeyword": False, "indexedLanguageId": "",
            "objectFieldSettings": [
                {"name": "acceptedFileExtensions", "value": "jpg, jpeg, png, gif, pdf, docx, xlsx, pptx, txt, csv, zip"},
                {"name": "fileSource", "value": "documentsAndMedia"},
                {"name": "maximumFileSize", "value": "100"},
            ],
        })
    definition = {
        "externalReferenceCode": erc_list(list_id),
        "name": name,
        "label": {"en_US": title},
        "pluralLabel": {"en_US": overrides.get("pluralLabel") or title},
        "scope": "depot" if target == "cms" else "site",
        "panelCategoryKey": "" if target == "cms" else "site_administration.content",
        "titleObjectFieldName": "title",
        "enableComments": False,
        "portlet": target != "cms",
        "objectFields": [m["objectField"] for m in mapped if m["objectField"]] + attachments,
    }
    if target == "cms":
        definition["objectFolderExternalReferenceCode"] = "L_CMS_CONTENT_STRUCTURES"
    taxonomy_fields = [{"field": m["sp"], **m["taxonomy"]} for m in mapped if m.get("taxonomy")]
    if taxonomy_fields:
        definition["enableCategorization"] = True
    return {
        "listId": list_id, "listTitle": list_info["Title"], "target": target,
        "definition": definition, "fields": mapped, "skipped": skipped,
        "picklists": [m["picklist"] for m in mapped if m["picklist"]],
        "lookups": [{"field": m["sp"], **m["lookup"]} for m in mapped if m["lookup"]],
        "attachmentFields": [a["name"] for a in attachments],
        "taxonomyFields": taxonomy_fields,
    }


def _iso_date(value: str) -> str:
    return value[:10]


def convert_value(m: dict, value: Any) -> Any:
    """Convert a SharePoint REST item value to the Liferay object entry value."""
    kind = m["kind"]
    if value is None or value == "":
        return None
    if kind == "Text" or kind == "Note":
        return value
    if kind == "Number":
        return int(value) if m["objectField"]["businessType"] == "LongInteger" else float(value)
    if kind == "Currency":
        return float(value)
    if kind == "DateTime":
        if m["objectField"]["businessType"] == "Date":
            return _iso_date(value)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind == "Boolean":
        return bool(value)
    if kind == "Choice":
        return {"key": picklist_key(value)}
    if kind == "MultiChoice":
        vals = value.get("results", []) if isinstance(value, dict) else value
        return [{"key": picklist_key(v)} for v in vals]
    if kind == "URL":
        return value.get("Url") if isinstance(value, dict) else value
    if kind == "User":
        return value.get("EMail") or value.get("Title") if isinstance(value, dict) else None
    if kind == "AuditDate":
        return iso_utc(value)
    if kind == "AuditUser":
        return (value.get("EMail") or value.get("Title")) if isinstance(value, dict) else None
    if kind == "UserMulti":
        vals = value.get("results", []) if isinstance(value, dict) and "results" in value else value
        return ", ".join(v.get("EMail") or v.get("Title", "") for v in vals or [])
    return None


def item_to_entry(plan: dict, item: dict) -> dict:
    entry: dict[str, Any] = {"externalReferenceCode": erc_item(plan["listId"], item["Id"])}
    for m in plan["fields"]:
        if m["lookup"] or not m["objectField"]:
            continue
        v = convert_value(m, item.get(m["sp"]))
        if v is not None:
            entry[m["name"]] = v
    if not entry.get("title"):
        entry["title"] = item.get("Title") or f"Item {item['Id']}"
    return entry


def lookup_target_ids(item: dict, sp_field: str, multi: bool) -> list[int]:
    v = item.get(sp_field)
    if not v:
        return []
    if multi:
        vals = v.get("results", []) if isinstance(v, dict) else v
        return [x["Id"] for x in vals if x.get("Id")]
    return [v["Id"]] if isinstance(v, dict) and v.get("Id") else []


# ---------------------------------------------------------------- pages

IMAGE_WEBPART = "d1d91016-032f-456d-98a4-721247c305e8"
_IMG_SRC = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)

LAYOUT_WIDTHS = {
    "oneColumn": [12], "fullWidth": [12], "twoColumns": [6, 6],
    "threeColumns": [4, 4, 4], "oneThirdLeftColumn": [4, 8], "oneThirdRightColumn": [8, 4],
}


def find_page_image_urls(page: dict) -> list[str]:
    urls: list[str] = []
    for wp in _iter_webparts(page):
        if wp.get("@odata.type") == "#microsoft.graph.textWebPart":
            urls += _IMG_SRC.findall(wp.get("innerHtml") or "")
        elif wp.get("webPartType") == IMAGE_WEBPART:
            for src in ((wp.get("data") or {}).get("serverProcessedContent") or {}).get("imageSources", []):
                if src.get("value"):
                    urls.append(src["value"])
    return list(dict.fromkeys(urls))


def _iter_webparts(page: dict):
    canvas = page.get("canvasLayout") or {}
    for section in canvas.get("horizontalSections", []):
        for col in section.get("columns", []):
            yield from col.get("webparts", [])
    vs = canvas.get("verticalSection")
    if vs:
        yield from vs.get("webparts", [])


def _fragment(key: str, editable: str, html_value: str, locale: str) -> dict:
    return {
        "type": "Fragment",
        "definition": {
            "fragment": {"key": key},
            "fragmentFields": [
                {"id": editable, "value": {"text": {"value_i18n": {locale: html_value}}}}
            ],
        },
    }


def rewrite_html(fragment_html: str, url_map: dict[str, str], page_links: dict[str, str]) -> str:
    """Rewrite SharePoint image and page URLs to their Liferay equivalents."""
    out = fragment_html
    for old, new in sorted({**url_map, **page_links}.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(old, new)
    return out


def webpart_to_fragment(wp: dict, locale: str, url_map: dict[str, str],
                        page_links: dict[str, str], unmigrated: list[str]) -> dict | None:
    if wp.get("@odata.type") == "#microsoft.graph.textWebPart":
        body = rewrite_html(wp.get("innerHtml") or "", url_map, page_links)
        return _fragment("BASIC_COMPONENT-paragraph", "element-text", body, locale) if body.strip() else None
    data = wp.get("data") or {}
    if wp.get("webPartType") == IMAGE_WEBPART:
        srcs = (data.get("serverProcessedContent") or {}).get("imageSources", [])
        if srcs:
            src = url_map.get(srcs[0]["value"], srcs[0]["value"])
            alt = html.escape((data.get("properties") or {}).get("altText") or data.get("title") or "")
            return _fragment("BASIC_COMPONENT-paragraph", "element-text", f'<img src="{src}" alt="{alt}" />', locale)
    title = data.get("title") or wp.get("webPartType") or "web part"
    unmigrated.append(title)
    note = (f'<div class="sp-unmigrated" data-sp-webpart="{html.escape(str(wp.get("webPartType")))}">'
            f"[Unmigrated SharePoint web part: {html.escape(title)}]</div>")
    return _fragment("BASIC_COMPONENT-paragraph", "element-text", note, locale)


def page_to_site_page(page: dict, locale: str, url_map: dict[str, str],
                      page_links: dict[str, str]) -> tuple[dict, list[str]]:
    """Convert a Graph sitePage (with canvasLayout) to a headless-delivery SitePage body."""
    unmigrated: list[str] = []
    sections = []
    canvas = page.get("canvasLayout") or {}
    for section in canvas.get("horizontalSections", []):
        columns = section.get("columns", [])
        widths = [c.get("width") for c in columns]
        if not all(widths):
            widths = LAYOUT_WIDTHS.get(section.get("layout", "oneColumn"), [12] * len(columns))
        col_elements = []
        for col, width in zip(columns, widths):
            frags = [f for f in (webpart_to_fragment(wp, locale, url_map, page_links, unmigrated)
                                 for wp in col.get("webparts", [])) if f]
            col_elements.append({"type": "Column", "definition": {"size": int(width)}, "pageElements": frags})
        sections.append({
            "type": "Section",
            "definition": {"layout": {"widthType": "Fluid" if section.get("layout") == "fullWidth" else "Fixed"}},
            "pageElements": [{
                "type": "Row",
                "definition": {"gutters": True, "numberOfColumns": len(col_elements),
                               "modulesPerRow": len(col_elements), "reverseOrder": False,
                               "verticalAlignment": "top"},
                "pageElements": col_elements,
            }],
        })
    vs = canvas.get("verticalSection")
    if vs:
        frags = [f for f in (webpart_to_fragment(wp, locale, url_map, page_links, unmigrated)
                             for wp in vs.get("webparts", [])) if f]
        if frags:
            sections.append({"type": "Section", "definition": {"layout": {"widthType": "Fixed"}},
                             "pageElements": frags})
    title = page.get("title") or page.get("name")
    heading = {"type": "Section", "definition": {"layout": {"widthType": "Fixed"}},
               "pageElements": [_fragment("BASIC_COMPONENT-heading", "element-text", html.escape(title), locale)]}
    body = {
        "externalReferenceCode": erc_page(page["id"]),
        "title": title,
        "title_i18n": {locale: title},
        "friendlyUrlPath": "/" + slugify(page.get("name") or title),
        "friendlyUrlPath_i18n": {locale: "/" + slugify(page.get("name") or title)},
        "pageDefinition": {"pageElement": {"type": "Root", "pageElements": [heading] + sections}},
    }
    if page.get("description"):
        body["pageSettings"] = {"seoSettings": {"description_i18n": {locale: page["description"]}}}
    return body, unmigrated


def page_to_html(page: dict, url_map: dict[str, str], page_links: dict[str, str]) -> str:
    """Flatten a page canvas into one HTML body (news posts -> blog article body)."""
    parts = []
    for wp in _iter_webparts(page):
        if wp.get("@odata.type") == "#microsoft.graph.textWebPart":
            parts.append(rewrite_html(wp.get("innerHtml") or "", url_map, page_links))
        elif wp.get("webPartType") == IMAGE_WEBPART:
            data = wp.get("data") or {}
            srcs = (data.get("serverProcessedContent") or {}).get("imageSources", [])
            if srcs:
                alt = html.escape((data.get("properties") or {}).get("altText") or "")
                parts.append(f'<p><img src="{url_map.get(srcs[0]["value"], srcs[0]["value"])}" alt="{alt}" /></p>')
    return "\n".join(p for p in parts if p.strip())


def is_news(page: dict) -> bool:
    return page.get("promotionKind") == "newsPost"


def page_link_map(pages: list[dict], sp_site_path: str, lr_site_prefix: str) -> dict[str, str]:
    """Map /sites/x/SitePages/Foo.aspx (and absolute forms) to /web/<site>/foo."""
    out = {}
    for p in pages:
        name = p.get("name")
        if not name:
            continue
        target = f"{lr_site_prefix}/{slugify(name)}"
        rel = f"{sp_site_path}/SitePages/{name}"
        out[rel] = target
        if p.get("webUrl"):
            out[p["webUrl"]] = target
    return out


def navigation_items(nodes: list[dict], page_links: dict[str, str]) -> list[dict]:
    items = []
    for n in nodes:
        url = n.get("Url") or ""
        url = page_links.get(url, url)
        items.append({
            "name": n.get("Title"), "name_i18n": {"en_US": n.get("Title")},
            "type": "url", "url": url, "useCustomName": True,
            "navigationMenuItems": navigation_items(n.get("Children") or [], page_links),
        })
    return items


# ---------------------------------------------------------------- permissions

# Default mapping from SharePoint permission levels to Liferay roles.
# CMS Spaces are depot groups, so they use the Asset Library role family.
# "SP Contributor" is created by the migrator (site and depot variants).
# Overridable in mapping.yaml -> permissions.levels
DEFAULT_LEVEL_MAP = {
    "Full Control": {"siteRole": "Site Owner", "spaceRole": "Asset Library Owner"},
    "Design": {"siteRole": "Site Content Reviewer", "spaceRole": "Asset Library Administrator"},
    "Edit": {"siteRole": "SP Contributor", "spaceRole": "SP Space Contributor"},
    "Contribute": {"siteRole": "SP Contributor", "spaceRole": "SP Space Contributor"},
    "Read": {"siteRole": None, "spaceRole": None},  # plain membership = view
    "View Only": {"siteRole": None, "spaceRole": None},
    "Limited Access": None,  # SharePoint-internal, never migrated
}

# Entry-level actions for item permissions
LEVEL_ENTRY_ACTIONS = {
    "Full Control": ["VIEW", "UPDATE", "DELETE", "PERMISSIONS"],
    "Design": ["VIEW", "UPDATE", "DELETE"],
    "Edit": ["VIEW", "UPDATE", "DELETE"],
    "Contribute": ["VIEW", "UPDATE"],
    "Read": ["VIEW"],
    "View Only": ["VIEW"],
}


def role_scope(target: str) -> str:
    """Where a migrated record lives: 'cms' (the Space) or 'site' (Objects, Documents, pages)."""
    return "cms" if target == "cms" else "site"


def principal_role_name(principal_name: str, target: str) -> str:
    """Role used for migrated item permissions: depot role in the Space, site role in the site."""
    prefix = "SP Principal - " if role_scope(target) == "cms" else "SP Site Principal - "
    return f"{prefix}{principal_name}"[:75]


def strongest_level(bindings: list[dict]) -> str | None:
    order = ["Full Control", "Design", "Edit", "Contribute", "Read", "View Only"]
    names = {b.get("Name") for b in bindings}
    for lvl in order:
        if lvl in names:
            return lvl
    custom = [n for n in names if n and n != "Limited Access"]
    return custom[0] if custom else None
