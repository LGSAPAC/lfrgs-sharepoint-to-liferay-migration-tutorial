# 6. Managed metadata → categories

```bash
sp2lr load --phase taxonomy
```

Run this **before** structures, entries and documents. Those phases tag records with the category
IDs this phase creates.

## 6.1 Mapping

| SharePoint (term store) | Liferay |
|---|---|
| Term set | **Vocabulary** (ERC `sp-termset-<termSetId>`) |
| Term (with child terms) | **Category**, same hierarchy (ERC `sp-term-<termGuid>`) |
| Managed metadata column (`TaxonomyFieldType`, `TaxonomyFieldTypeMulti`) on a list or library | Not a field. Items get `taxonomyCategoryIds` |
| Term labels / default label | Category name (default label) |
| Deprecated or deleted term still tagged on items | Top-level category, so the tag isn't lost |

Liferay categories are the native equivalent of managed metadata. Search facets, Collection Display
filters, segmentation and Asset Publisher all work with them, so a tag doesn't become a plain
text field.

## 6.2 How the term store is read

During `sp2lr extract`, every managed-metadata column found on a migrated list or library has its
`TermSetId` resolved through Microsoft Graph:

```
GET /sites/{siteId}/termStore/sets/{setId}                      name (localizedNames)
GET /sites/{siteId}/termStore/sets/{setId}/children             top-level terms
GET /sites/{siteId}/termStore/sets/{setId}/terms/{id}/children  child terms (recursive)
```

This needs the delegated Graph permission **`TermStore.Read.All`** ([Chapter 1](01-prerequisites.md)).
If the term set can't be read (missing consent, or a site-collection term set the account can't see),
the extract carries on. The vocabulary is then built **flat** from the labels actually tagged on
items, and `plan.md` warns about it.

## 6.3 Where vocabularies live

```yaml
taxonomy:
  scope: global   # global | site
```

| Scope | Created in | Use when |
|---|---|---|
| `global` (default) | The Global site | Categories must apply to CMS Space entries, site Objects, documents **and** other sites. Global vocabularies are available everywhere |
| `site` | The migrated site | Everything migrated is site-scoped (Objects, Documents and Media) and the term set is local to this site |

## 6.4 API calls

```json
POST /o/headless-admin-taxonomy/v1.0/sites/{globalSiteId}/taxonomy-vocabularies
{ "externalReferenceCode": "sp-termset-cccccccc-…", "name": "Departments" }

POST /o/headless-admin-taxonomy/v1.0/taxonomy-vocabularies/{vocabularyId}/taxonomy-categories
{ "externalReferenceCode": "sp-term-t-sales", "name": "Sales" }

POST /o/headless-admin-taxonomy/v1.0/taxonomy-categories/{salesId}/taxonomy-categories
{ "externalReferenceCode": "sp-term-t-emea", "name": "EMEA" }
```

Categories are created parents first. Re-runs look each one up by ERC
(`…/taxonomy-categories/by-external-reference-code/{erc}`) and reuse it.

Tagging happens in the later phases:

```json
PUT /o/c/productcatalogs/scopes/{spaceId}/by-external-reference-code/sp-item-…   { …, "taxonomyCategoryIds": [41203] }
POST /o/headless-delivery/v1.0/document-folders/{id}/documents   document = { …, "taxonomyCategoryIds": [41207] }
```

Object definitions that receive categories are created with `"enableCategorization": true`.

## 6.5 Check

> With the sharepoint MCP server, call `get_term_set` for each term set in `out/plan.json`. With the
> liferay MCP server, list the categories of the matching vocabulary and compare the trees: same
> names, same parents. Then pick 3 tagged items and confirm the Liferay entries carry the same
> categories.

Next: [7. Lists: CMS structures or Objects](07-lists-cms-or-objects.md)
