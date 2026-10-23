"""Document libraries -> Liferay Documents and Media. Pure functions.

  library            -> root folder (in the site's Documents and Media)
  folders            -> sub-folders, same tree
  files              -> documents (ERC sp-doc-<libraryId>-<itemId>)
  metadata columns   -> a Document Type per library (data-engine definition)
  managed metadata   -> categories (taxonomyCategoryIds)
  Created/Modified/Author/Editor -> document type fields (and optional impersonation)
  unique permissions -> explicit permissions on every affected folder and document,
                        because Liferay doesn't inherit permissions down a folder tree
"""

from __future__ import annotations

import posixpath

from . import transform as t

# Liferay data-engine field types for document type fields, by object businessType
DDM_TYPES = {
    "Text": ("text", {}), "LongText": ("text", {"displayStyle": "multiline"}),
    "RichText": ("rich_text", {}), "LongInteger": ("numeric", {"dataType": "integer"}),
    "Decimal": ("numeric", {"dataType": "double"}), "PrecisionDecimal": ("numeric", {"dataType": "double"}),
    "Date": ("date", {}), "DateTime": ("date_time", {}), "Boolean": ("checkbox", {}),
    "Picklist": ("select", {}), "MultiselectPicklist": ("select", {"multiple": True}),
}

# Documents and Media actions per SharePoint level (folders get the ADD_* actions too)
DOC_ACTIONS = {
    "Full Control": ["VIEW", "UPDATE", "DELETE", "PERMISSIONS"],
    "Design": ["VIEW", "UPDATE", "DELETE"],
    "Edit": ["VIEW", "UPDATE", "DELETE"],
    "Contribute": ["VIEW", "UPDATE"],
    "Read": ["VIEW"],
    "View Only": ["VIEW"],
}
FOLDER_EXTRA = {"Full Control": ["ADD_DOCUMENT", "ADD_SUBFOLDER"], "Design": ["ADD_DOCUMENT", "ADD_SUBFOLDER"],
                "Edit": ["ADD_DOCUMENT", "ADD_SUBFOLDER"], "Contribute": ["ADD_DOCUMENT"]}


def erc_folder(library_id: str, item_id: int | None) -> str:
    return f"sp-folder-{library_id}-{'root' if item_id is None else item_id}"


def erc_document(library_id: str, item_id: int) -> str:
    return f"sp-doc-{library_id}-{item_id}"


def document_type(lib: dict, fields: list[dict], audit: bool = True) -> dict:
    """Document Type (data-engine definition) for a library's metadata columns."""
    lid = lib["Id"]
    mapped, skipped = [], []
    for f in fields:
        if f["InternalName"] in ("Title", "FileLeafRef"):
            continue
        m = t.map_field(f, lid)
        if m is None or m.get("lookup"):
            skipped.append(f"{f.get('Title')} ({f['TypeAsString']})")
            continue
        if m.get("taxonomy"):
            continue  # categories, not a field
        mapped.append(m)
    if audit:
        mapped += t.audit_mappings()
    ddm_fields = []
    for m in mapped:
        of = m["objectField"]
        ftype, props = DDM_TYPES.get(of["businessType"], ("text", {}))
        field = {"name": m["name"], "label": of["label"], "fieldType": ftype,
                 "customProperties": dict(props), "indexType": "keyword", "localizable": False,
                 "repeatable": False, "required": False}
        if m.get("picklist"):
            field["customProperties"]["options"] = {"en_US": [
                {"label": e["name"], "value": e["key"]} for e in m["picklist"]["listTypeEntries"]]}
        ddm_fields.append(field)
    return {"erc": f"sp-doctype-{lid}", "name": f"SP {lib['Title']}"[:75], "fields": mapped,
            "dataDefinition": {"name": {"en_US": f"SP {lib['Title']}"[:75]},
                               "dataDefinitionKey": f"SP_DOCTYPE_{lid.replace('-', '').upper()}",
                               "availableLanguageIds": ["en_US"], "defaultLanguageId": "en_US",
                               "dataDefinitionFields": ddm_fields},
            "skipped": skipped}


def metadata_values(doc_type: dict, item: dict) -> dict[str, str]:
    """Document type field values for one file, as strings (data-engine contentFields)."""
    out = {}
    for m in doc_type["fields"]:
        v = t.convert_value(m, item.get(m["sp"]))
        if v is None:
            continue
        if isinstance(v, dict) and "key" in v:
            v = v["key"]
        elif isinstance(v, list):
            v = ",".join(x["key"] if isinstance(x, dict) else str(x) for x in v)
        out[m["name"]] = str(v).lower() if isinstance(v, bool) else str(v)
    return out


