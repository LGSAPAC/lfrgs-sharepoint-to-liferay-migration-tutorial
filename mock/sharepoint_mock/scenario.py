"""The test site from docs/VALIDATION-CHECKLIST.md ("End-to-end scenario"), as SharePoint returns it.

Values are the raw API shapes (REST with odata=nometadata, Graph v1.0) that sp2lr reads.
`build(mode, site_url)` returns the scenario for SharePoint Online ("online") or
SharePoint Server Subscription Edition ("server"); the two differ where the platforms do:
login names, groups, and how modern pages are read.

These shapes are written from Microsoft's documentation and the code's expectations. They
are not captured from a live tenant: the mock is for developing and regression-testing the
tooling, not for proving it works against real SharePoint.
"""

from __future__ import annotations

import base64
import copy
import json
from urllib.parse import urlparse

SITE_GUID = "11111111-aaaa-bbbb-cccc-000000000001"
WEB_GUID = "22222222-aaaa-bbbb-cccc-000000000002"
PRODUCTS = "aaaaaaaa-0000-0000-0000-000000000001"
ORDERS = "aaaaaaaa-0000-0000-0000-000000000002"
POLICIES = "bbbbbbbb-0000-0000-0000-000000000001"
SITE_PAGES = "dddddddd-0000-0000-0000-000000000001"
TERMSET = "cccccccc-0000-0000-0000-000000000001"
SALES_GROUP = "eeeeeeee-0000-0000-0000-000000000001"  # Entra security group (Online)
SALES_SID = "s-1-5-21-1004336348-1177238915-682003330-1105"  # AD group (Server)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

IMAGE_WEBPART = "d1d91016-032f-456d-98a4-721247c305e8"
QUICK_LINKS_WEBPART = "c70391ea-0b10-4ee9-b2b4-006d3fcad0cd"

# People: (key, display name, given, surname, job title)
PEOPLE = [("jane", "Jane Doe", "Jane", "Doe", "Marketing Lead"),
          ("bob", "Bob Smith", "Bob", "Smith", "Sales Rep"),
          ("old", "Old User", "Old", "User", "")]


def _field(fid, name, title, kind, **kw):
    return {"Id": fid, "InternalName": name, "Title": title, "TypeAsString": kind, "Hidden": False,
            "ReadOnlyField": False, "FromBaseType": False, "Required": kw.pop("Required", False), **kw}


