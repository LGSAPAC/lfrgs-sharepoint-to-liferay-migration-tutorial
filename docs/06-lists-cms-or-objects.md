# 6. Lists: CMS structures or Objects

SharePoint uses one feature, the **list**, for two very different jobs:

* **Publishing content** that people read: a product catalog, FAQs, policies, a staff directory, news.
* **Running a process** that people work through: support requests, orders, approvals, registrations.

Liferay 2026.Q3 has a purpose-built home for each, and both are built on Liferay Objects:

| | **Scenario A: CMS content structure** | **Scenario B: Liferay Object** |
|---|---|---|
| Built on | Object definition, depot scope, in the `L_CMS_CONTENT_STRUCTURES` folder | Object definition, site (or company) scope |
| Lives in | A CMS **Space**, reused by every connected site | The **site** |
| Edited in | CMS → Spaces (folders, preview, translation) | Site Menu → Content & Data → *Object* (generated admin UI), Liferay Forms, custom apps |
| Strengths | Versioning, friendly URLs, translation, per-Space workflow, display page templates, reuse across sites | Object **views**, **layouts**, **validations**, **actions** (notifications, webhooks, Groovy), **state fields**, Forms, row-level permissions, REST/GraphQL for apps |
| Replaces in SharePoint | Lists shown with list/highlighted-content web parts | Lists behind Power Automate flows, Power Apps forms, approval workflows |

## 6.1 How `sp2lr` chooses

`mapping.yaml` → `lists.target` takes `auto` (default), `cms` or `objects`, and you can override it
per list. With `auto`, every list is scored on signals from the extract, and the reasons are printed
in `plan.md`:

| Signal | Points to |
|---|---|
| List name like *request, ticket, order, approval, registration, booking, incident, tracker…* | Objects (+2) |
| Choice column named *Status / State / Stage / Priority / Severity* | Objects (+2) |
| Person column named *Assigned to / Owner / Requester / Approver…* | Objects (+2) |
| Items with unique permissions (row-level security) | Objects (+2) |
| Column validation formulas, due-date columns, more than 5,000 items | Objects (+1 each) |
| List name like *news, FAQ, product, catalog, policy, article, directory, event…* | CMS (+2) |
| Rich-text columns | CMS (+2) |
| No strong signal | CMS (default) |

```
## Lists
| SharePoint list  | Target                        | Decided by | Why                                                        |
|------------------|-------------------------------|------------|------------------------------------------------------------|
| Product Catalog  | CMS content structure (Space) | auto       | list name suggests published content; 'Description' is rich text |
| Orders           | Liferay Object (site)         | auto       | list name suggests a business process; 'Order Status' is a status/priority column; 'Owner' assigns work to people; row-level (item) permissions |
```

The score is a starting point, not a verdict. Confirm each list with its owner and pin the decision:

```yaml
lists:
  target: auto
  overrides:
    "Orders":          { target: objects }
    "Product Catalog": { target: cms, name: Product, pluralLabel: Products }
```

What's the same in both scenarios: column → field mapping ([Chapter 4](04-mapping-and-plan.md#42-field-mapping)),
picklists, lookups → relationships, attachments, idempotent ERCs, and entry-level permissions.

```bash
sp2lr load --phase structures --phase entries
```

---

## 6.2 Scenario A: Product Catalog → CMS content structure

**Use case:** the marketing team publishes products. The list is shown on several SharePoint pages,
editors care about wording and images, and the same products should appear on other sites later.

### Picklists

```json
PUT /o/headless-admin-list-type/v1.0/list-type-definitions/by-external-reference-code/SP_CHOICE_AAAAAAAA0000_CATEGORY
{ "name": "Category (aaaaaaaa)", "name_i18n": {"en_US": "Category"},
  "listTypeEntries": [ {"key": "hardware", "name_i18n": {"en_US": "Hardware"}},
                       {"key": "softwareServices", "name_i18n": {"en_US": "Software & Services"}} ] }
```

### The structure

