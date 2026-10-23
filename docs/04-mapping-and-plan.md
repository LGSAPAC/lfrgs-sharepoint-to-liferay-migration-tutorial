# 4. Mapping and plan

## 4.1 Target architecture in 2026.Q3

In 2026.Q3 the **new CMS** is the standard way to manage content in Liferay. It's built on
**Liferay Objects**:

* A **Space** groups content for a team. It has its own members, roles and folders. Technically it's an
  asset library (depot) of type `Space`, managed with `/o/headless-asset-library/v1.0/asset-libraries`.
* A **content structure** is the schema of a content type. It's an object definition with
  **depot scope** in the `L_CMS_CONTENT_STRUCTURES` object folder. Its **Space Availability** setting
  controls which spaces can use it.
* An **asset** (entry) is an object entry inside a space, with versioning, friendly URLs, translation
  and workflow.
* A **Site** delivers pages. Sites reach Space content through the space's **connected sites**, and
  pages render it with fragments, Collection Display, and display page templates.

So a SharePoint site becomes **two** Liferay containers:

```
SharePoint site  ──►  Liferay Site  "Marketing"            pages, navigation, page images
                 └─►  CMS Space     "Marketing Content"    list content (structures + entries)
                         ▲ connected to the site
```

### CMS structure or classic Object?

Both are object definitions, and `sp2lr` decides per list. With `lists.target: auto` (the
default) it scores each list and explains why in `plan.md`. [Chapter 6](06-lists-cms-or-objects.md)
walks through both scenarios end to end.

| **CMS content structure** (`target: cms`) | **Liferay Object** (`target: objects`) |
|---|---|
| The list holds editorial content: products, FAQs, policies, news, people directory | The list runs a process: requests, orders, approvals, registrations, inventory |
| Content should be reused by several sites | Data is site-specific and edited through forms |
| Editors work in the CMS UI (spaces, folders, translation, preview, versioning) | You need object views, layouts, validations, actions, state managers or Liferay Forms |
| Lives in the Space | Lives in the site. SharePoint list views become object views |

So the target model is usually:

```
SharePoint site  ──►  Liferay Site  "Marketing"            pages, navigation, images, Objects for app lists
                 └─►  CMS Space     "Marketing Content"    content lists as structures + entries
```

## 4.2 Field mapping

| SharePoint `TypeAsString` | Liferay object field (`businessType` / `DBType`) | Value conversion |
|---|---|---|
| `Text` | `Text` / `String` | as-is |
| `Note` (plain) | `LongText` / `Clob` | as-is |
| `Note` (`RichText=true`) | `RichText` / `Clob` | HTML as-is |
| `Number` (`Decimals=0`) | `LongInteger` / `Long` | `int()` |
| `Number` | `Decimal` / `Double` | `float()` |
| `Currency` | `PrecisionDecimal` / `BigDecimal` | `float()` |
| `DateTime` (date only) | `Date` / `Date` | `YYYY-MM-DD` |
| `DateTime` | `DateTime` / `DateTime` (UTC) | ISO 8601 `Z` |
| `Boolean` | `Boolean` / `Boolean` | `bool()` |
| `Choice` | `Picklist` + list type definition | `{"key": "<camelCaseChoice>"}` |
| `MultiChoice` | `MultiselectPicklist` | `[{"key": …}, …]` |
| `URL` | `Text` | the URL (`Description` dropped) |
| `User` | `Text` | e-mail address |
| `UserMulti` | `LongText` | comma-separated e-mails |
| `Lookup` | one-to-many **relationship** | linked after all entries exist |
| `LookupMulti` | many-to-many **relationship** | linked after all entries exist |
| Attachments | `Attachment` fields `attachment1..N` (N = max attachments per item, capped at 10) | base64 upload into Documents and Media |
| `Calculated`, `TaxonomyFieldType`, `Location`, `Thumbnail`, `Image` | not migrated (warning) | rebuild by hand |

Naming rules:

* Definition name = PascalCase list title (`Product Catalog` → `ProductCatalog`). Override it with `name`.
* Field name = camelCase **display name**, with SharePoint `_x0020_` escapes decoded
  (`Launch_x0020_Date` → `launchDate`). Names that collide with Liferay system fields (`status`, `id`,
  `creator`…) get an `sp` prefix, so `Status` becomes `spStatus`.
* Every record gets an **External Reference Code (ERC)** derived from its SharePoint ID, which keeps
  loads idempotent:

  | Record | ERC |
  |---|---|
  | Structure | `SP_LIST_<LISTGUID>` |
  | Entry | `sp-item-<listGuid>-<itemId>` |
  | User | `entra-<entraObjectId>` |
  | User group | `sp-group-<siteKey>-<spGroupId>` |
  | Page | `sp-page-<graphPageId>` |

## 4.3 `mapping.yaml`

Every decision that isn't mechanical lives in `mapping.yaml`. See
[`mapping.example.yaml`](../mapping.example.yaml). The most common overrides are:

```yaml
lists:
  target: auto          # auto | cms | objects
  overrides:
    "Support Requests": { target: objects }
    "Product Catalog":  { name: Product, pluralLabel: Products, skipFields: [LegacySku] }
    "Old Archive":      { skip: true }
permissions:
  levels:
    Approve: { siteRole: Site Content Reviewer, spaceRole: Asset Library Content Reviewer }
```

## 4.4 Build and review the plan

```bash
sp2lr plan --mapping mapping.yaml
```

This writes `out/plan.json` (machine contract) and `out/plan.md` (for humans). Review `plan.md` with
the site owner. It has one table per list (column → field → type), the permission mapping, every page
and its unmigrated web parts, plus **Warnings** and **Manual follow-ups** checklists.

Have the agent review it too ([`prompts/02-plan-review.md`](../prompts/02-plan-review.md)). With both
MCP servers connected, it can compare the plan against the live source (sample items through
`get_list_items`) and the target (existing object definitions through the Liferay MCP server), and
catch problems such as a picklist value that appears in items but isn't among the column's choices.

Next: [5. Users and groups](05-users-and-groups.md)
