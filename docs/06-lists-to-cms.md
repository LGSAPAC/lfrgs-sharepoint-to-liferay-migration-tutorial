# 6. Lists to CMS structures

```bash
sp2lr load --phase structures --phase entries
```

## 6.1 Picklists

Each `Choice` / `MultiChoice` column becomes a list type definition:

```json
PUT /o/headless-admin-list-type/v1.0/list-type-definitions/by-external-reference-code/SP_CHOICE_AAAAAAAA0000_CATEGORY
{ "externalReferenceCode": "SP_CHOICE_AAAAAAAA0000_CATEGORY",
  "name": "Category (aaaaaaaa)", "name_i18n": {"en_US": "Category"},
  "listTypeEntries": [
    {"key": "hardware", "name": "Hardware", "name_i18n": {"en_US": "Hardware"}},
    {"key": "softwareServices", "name": "Software & Services", "name_i18n": {"en_US": "Software & Services"}} ] }
```

## 6.2 Content structures

Each custom list becomes a **depot-scoped** object definition in the CMS content-structures folder,
then gets published:

```json
POST /o/object-admin/v1.0/object-definitions
{
  "externalReferenceCode": "SP_LIST_AAAAAAAA000000000000000000000001",
  "name": "ProductCatalog",
  "label": {"en_US": "Product Catalog"}, "pluralLabel": {"en_US": "Product Catalog"},
  "scope": "depot",
  "objectFolderExternalReferenceCode": "L_CMS_CONTENT_STRUCTURES",
  "titleObjectFieldName": "title",
  "objectFields": [
    {"name": "title", "businessType": "Text", "DBType": "String", "label": {"en_US": "Title"}, "required": true},
    {"name": "price", "businessType": "PrecisionDecimal", "DBType": "BigDecimal", "label": {"en_US": "Price"}},
    {"name": "category", "businessType": "Picklist", "DBType": "String",
     "listTypeDefinitionExternalReferenceCode": "SP_CHOICE_AAAAAAAA0000_CATEGORY"},
    {"name": "launchDate", "businessType": "Date", "DBType": "Date"},
    {"name": "description", "businessType": "RichText", "DBType": "Clob"},
    {"name": "attachment1", "businessType": "Attachment", "DBType": "Long",
     "objectFieldSettings": [{"name": "fileSource", "value": "documentsAndMedia"}, …]}
  ]
}
POST /o/object-admin/v1.0/object-definitions/{id}/publish
```

Why depot scope and that folder? In 2026.Q3 the CMS accepts a custom object definition as a content
structure only if it has **depot scope** and lives in one of the CMS object folders
(`L_CMS_CONTENT_STRUCTURES`, `L_CMS_FILE_TYPES`, `L_CMS_STRUCTURE_REPEATABLE_GROUPS`). After
publishing, the structure appears under *CMS → Admin → Content Structures*, where you can adjust its
**Space Availability** if needed.

On a re-run, the loader finds the definition by ERC and **adds missing fields only**. It never drops a
field that holds data.

**Manual step (permissions):** custom structures have **VIEW disabled by default**, so space members
won't see migrated entries until you grant it. Go to *CMS → Admin → Content Structures → ⋮ →
Permissions* and check **View** for *Asset Library Member* (and for *Site Member* /
*Guest* if the content is public). [Chapter 8](08-permissions.md) covers the rest.

## 6.3 Relationships (lookups)

Created after every definition exists:

| SharePoint | Liferay relationship | Created on |
|---|---|---|
| `Orders.Product` → `Product Catalog` (single) | `oneToMany`, ProductCatalog (1) → Orders (many) | the target (ProductCatalog) |
| `Orders.Tags` → `Tags` (multi) | `manyToMany`, Orders ↔ Tags | the source (Orders) |

## 6.4 Entries

Entries are upserted into the Space by ERC:

```json
PUT /o/c/productcatalogs/scopes/{spaceId}/by-external-reference-code/sp-item-aaaaaaaa-…-1
{ "externalReferenceCode": "sp-item-aaaaaaaa-…-1", "title": "Widget", "price": 9.5,
  "category": {"key": "hardware"}, "launchDate": "2026-01-15",
  "description": "<p>Best <b>widget</b></p>",
  "attachment1": {"name": "spec.pdf", "fileBase64": "JVBERi0x…"} }
```

The loader takes the REST path (`/o/c/productcatalogs`) from the published definition's
`restContextPath`, not from guesswork.

Pass 2 links lookups using the SharePoint-ID → Liferay-ID map saved in `out/state.json`:

```
PATCH /o/c/orders/{orderId}            {"r_product_c_productCatalogId": <productEntryId>}   # one-to-many
PUT   /o/c/orders/{orderId}/tags/{tagEntryId}                                           # many-to-many
```

> **Author and dates:** object entries are created as the migration user with the current date.
> `Created`, `Modified`, `Author` and `Editor` aren't migrated as system fields. If you need them,
> add explicit fields such as `spCreated` and `spAuthor` with a `mapping.yaml` override, or keep them
> in the extract as an audit trail.

## 6.5 Check

> With the liferay MCP server, count the entries of each migrated structure in the space
> "Marketing Content" and fetch 3 random entries of Product Catalog (fields: title, price, category).
> With the sharepoint MCP server, fetch the same items by Title from the Product Catalog list and
> compare field by field.

Next: [7. Pages](07-pages.md)