```json
POST /o/object-admin/v1.0/object-definitions
{
  "externalReferenceCode": "SP_LIST_AAAAAAAA000000000000000000000001",
  "name": "ProductCatalog", "label": {"en_US": "Product Catalog"}, "pluralLabel": {"en_US": "Product Catalog"},
  "scope": "depot",
  "objectFolderExternalReferenceCode": "L_CMS_CONTENT_STRUCTURES",
  "titleObjectFieldName": "title",
  "objectFields": [
    {"name": "title", "businessType": "Text", "DBType": "String", "required": true},
    {"name": "price", "businessType": "PrecisionDecimal", "DBType": "BigDecimal"},
    {"name": "category", "businessType": "Picklist", "DBType": "String",
     "listTypeDefinitionExternalReferenceCode": "SP_CHOICE_AAAAAAAA0000_CATEGORY"},
    {"name": "launchDate", "businessType": "Date", "DBType": "Date"},
    {"name": "description", "businessType": "RichText", "DBType": "Clob"},
    {"name": "attachment1", "businessType": "Attachment", "DBType": "Long",
     "objectFieldSettings": [{"name": "fileSource", "value": "documentsAndMedia"}]}
  ]
}
POST /o/object-admin/v1.0/object-definitions/{id}/publish
```

The CMS only accepts a custom object definition as a content structure when it has **depot scope**
and sits in a CMS object folder (`L_CMS_CONTENT_STRUCTURES`, `L_CMS_FILE_TYPES` or
`L_CMS_STRUCTURE_REPEATABLE_GROUPS`). Once published, it appears under *CMS → Admin → Content
Structures*.

### Entries go into the Space

```json
PUT /o/c/productcatalogs/scopes/{spaceId}/by-external-reference-code/sp-item-aaaaaaaa-…-1
{ "title": "Widget", "price": 9.5, "category": {"key": "hardware"}, "launchDate": "2026-01-15",
  "description": "<p>Best <b>widget</b></p>", "attachment1": {"name": "spec.pdf", "fileBase64": "JVBERi0x…"} }
```

### Finishing Scenario A in Liferay