def build(mode: str, site_url: str) -> dict:
    """Everything the mock serves, for one platform."""
    server = mode == "server"
    u = urlparse(site_url)
    host, path = u.hostname or "localhost", u.path.rstrip("/")
    origin = f"{u.scheme}://{u.netloc}"

    def login(key: str) -> str:
        return f"i:0#.w|contoso\\{key}" if server else f"i:0#.f|membership|{key}@contoso.com"

    def person(key: str) -> dict:
        title = next(p[1] for p in PEOPLE if p[0] == key)
        return {"EMail": f"{key}@contoso.com", "Title": title, "Id": 10 + [p[0] for p in PEOPLE].index(key)}

    site_users = [{"Id": 10 + i, "Title": t, "Email": f"{k}@contoso.com", "LoginName": login(k),
                   "PrincipalType": 1, "IsSiteAdmin": k == "jane", "UserPrincipalName": f"{k}@contoso.com"}
                  for i, (k, t, *_r) in enumerate(PEOPLE)]
    sales = ({"Id": 20, "Title": "CONTOSO\\Sales", "LoginName": f"c:0+.w|{SALES_SID}", "PrincipalType": 4}
             if server else
             {"Id": 20, "Title": "Sales", "LoginName": f"c:0t.c|tenant|{SALES_GROUP}", "PrincipalType": 4})
    site_users.append({**sales, "Email": "", "IsSiteAdmin": False, "UserPrincipalName": None})

    groups = [
        {"Id": 3, "Title": "Marketing Owners", "Description": "Full control", "LoginName": "Marketing Owners",
         "OwnerTitle": "Marketing Owners", "Users": [{"LoginName": login("jane"), "PrincipalType": 1}]},
        {"Id": 5, "Title": "Marketing Members", "Description": "Can edit", "LoginName": "Marketing Members",
         "OwnerTitle": "Marketing Owners",
         # Online: an Entra group (expanded through Graph). Server: an AD group (can't be expanded
         # over REST) plus Bob added directly, so the members group isn't empty.
         "Users": [{"LoginName": sales["LoginName"], "PrincipalType": 4}]
         + ([{"LoginName": login("bob"), "PrincipalType": 1}] if server else [])},
        {"Id": 4, "Title": "Marketing Visitors", "Description": "Can read", "LoginName": "Marketing Visitors",
         "OwnerTitle": "Marketing Owners", "Users": []},
    ]
    associated = {"AssociatedOwnerGroup": 3, "AssociatedMemberGroup": 5, "AssociatedVisitorGroup": 4}

    def ra(gid: int, title: str, *levels: str) -> dict:
        return {"PrincipalId": gid, "Member": {"Id": gid, "Title": title, "LoginName": title, "PrincipalType": 8},
                "RoleDefinitionBindings": [{"Name": lv, "RoleTypeKind": 0} for lv in levels]}

    role_assignments = {
        "web": [ra(3, "Marketing Owners", "Full Control"), ra(5, "Marketing Members", "Edit", "Limited Access"),
                ra(4, "Marketing Visitors", "Read")],
        (ORDERS, 2): [ra(3, "Marketing Owners", "Full Control")],
        (POLICIES, 10): [ra(3, "Marketing Owners", "Full Control"), ra(5, "Marketing Members", "Read")],
    }

    lists = [
        {"Id": PRODUCTS, "Title": "Product Catalog", "Description": "", "BaseTemplate": 100, "ItemCount": 2,
         "Hidden": False, "HasUniqueRoleAssignments": False, "EntityTypeName": "Product_x0020_CatalogList",
         "EnableModeration": False, "EnableVersioning": True, "EnableAttachments": True, "ContentTypesEnabled": False,
         "IsSystemList": False, "IsCatalog": False, "RootFolder": {"ServerRelativeUrl": f"{path}/Lists/Product Catalog"}},
        {"Id": ORDERS, "Title": "Orders", "Description": "", "BaseTemplate": 100, "ItemCount": 2, "Hidden": False,
         "HasUniqueRoleAssignments": False, "EntityTypeName": "OrdersList", "EnableModeration": False,
         "EnableVersioning": False, "EnableAttachments": True, "ContentTypesEnabled": False, "IsSystemList": False,
         "IsCatalog": False, "RootFolder": {"ServerRelativeUrl": f"{path}/Lists/Orders"}},
        {"Id": POLICIES, "Title": "Policies", "Description": "", "BaseTemplate": 101, "ItemCount": 4, "Hidden": False,
         "HasUniqueRoleAssignments": False, "EntityTypeName": "Policies", "EnableModeration": False,
         "EnableVersioning": True, "EnableAttachments": False, "ContentTypesEnabled": False, "IsSystemList": False,
         "IsCatalog": False, "RootFolder": {"ServerRelativeUrl": f"{path}/Policies"}},
        {"Id": SITE_PAGES, "Title": "Site Pages", "Description": "", "BaseTemplate": 119, "ItemCount": 3,
         "Hidden": False, "HasUniqueRoleAssignments": False, "EntityTypeName": "SitePages", "EnableModeration": False,
         "EnableVersioning": True, "EnableAttachments": False, "ContentTypesEnabled": True, "IsSystemList": False,
         "IsCatalog": False, "RootFolder": {"ServerRelativeUrl": f"{path}/SitePages"}},
    ]

    fields = {
        PRODUCTS: [
            _field("f1", "Title", "Title", "Text", Required=True),
            _field("f2", "Price", "Price", "Currency"),
            _field("f3", "Category", "Category", "Choice", Choices=["Hardware", "Software & Services"]),
            _field("f4", "Launch_x0020_Date", "Launch Date", "DateTime", DisplayFormat=0),
            _field("f5", "Description", "Description", "Note", RichText=True),
            _field("f6", "Status", "Status", "Text"),
            _field("f7", "Calc", "Calc", "Calculated"),
            _field("f8", "Dept", "Department", "TaxonomyFieldType", TermSetId=TERMSET),
        ],
        ORDERS: [
            _field("g1", "Title", "Title", "Text"),
            _field("g2", "Product", "Product", "Lookup", LookupList="{" + PRODUCTS.upper() + "}", LookupField="Title"),
            _field("g3", "Owner", "Owner", "User"),
            _field("g4", "Qty", "Quantity", "Number", Decimals=0),
            _field("g5", "Tags", "Tags", "MultiChoice", Choices=["Urgent", "Gift"]),
            _field("g6", "Shipped", "Shipped", "Boolean"),
            _field("g7", "OrderStatus", "Order Status", "Choice", Choices=["New", "Shipped"], DefaultValue="New"),
        ],
        POLICIES: [
            _field("h1", "Title", "Title", "Text"),
            _field("h2", "Audience", "Audience", "Choice", Choices=["HR", "IT"]),
            _field("h3", "Expiry", "Expiry", "DateTime", DisplayFormat=0),
            _field("h4", "Region", "Region", "TaxonomyFieldTypeMulti", TermSetId=TERMSET),
            # Hidden and system fields the client must skip
            {**_field("hx", "_dlc_DocId", "Document ID", "Text"), "Hidden": True},
        ],
    }

    views = {
        ORDERS: [
            {"Id": "v1", "Title": "All Items", "DefaultView": True, "PersonalView": False, "Hidden": False,
             "RowLimit": 30, "ViewQuery": '<OrderBy><FieldRef Ascending="FALSE" Name="Created" /></OrderBy>',
             "ViewFields": {"Items": ["LinkTitle", "Product", "Qty", "OrderStatus"], "SchemaXml": ""}},
            {"Id": "v2", "Title": "Open orders", "DefaultView": False, "PersonalView": False, "Hidden": False,
             "RowLimit": 30,
             "ViewQuery": '<Where><Eq><FieldRef Name="Shipped"/><Value Type="Boolean">0</Value></Eq></Where>',
             "ViewFields": {"Items": ["LinkTitle", "Owner"], "SchemaXml": ""}},
            {"Id": "v3", "Title": "My view", "DefaultView": False, "PersonalView": True, "Hidden": False,
             "RowLimit": 30, "ViewQuery": "", "ViewFields": {"Items": ["LinkTitle"], "SchemaXml": ""}},
        ],
        PRODUCTS: [{"Id": "v4", "Title": "All Items", "DefaultView": True, "PersonalView": False, "Hidden": False,
                    "RowLimit": 30, "ViewQuery": "", "ViewFields": {"Items": ["LinkTitle", "Price"], "SchemaXml": ""}}],
    }

    def audit(created, modified, author, editor):
        return {"Created": created, "Modified": modified, "Author": person(author), "Editor": person(editor)}

    items = {
        PRODUCTS: [
            {"Id": 1, "Title": "Widget", "Price": 9.5, "Category": "Hardware",
             "Launch_x0020_Date": "2026-01-15T08:00:00Z", "Description": "<p>Best <b>widget</b></p>",
             "Status": "Active", "HasUniqueRoleAssignments": False, "Attachments": True,
             "Dept": {"Label": "Sales", "TermGuid": "T-SALES", "WssId": 1},
             **audit("2024-03-01T09:00:00Z", "2025-02-01T10:30:00Z", "jane", "bob")},
            {"Id": 2, "Title": "Support", "Price": 100, "Category": "Software & Services",
             "HasUniqueRoleAssignments": False, "Attachments": False,
             "Dept": {"Label": "Legacy", "TermGuid": "T-OLD", "WssId": 2},
             **audit("2024-04-01T09:00:00Z", "2024-04-01T09:00:00Z", "bob", "bob")},
        ],
        ORDERS: [
            {"Id": 1, "Title": "PO-1", "Product": {"Id": 1, "Title": "Widget"}, "Owner": person("jane"),
             "Qty": 3.0, "Tags": ["Urgent", "Gift"], "Shipped": True, "HasUniqueRoleAssignments": False,
             "Attachments": False, **audit("2025-05-01T09:00:00Z", "2025-05-02T09:00:00Z", "bob", "bob")},
            {"Id": 2, "Title": "PO-2", "Product": {"Id": 2, "Title": "Support"}, "Owner": None, "Qty": 1.0,
             "Tags": [], "Shipped": False, "HasUniqueRoleAssignments": True, "Attachments": False,
             **audit("2025-06-01T09:00:00Z", "2025-06-01T09:00:00Z", "jane", "jane")},
        ],
        POLICIES: [
            {"Id": 10, "FSObjType": 1, "FileLeafRef": "HR", "FileRef": f"{path}/Policies/HR",
             "FileDirRef": f"{path}/Policies", "HasUniqueRoleAssignments": True, "Title": None,
             **audit("2023-01-01T08:00:00Z", "2023-01-01T08:00:00Z", "jane", "jane")},
            {"Id": 11, "FSObjType": 0, "FileLeafRef": "handbook.pdf", "FileRef": f"{path}/Policies/HR/handbook.pdf",
             "FileDirRef": f"{path}/Policies/HR", "Title": "Employee Handbook", "Audience": "HR",
             "Expiry": "2027-01-01T00:00:00Z", "HasUniqueRoleAssignments": False,
             "Region": [{"Label": "EMEA", "TermGuid": "T-EMEA", "WssId": 3}],
             "File": {"Length": str(len(PDF)), "UIVersionLabel": "3.0"},
             **audit("2023-05-01T08:00:00Z", "2024-06-01T08:00:00Z", "jane", "jane")},
            {"Id": 12, "FSObjType": 0, "FileLeafRef": "readme.txt", "FileRef": f"{path}/Policies/readme.txt",
             "FileDirRef": f"{path}/Policies", "Title": None, "HasUniqueRoleAssignments": False, "Region": [],
             "File": {"Length": "8", "UIVersionLabel": "1.0"},
             **audit("2024-01-01T08:00:00Z", "2024-01-01T08:00:00Z", "bob", "bob")},
            {"Id": 13, "FSObjType": 0, "FileLeafRef": "video.mp4", "FileRef": f"{path}/Policies/video.mp4",
             "FileDirRef": f"{path}/Policies", "Title": None, "HasUniqueRoleAssignments": False, "Region": [],
             "File": {"Length": str(300 * 1024 * 1024), "UIVersionLabel": "1.0"},  # over the 250 MB default
             **audit("2024-01-01T08:00:00Z", "2024-01-01T08:00:00Z", "bob", "bob")},
        ],
    }
    attachments = {(PRODUCTS, 1): [{"FileName": "spec.txt",
                                    "ServerRelativeUrl": f"{path}/Lists/Product Catalog/Attachments/1/spec.txt"}]}
    files = {
        f"{path}/Policies/HR/handbook.pdf": PDF,
        f"{path}/Policies/readme.txt": b"read me\n",
        f"{path}/Lists/Product Catalog/Attachments/1/spec.txt": b"Widget spec v1\n",
        f"{path}/SiteAssets/team.png": PNG,
    }

    # ------------------------------------------------------------- pages
    pages = [
        {"id": "p-home", "itemId": 1, "name": "Home.aspx", "title": "Home", "description": "", "promoted": 0,
         "sections": [{"factors": [6, 6], "columns": [
             [{"text": f'<p>See <a href="{path}/SitePages/About-Us.aspx">about us</a></p>'}],
             [{"webPartId": QUICK_LINKS_WEBPART, "title": "Quick links", "properties": {}, "images": {}}]]}],
         **audit("2023-01-01T08:00:00Z", "2025-01-01T08:00:00Z", "jane", "jane")},
        {"id": "p-about", "itemId": 2, "name": "About-Us.aspx", "title": "About Us", "description": "Who we are",
         "promoted": 0,
         "sections": [{"factors": [12], "columns": [[
             {"webPartId": IMAGE_WEBPART, "title": "Image", "properties": {"altText": "Team"},
              "images": {"imageSource": f"{path}/SiteAssets/team.png"}},
             {"text": "<p>We sell <b>widgets</b>.</p>"}]]}],
         **audit("2023-01-01T08:00:00Z", "2024-01-01T08:00:00Z", "jane", "bob")},
        {"id": "p-news", "itemId": 3, "name": "Q3-Results.aspx", "title": "Q3 results are in",
         "description": "Record quarter", "promoted": 2, "firstPublished": "2026-09-30T07:00:00Z",
         "sections": [{"factors": [12], "columns": [[{"text": "<p>Revenue up 20%</p>"}]]}],
         **audit("2026-09-29T07:00:00Z", "2026-09-30T07:00:00Z", "jane", "jane")},
    ]

    def graph_webpart(c: dict) -> dict:
        if "text" in c:
            return {"@odata.type": "#microsoft.graph.textWebPart", "innerHtml": c["text"]}
        return {"@odata.type": "#microsoft.graph.standardWebPart", "webPartType": c["webPartId"],
                "data": {"title": c["title"], "properties": c["properties"],
                         "serverProcessedContent": {"imageSources": [{"key": k, "value": v}
                                                                     for k, v in c["images"].items()]}}}

    def graph_page(p: dict) -> dict:
        names = {(6, 6): "twoColumns", (12,): "oneColumn", (4, 4, 4): "threeColumns"}
        return {"id": p["id"], "name": p["name"], "title": p["title"], "description": p["description"],
                "webUrl": f"{origin}{path}/SitePages/{p['name']}", "pageLayout": "article",
                "promotionKind": "newsPost" if p["promoted"] == 2 else "page",
                "createdDateTime": p["Created"], "lastModifiedDateTime": p["Modified"],
                "canvasLayout": {"horizontalSections": [
                    {"layout": names[tuple(s["factors"])], "columns": [
                        {"width": f, "webparts": [graph_webpart(c) for c in col]}
                        for f, col in zip(s["factors"], s["columns"])]} for s in p["sections"]]}}

    def canvas_content(p: dict) -> str:
        """CanvasContent1 as stored on the Site Pages item (what SharePoint Server exposes)."""
        controls = []
        for z, s in enumerate(p["sections"], start=1):
            for ci, (f, col) in enumerate(zip(s["factors"], s["columns"]), start=1):
                if not col:
                    controls.append({"position": {"zoneIndex": z, "sectionIndex": ci, "sectionFactor": f,
                                                  "layoutIndex": 1}, "displayMode": 2, "emphasis": {}})
                for k, c in enumerate(col, start=1):
                    pos = {"zoneIndex": z, "sectionIndex": ci, "controlIndex": k, "sectionFactor": f, "layoutIndex": 1}
                    if "text" in c:
                        controls.append({"controlType": 4, "id": f"t{z}{ci}{k}", "position": pos,
                                         "displayMode": 2, "innerHTML": c["text"]})
                    else:
                        controls.append({"controlType": 3, "id": f"w{z}{ci}{k}", "webPartId": c["webPartId"],
                                         "position": pos, "webPartData": {
                                             "id": c["webPartId"], "instanceId": f"w{z}{ci}{k}", "title": c["title"],
                                             "properties": c["properties"],
                                             "serverProcessedContent": {"htmlStrings": {}, "searchablePlainTexts": {},
                                                                        "imageSources": c["images"], "links": {}}}})
        controls.append({"controlType": 0, "pageSettingsSlice": {"isDefaultDescription": True}})
        return json.dumps(controls)

    site_page_items = [
        {"Id": p["itemId"], "Title": p["title"], "FileLeafRef": p["name"], "FileRef": f"{path}/SitePages/{p['name']}",
         "Description": p["description"], "PromotedState": p["promoted"], "CanvasContent1": canvas_content(p),
         "FirstPublishedDate": p.get("firstPublished"), "HasUniqueRoleAssignments": False,
         "ClientSideApplicationId": "b6917cb1-93a0-4b97-a84d-7cf49975d4ec", **audit(
             p["Created"], p["Modified"], p["Author"]["EMail"].split("@")[0], p["Editor"]["EMail"].split("@")[0])}
        for p in pages
    ]
    # A classic wiki page: no CanvasContent1, so it isn't a modern page
    site_page_items.append({"Id": 4, "Title": "Old wiki", "FileLeafRef": "Old-Wiki.aspx",
                            "FileRef": f"{path}/SitePages/Old-Wiki.aspx", "Description": None, "PromotedState": 0,
                            "CanvasContent1": None, "HasUniqueRoleAssignments": False,
                            **audit("2015-01-01T08:00:00Z", "2015-01-01T08:00:00Z", "old", "old")})

    navigation = {
        "QuickLaunch": [
            {"Id": 1, "Title": "Home", "Url": f"{path}/SitePages/Home.aspx", "IsExternal": False, "Children": []},
            {"Id": 2, "Title": "About", "Url": f"{path}/SitePages/About-Us.aspx", "IsExternal": False,
             "Children": [{"Id": 3, "Title": "Bing", "Url": "https://bing.com", "IsExternal": True, "Children": []}]},
        ],
        "TopNavigationBar": [],
    }

    terms = [{"id": "T-SALES", "labels": [{"name": "Sales", "isDefault": True, "languageTag": "en-US"}],
              "children": [{"id": "T-EMEA", "labels": [{"name": "EMEA", "isDefault": True, "languageTag": "en-US"}],
                            "children": []}]}]

    profiles = {k: {"id": f"u-{k}", "displayName": t, "givenName": g, "surname": s, "jobTitle": j,
                    "mail": f"{k}@contoso.com", "userPrincipalName": f"{k}@contoso.com",
                    "department": "Marketing", "accountEnabled": k != "old", "preferredLanguage": "en-US"}
                for k, t, g, s, j in PEOPLE}

    return copy.deepcopy({
        "mode": mode, "host": host, "path": path, "origin": origin,
        "site": {"id": f"{host},{SITE_GUID},{WEB_GUID}", "displayName": "Marketing", "name": path.rsplit("/", 1)[-1],
                 "webUrl": f"{origin}{path}"},
        "web": {"Id": WEB_GUID, "Title": "Marketing", "Description": "Marketing team site", "Url": f"{origin}{path}",
                "ServerRelativeUrl": path, "Language": 1033, "HasUniqueRoleAssignments": True,
                "WelcomePage": "SitePages/Home.aspx"},
        "siteId": SITE_GUID, "siteUsers": site_users, "groups": groups, "associated": associated,
        "roleDefinitions": [{"Id": 1073741829, "Name": "Full Control", "RoleTypeKind": 5, "Hidden": False},
                            {"Id": 1073741830, "Name": "Edit", "RoleTypeKind": 6, "Hidden": False},
                            {"Id": 1073741826, "Name": "Read", "RoleTypeKind": 2, "Hidden": False},
                            {"Id": 1073741825, "Name": "Limited Access", "RoleTypeKind": 1, "Hidden": True}],
        "roleAssignments": role_assignments, "lists": lists, "fields": fields, "views": views, "items": items,
        "attachments": attachments, "files": files, "pages": [graph_page(p) for p in pages],
        "sitePageItems": site_page_items, "navigation": navigation,
        "termSet": {"id": TERMSET, "localizedNames": [{"name": "Departments", "languageTag": "en-US"}], "terms": terms},
        "profiles": profiles,
        "entraGroups": {SALES_GROUP: ["bob"]},
    })