def plan_library(entry: dict, items: list[dict], perms: dict, audit: bool = True) -> dict:
    """Folder tree, documents, document type and effective permission scopes for one library."""
    lib, fields = entry["list"], entry["fields"]
    lid = lib["Id"]
    root_url = ((lib.get("RootFolder") or {}).get("ServerRelativeUrl") or "").rstrip("/")
    doc_type = document_type(lib, fields, audit)
    taxonomy_fields = []
    for f in fields:
        m = t.map_field(f, lid)
        if m and m.get("taxonomy"):
            taxonomy_fields.append({"field": f["InternalName"], **m["taxonomy"]})

    folders_by_url = {root_url: {"erc": erc_folder(lid, None), "name": lib["Title"], "parentErc": None,
                                 "spItemId": None, "url": root_url, "depth": 0}}
    for it in sorted((i for i in items if i.get("FSObjType") == 1), key=lambda i: i["FileRef"].count("/")):
        folders_by_url[it["FileRef"].rstrip("/")] = {
            "erc": erc_folder(lid, it["Id"]), "name": it.get("FileLeafRef") or posixpath.basename(it["FileRef"]),
            "parentErc": None, "spItemId": it["Id"], "url": it["FileRef"].rstrip("/"),
            "depth": it["FileRef"].count("/") - root_url.count("/"), "item": it}
    for url, f in folders_by_url.items():
        if url != root_url:
            parent = folders_by_url.get(posixpath.dirname(url))
            f["parentErc"] = parent["erc"] if parent else folders_by_url[root_url]["erc"]

    # Effective unique-permission scope: the nearest SharePoint object that broke inheritance
    unique_items = set(perms["items"].get(lid, {}))
    library_unique = lid in perms["lists"]

    def scope_for(item_id: int | None, url: str) -> str | None:
        if item_id is not None and str(item_id) in unique_items:
            return str(item_id)
        cur = posixpath.dirname(url)
        while cur and cur.startswith(root_url) and cur != root_url:
            folder = folders_by_url.get(cur)
            if folder and str(folder["spItemId"]) in unique_items:
                return str(folder["spItemId"])
            cur = posixpath.dirname(cur)
        return "library" if library_unique else None

    folders = []
    for f in sorted(folders_by_url.values(), key=lambda x: x["depth"]):
        f = {k: v for k, v in f.items() if k != "item"}
        f["permissionScope"] = "library" if f["spItemId"] is None and library_unique else (
            scope_for(f["spItemId"], f["url"]) if f["spItemId"] is not None else None)
        folders.append(f)

    documents, skipped = [], []
    for it in items:
        if it.get("FSObjType") != 0:
            continue
        if it.get("_skipped") or not it.get("_localPath"):
            skipped.append(f"{it['FileRef']}: {it.get('_skipped') or 'not downloaded'}")
            continue
        folder = folders_by_url.get(it.get("FileDirRef", "").rstrip("/"), folders_by_url[root_url])
        documents.append({
            "erc": erc_document(lid, it["Id"]), "spItemId": it["Id"], "fileName": it["FileLeafRef"],
            "title": it.get("Title") or posixpath.splitext(it["FileLeafRef"])[0],
            "folderErc": folder["erc"], "localPath": it["_localPath"],
            "metadata": metadata_values(doc_type, it),
            "terms": [tv["guid"] for tf in taxonomy_fields for tv in t.term_values(it, tf["field"])],
            "authorEmail": ((it.get("Author") or {}).get("EMail") or "").lower(),
            "permissionScope": scope_for(it["Id"], it["FileRef"]),
        })
    return {"libraryId": lid, "title": lib["Title"], "rootUrl": root_url, "documentType": doc_type,
            "taxonomyFields": taxonomy_fields, "folders": folders, "documents": documents,
            "skippedFiles": skipped, "moderation": bool(lib.get("EnableModeration")),
            "versioning": bool(lib.get("EnableVersioning"))}


def describe_metadata(values: dict[str, str], doc_type: dict) -> str:
    """Fallback when document types can't be set: keep metadata readable in the description."""
    labels = {m["name"]: m["objectField"]["label"]["en_US"] for m in doc_type["fields"]}
    return "\n".join(f"{labels.get(k, k)}: {v}" for k, v in values.items())