1. **Permissions:** custom structures have **View disabled by default**. Go to *CMS → Admin → Content
   Structures → ProductCatalog → ⋮ → Permissions* and check View for *Asset Library Member* (and for
   *Guest* if it's public).
2. **Space Availability:** limit the structure to the Marketing Content Space if other teams shouldn't
   use it.
3. **Approval:** if the list used *content approval*, set a workflow on the structure or override it
   on the Space. `plan.md` lists these lists.
4. **Display:** on the site, add a **Collection Display** fragment over the structure to replace the
   SharePoint list web part. Create a **display page template** for the product detail page.
5. **Views:** SharePoint views don't carry over. Recreate the important ones as collections with
   filters.

---

## 6.3 Scenario B: Orders → Liferay Object

**Use case:** the sales team tracks orders. Each order has an owner, a status and a quantity, some
orders are restricted to the Owners group, and a Power Automate flow e-mails the owner when the
status changes.

### The Object

Same payload shape, but site-scoped, with no CMS folder:

```json
POST /o/object-admin/v1.0/object-definitions
{
  "externalReferenceCode": "SP_LIST_AAAAAAAA000000000000000000000002",
  "name": "Orders", "label": {"en_US": "Orders"}, "pluralLabel": {"en_US": "Orders"},
  "scope": "site",
  "panelCategoryKey": "site_administration.content",
  "titleObjectFieldName": "title",
  "objectFields": [
    {"name": "title", "businessType": "Text", "DBType": "String"},
    {"name": "owner", "businessType": "Text", "DBType": "String"},
    {"name": "quantity", "businessType": "LongInteger", "DBType": "Long"},
    {"name": "tags", "businessType": "MultiselectPicklist", "DBType": "String", "listTypeDefinitionExternalReferenceCode": "SP_CHOICE_…_TAGS"},
    {"name": "shipped", "businessType": "Boolean", "DBType": "Boolean"},
    {"name": "orderStatus", "businessType": "Picklist", "DBType": "String", "listTypeDefinitionExternalReferenceCode": "SP_CHOICE_…_ORDERSTATUS",
     "objectFieldSettings": [{"name": "defaultValueType", "value": "inputAsValue"}, {"name": "defaultValue", "value": "new"}]}
  ]
}
```

On the Objects path, the planner also carries over column settings:

| SharePoint column setting | Liferay object field |
|---|---|
| *Require that this column contains information* | `required: true` |
| *Enforce unique values* | `uniqueValues` setting |
| *Default value* (not `[today]` / `[me]`) | `defaultValueType` + `defaultValue` settings |

### Views: SharePoint list views → object views

Every public SharePoint view becomes an **object view**, with the same columns in the same order and the
same sort order. The default view stays the default:

```json
POST /o/object-admin/v1.0/object-definitions/{id}/object-views
{ "name": {"en_US": "All Items"}, "defaultObjectView": true,
  "objectViewColumns": [ {"objectFieldName": "title", "priority": 0},
                         {"objectFieldName": "quantity", "priority": 1},
                         {"objectFieldName": "orderStatus", "priority": 2} ],
  "objectViewSortColumns": [ {"objectFieldName": "dateCreated", "priority": 0, "sortOrder": "desc"} ],
  "objectViewFilterColumns": [] }
```

`Created`, `Modified`, `Author` and `ID` map to the system fields `dateCreated`, `dateModified`,
`creator` and `id`. CAML `<Where>` filters are listed in `plan.md` as manual follow-ups, because they
rarely translate one-to-one. Rebuild them in *Objects → Orders → Views → Filters*.

### Entries go into the site

```json
PUT /o/c/orders/scopes/{siteId}/by-external-reference-code/sp-item-aaaaaaaa-…-1
{ "title": "PO-1", "owner": "jane@contoso.com", "quantity": 3,
  "tags": [{"key": "urgent"}, {"key": "gift"}], "shipped": true }
```

Row-level permissions work as in [Chapter 8](08-permissions.md), but with **site** roles
(`SP Site Principal - <group>`) because the entries live in the site, not in a Space.

### Finishing Scenario B in Liferay: rebuilding the app behaviour

This is where an Object is worth more than a content structure. Use `plan.md`'s manual list as the
backlog:

| SharePoint / Power Platform | Liferay Object feature | Where |
|---|---|---|
| List form / Power Apps form | **Layout** (tabs, field groups) or a **Liferay Form** bound to the object | Objects → Orders → Layouts |
| Column / list validation formula | **Validation** (Expression Builder or Groovy) | Objects → Orders → Validations |
| Power Automate "when an item is created/modified → send e-mail" | **Action** with trigger *On After Add/Update*, type *Notification* | Objects → Orders → Actions |
| Power Automate "→ call an API" | **Action** type *Webhook* | Objects → Orders → Actions |
| Status choice with allowed transitions | Picklist field + **State Manager** | Objects → Orders → State Manager |
| Content approval | **Workflow** (Single Approver or a custom Kaleo process) | Process Builder → Configuration → Orders |
| Calculated column | **Formula** field (or **Aggregation** across a relationship) | Objects → Orders → Fields |
| List web part on a page | **Collection Display** fragment or the object's **widget** | Site Builder |
| "My items" view | Object view filtered on *Creator* = current user | Objects → Orders → Views |

> Agent prompt (Liferay MCP server): *"List the fields, views, validations and actions of the Orders
> object. Compare with the SharePoint list 'Orders' (columns, views, validation formulas from the
> sharepoint MCP server) and tell me what's still missing from the table in docs/06."*

---

## 6.4 Relationships across scenarios

Lookups are migrated whichever target each side has, so an Orders Object can point to a ProductCatalog
CMS structure:

| SharePoint | Liferay relationship | Created on |
|---|---|---|
| `Orders.Product` → `Product Catalog` (single) | `oneToMany`, ProductCatalog (1) → Orders (many) | the target definition |
| `Orders.Tags` → `Tags` (multi) | `manyToMany` | the source definition |

```
PATCH /o/c/orders/{orderId}              {"r_product_c_productCatalogId": <productEntryId>}
PUT   /o/c/orders/{orderId}/tags/{tagEntryId}
```

> Cross-scope relationships (site Object → depot structure) are listed in the validation checklist
> (L17). If your build rejects them, set both lists to the same target.

## 6.5 What neither scenario migrates

* `Created` / `Modified` / `Author` / `Editor` values. Entries are created by the migration user.
  Keep them by adding explicit fields such as `spCreated` and `spAuthor` with a `mapping.yaml` override.
* Version history. In the CMS, versioning starts at migration time. Objects don't version entries.
* Managed metadata columns. Map them to Liferay **categories** by hand.
* Document libraries. Use Documents and Media; see the Scope section of the README.

Next: [7. Pages](07-pages.md)
