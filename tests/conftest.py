"""A small synthetic SharePoint extract, shaped like the real Graph / REST responses."""

import json

import pytest

SITE_GRAPH_ID = "contoso.sharepoint.com,11111111-aaaa-bbbb-cccc-000000000001,22222222-aaaa-bbbb-cccc-000000000002"
PRODUCTS = "aaaaaaaa-0000-0000-0000-000000000001"
ORDERS = "aaaaaaaa-0000-0000-0000-000000000002"


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
             "RoleDefinitionBindings": [{"Name": "Full Control"}]}]}},
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
         ], "itemCount": 2},
        {"list": {"Id": ORDERS, "Title": "Orders", "BaseTemplate": 100, "ItemCount": 2},
         "fields": [
             _field("g1", "Title", "Title", "Text"),
             _field("g2", "Product", "Product", "Lookup", LookupList="{" + PRODUCTS + "}", LookupField="Title"),
             _field("g3", "Owner", "Owner", "User"),
             _field("g4", "Qty", "Quantity", "Number", Decimals=0),
             _field("g5", "Tags", "Tags", "MultiChoice", Choices=["Urgent", "Gift"]),
             _field("g6", "Shipped", "Shipped", "Boolean"),
         ], "itemCount": 2},
    ],
    "lists/" + PRODUCTS + ".items.json": [
        {"Id": 1, "Title": "Widget", "Price": 9.5, "Category": "Hardware", "Launch_x0020_Date": "2026-01-15T08:00:00Z",
         "Description": "<p>Best <b>widget</b></p>", "Status": "Active"},
        {"Id": 2, "Title": "Support", "Price": 100, "Category": "Software & Services"},
    ],
    "lists/" + ORDERS + ".items.json": [
        {"Id": 1, "Title": "PO-1", "Product": {"Id": 1, "Title": "Widget"}, "Owner": {"EMail": "jane@contoso.com"},
         "Qty": 3.0, "Tags": ["Urgent", "Gift"], "Shipped": True},
        {"Id": 2, "Title": "PO-2", "Product": {"Id": 2, "Title": "Support"}, "Owner": None, "Qty": 1.0,
         "Tags": [], "Shipped": False, "HasUniqueRoleAssignments": True},
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
    ],
}


@pytest.fixture
def extract_dir(tmp_path):
    root = tmp_path / "extract"
    for name, data in EXTRACT.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
    return tmp_path
