"""A small synthetic SharePoint extract, shaped like the real Graph / REST responses."""

import json

import pytest

SITE_GRAPH_ID = "contoso.sharepoint.com,11111111-aaaa-bbbb-cccc-000000000001,22222222-aaaa-bbbb-cccc-000000000002"
PRODUCTS = "aaaaaaaa-0000-0000-0000-000000000001"
ORDERS = "aaaaaaaa-0000-0000-0000-000000000002"
POLICIES = "bbbbbbbb-0000-0000-0000-000000000001"
TERMSET = "cccccccc-0000-0000-0000-000000000001"


def _field(fid, name, title, kind, **kw):
    return {"Id": fid, "InternalName": name, "Title": title, "TypeAsString": kind,
            "Required": kw.pop("Required", False), **kw}


EXTRACT = {
    "site.json": {
        "graph": {"id": SITE_GRAPH_ID, "displayName": "Marketing"},
        "web": {"Title": "Marketing", "Url": "https://contoso.sharepoint.com/sites/marketing",
                "ServerRelativeUrl": "/sites/marketing", "WelcomePage": "SitePages/Home.aspx"},
        "navigation": {"quickLaunch": [
            {"Title": "Home", "Url": "/sites/marketing/SitePages/Home.aspx", "Children": []},
            {"Title": "About", "Url": "/sites/marketing/SitePages/About-Us.aspx", "Children": [
                {"Title": "Bing", "Url": "https://bing.com", "Children": []}]},
        ], "topNavigationBar": []},
    },
    "users.json": {
        "siteUsers": [],
        "profiles": {
            "jane@contoso.com": {"id": "u-1", "mail": "jane@contoso.com", "userPrincipalName": "jane@contoso.com",
                                 "givenName": "Jane", "surname": "Doe", "accountEnabled": True},
            "bob@contoso.com": {"id": "u-2", "mail": "bob@contoso.com", "userPrincipalName": "bob@contoso.com",
                                "givenName": "Bob", "surname": "Smith", "accountEnabled": True},
            "old@contoso.com": {"id": "u-3", "mail": "old@contoso.com", "userPrincipalName": "old@contoso.com",
                                "givenName": "Old", "surname": "User", "accountEnabled": False},
        },
        "entraGroups": {"g-sales": [{"userPrincipalName": "bob@contoso.com"}]},
    },
    "groups.json": [
        {"Id": 3, "Title": "Marketing Owners", "associatedAs": "AssociatedOwnerGroup",
         "Users": [{"LoginName": "i:0#.f|membership|jane@contoso.com"}]},
        {"Id": 5, "Title": "Marketing Members", "associatedAs": "AssociatedMemberGroup",
         "Users": [{"LoginName": "c:0t.c|tenant|g-sales"}]},
    ],
    "permissions.json": {
        "roleDefinitions": [],
        "web": [
            {"Member": {"Id": 3, "Title": "Marketing Owners", "PrincipalType": 8},
             "RoleDefinitionBindings": [{"Name": "Full Control"}]},
            {"Member": {"Id": 5, "Title": "Marketing Members", "PrincipalType": 8},
             "RoleDefinitionBindings": [{"Name": "Edit"}, {"Name": "Limited Access"}]},
        ],
        "lists": {},
        "items": {ORDERS: {"2": [
            {"Member": {"Id": 3, "Title": "Marketing Owners", "PrincipalType": 8},
             "RoleDefinitionBindings": [{"Name": "Full Control"}]}]},
                  POLICIES: {"10": [
            {"Member": {"Id": 3, "Title": "Marketing Owners", "PrincipalType": 8},
             "RoleDefinitionBindings": [{"Name": "Full Control"}]},
            {"Member": {"Id": 5, "Title": "Marketing Members", "PrincipalType": 8},
             "RoleDefinitionBindings": [{"Name": "Read"}]}]}},
        "pages": {},
    },
    "lists.json": [
        {"list": {"Id": PRODUCTS, "Title": "Product Catalog", "BaseTemplate": 100, "ItemCount": 2},
         "fields": [
             _field("f1", "Title", "Title", "Text", Required=True),
             _field("f2", "Price", "Price", "Currency"),
             _field("f3", "Category", "Category", "Choice", Choices=["Hardware", "Software & Services"]),
             _field("f4", "Launch_x0020_Date", "Launch Date", "DateTime", DisplayFormat=0),
             _field("f5", "Description", "Description", "Note", RichText=True),
             _field("f6", "Status", "Status", "Text"),  # collides with a reserved name
             _field("f7", "Calc", "Calc", "Calculated"),
             _field("f8", "Dept", "Department", "TaxonomyFieldType", TermSetId=TERMSET),
         ], "itemCount": 2},
        {"list": {"Id": ORDERS, "Title": "Orders", "BaseTemplate": 100, "ItemCount": 2},
         "fields": [
             _field("g1", "Title", "Title", "Text"),
             _field("g2", "Product", "Product", "Lookup", LookupList="{" + PRODUCTS + "}", LookupField="Title"),
             _field("g3", "Owner", "Owner", "User"),
             _field("g4", "Qty", "Quantity", "Number", Decimals=0),
             _field("g5", "Tags", "Tags", "MultiChoice", Choices=["Urgent", "Gift"]),
             _field("g6", "Shipped", "Shipped", "Boolean"),
             _field("g7", "OrderStatus", "Order Status", "Choice", Choices=["New", "Shipped"], DefaultValue="New"),
         ],
         "views": [
             {"Title": "All Items", "DefaultView": True, "fields": ["LinkTitle", "Product", "Qty", "OrderStatus"],
              "ViewQuery": '<OrderBy><FieldRef Ascending="FALSE" Name="Created" /></OrderBy>'},
             {"Title": "Open orders", "DefaultView": False, "fields": ["LinkTitle", "Owner"],
              "ViewQuery": '<Where><Eq><FieldRef Name="Shipped"/><Value Type="Boolean">0</Value></Eq></Where>'},
         ],
         "itemCount": 2},
    ],
    "lists/" + PRODUCTS + ".items.json": [
        {"Id": 1, "Title": "Widget", "Price": 9.5, "Category": "Hardware", "Launch_x0020_Date": "2026-01-15T08:00:00Z",
         "Description": "<p>Best <b>widget</b></p>", "Status": "Active",
         "Created": "2024-03-01T09:00:00Z", "Modified": "2025-02-01T10:30:00Z",
         "Author": {"EMail": "jane@contoso.com"}, "Editor": {"EMail": "bob@contoso.com"},
         "Dept": {"Label": "Sales", "TermGuid": "T-SALES", "WssId": 1}},
        {"Id": 2, "Title": "Support", "Price": 100, "Category": "Software & Services",
         "Dept": {"Label": "Legacy", "TermGuid": "T-OLD", "WssId": 2}},
    ],
    "lists/" + ORDERS + ".items.json": [
        {"Id": 1, "Title": "PO-1", "Product": {"Id": 1, "Title": "Widget"}, "Owner": {"EMail": "jane@contoso.com"},
         "Qty": 3.0, "Tags": ["Urgent", "Gift"], "Shipped": True},
        {"Id": 2, "Title": "PO-2", "Product": {"Id": 2, "Title": "Support"}, "Owner": None, "Qty": 1.0,
         "Tags": [], "Shipped": False, "HasUniqueRoleAssignments": True},
    ],
    "termsets.json": {TERMSET: {
        "id": TERMSET, "localizedNames": [{"name": "Departments", "languageTag": "en-US"}],
        "terms": [{"id": "T-SALES", "labels": [{"name": "Sales", "isDefault": True}],
                   "children": [{"id": "T-EMEA", "labels": [{"name": "EMEA", "isDefault": True}], "children": []}]}]}},
    "libraries.json": [
        {"list": {"Id": POLICIES, "Title": "Policies", "BaseTemplate": 101, "ItemCount": 4, "EnableVersioning": True,
                  "RootFolder": {"ServerRelativeUrl": "/sites/marketing/Policies"}},
         "fields": [
             _field("h1", "Title", "Title", "Text"),
             _field("h2", "Audience", "Audience", "Choice", Choices=["HR", "IT"]),
             _field("h3", "Expiry", "Expiry", "DateTime", DisplayFormat=0),
             _field("h4", "Region", "Region", "TaxonomyFieldTypeMulti", TermSetId=TERMSET),
         ], "itemCount": 4, "fileCount": 3, "folderCount": 1, "bytes": 10},
    ],
    "libraries/" + POLICIES + ".items.json": [
        {"Id": 10, "FSObjType": 1, "FileLeafRef": "HR", "FileRef": "/sites/marketing/Policies/HR",
         "FileDirRef": "/sites/marketing/Policies", "HasUniqueRoleAssignments": True},
        {"Id": 11, "FSObjType": 0, "FileLeafRef": "handbook.pdf", "FileRef": "/sites/marketing/Policies/HR/handbook.pdf",
         "FileDirRef": "/sites/marketing/Policies/HR", "Title": "Employee Handbook", "Audience": "HR",
         "Expiry": "2027-01-01T00:00:00Z", "Created": "2023-05-01T08:00:00Z", "Modified": "2024-06-01T08:00:00Z",
         "Author": {"EMail": "jane@contoso.com"}, "Editor": {"EMail": "jane@contoso.com"},
         "Region": [{"Label": "EMEA", "TermGuid": "T-EMEA"}], "_localPath": "{ROOT}/files/handbook.pdf"},
        {"Id": 12, "FSObjType": 0, "FileLeafRef": "readme.txt", "FileRef": "/sites/marketing/Policies/readme.txt",
         "FileDirRef": "/sites/marketing/Policies", "Author": {"EMail": "bob@contoso.com"},
         "_localPath": "{ROOT}/files/readme.txt"},
        {"Id": 13, "FSObjType": 0, "FileLeafRef": "video.mp4", "FileRef": "/sites/marketing/Policies/video.mp4",
         "FileDirRef": "/sites/marketing/Policies", "_skipped": "larger than 250 MB"},
    ],
    "pages.json": [
        {"id": "p-home", "name": "Home.aspx", "title": "Home", "webUrl": "https://contoso.sharepoint.com/sites/marketing/SitePages/Home.aspx",
         "_images": {},
         "canvasLayout": {"horizontalSections": [
             {"layout": "twoColumns", "columns": [
                 {"width": 6, "webparts": [{"@odata.type": "#microsoft.graph.textWebPart",
                                            "innerHtml": '<p>See <a href="/sites/marketing/SitePages/About-Us.aspx">about</a></p>'}]},
                 {"width": 6, "webparts": [{"@odata.type": "#microsoft.graph.standardWebPart",
                                            "webPartType": "c70391ea-0b10-4ee9-b2b4-006d3fcad0cd",
                                            "data": {"title": "Quick links"}}]},
             ]}]}},
        {"id": "p-about", "name": "About-Us.aspx", "title": "About Us", "description": "Who we are",
         "_images": {"/sites/marketing/SiteAssets/team.jpg": "out/extract/files/team.jpg"},
         "canvasLayout": {"horizontalSections": [
             {"layout": "oneColumn", "columns": [{"webparts": [
                 {"@odata.type": "#microsoft.graph.standardWebPart",
                  "webPartType": "d1d91016-032f-456d-98a4-721247c305e8",
                  "data": {"title": "Image", "properties": {"altText": "Team"},
                           "serverProcessedContent": {"imageSources": [{"key": "imageSource", "value": "/sites/marketing/SiteAssets/team.jpg"}]}}},
             ]}]}]}},
        {"id": "p-news", "name": "Q3-Results.aspx", "title": "Q3 results are in", "promotionKind": "newsPost",
         "description": "Record quarter", "_images": {},
         "webUrl": "https://contoso.sharepoint.com/sites/marketing/SitePages/Q3-Results.aspx",
         "_listItem": {"Id": 7, "FirstPublishedDate": "2026-09-30T07:00:00Z", "Created": "2026-09-29T07:00:00Z",
                       "Author": {"EMail": "jane@contoso.com"}},
         "canvasLayout": {"horizontalSections": [{"layout": "oneColumn", "columns": [{"webparts": [
             {"@odata.type": "#microsoft.graph.textWebPart", "innerHtml": "<p>Revenue up 20%</p>"}]}]}]}},
    ],
}


@pytest.fixture
def extract_dir(tmp_path):
    root = tmp_path / "extract"
    for name, data in EXTRACT.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data).replace("{ROOT}", str(root)))
    (root / "files").mkdir()
    (root / "files" / "handbook.pdf").write_bytes(b"%PDF-1.7 handbook")
    (root / "files" / "readme.txt").write_text("read me")
    return tmp_path
