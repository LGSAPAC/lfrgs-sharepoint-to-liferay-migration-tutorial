# 8. Document libraries → Documents and Media

```bash
sp2lr load --phase documents
# item-level permissions are applied later by:  sp2lr load --phase permissions
```

## 8.1 Mapping

| SharePoint | Liferay Documents and Media (in the migrated site) |
|---|---|
| Document library | Root **folder** named after the library (ERC `sp-folder-<libraryId>-root`) |
| Folder | **Folder**, same tree (ERC `sp-folder-<libraryId>-<itemId>`) |
| File (current version) | **Document** (ERC `sp-doc-<libraryId>-<itemId>`), title = SharePoint Title or file name |
| Metadata columns (text, number, date, choice, yes/no…) | A **Document Type** per library (`SP <Library>`), with one field per column |
| Managed metadata columns | **Categories** ([Chapter 6](06-managed-metadata.md)) |
| Created / Modified / Created By / Modified By | Document type fields `spCreated`, `spModified`, `spCreatedBy`, `spModifiedBy` (plus optional impersonation, see [Chapter 4](04-mapping-and-plan.md#45-created-modified-and-author)) |
| Unique permissions on the library, a folder or a file | Explicit permissions on **every affected folder and document** ([Chapter 10](10-permissions.md#104-documents-and-folders)) |
| Content approval | Manual: enable a Documents and Media workflow |
| Version history | Only the current version (warning in `plan.md`) |
| Lookup, person-multi and calculated columns | Not migrated (warning) |

Libraries that SharePoint creates for itself are skipped: *Site Assets, Style Library, Form
Templates, Preservation Hold Library, Site Pages*, plus catalogs and system lists. Images used on
pages are migrated with the pages ([Chapter 9](09-pages-and-news.md)).

## 8.2 Extract

`sp2lr extract` enumerates every library through SharePoint REST, folders and files in one pass:

```
/_api/web/lists(guid'…')/items?$select=Id,FileLeafRef,FileRef,FileDirRef,FSObjType,File/Length,
     Created,Modified,Author/EMail,Editor/EMail,HasUniqueRoleAssignments,<metadata columns>
     &$expand=File,Author,Editor&$top=500          (paged with odata.nextLink)
```

Files are **streamed** to `out/extract/files/libraries/<libraryId>/<server-relative path>`, so large
files don't need to fit in memory.

```bash
sp2lr extract --max-file-mb 500     # default 250; bigger files are skipped and reported
sp2lr extract --no-libraries        # pages, lists and users only
```

Skip whole libraries in `mapping.yaml`:

```yaml
libraries:
  skip: [Archive 2015]
  metadata: documentType   # documentType | description
```

## 8.3 Document types (metadata)

Each library with metadata columns gets one Document Type, created with the data-engine API:

```json
POST /o/data-engine/v2.0/sites/{siteId}/data-definitions/by-content-type/document-library
{ "name": {"en_US": "SP Policies"}, "dataDefinitionKey": "SP_DOCTYPE_BBBBBBBB…",
  "availableLanguageIds": ["en_US"], "defaultLanguageId": "en_US",
  "dataDefinitionFields": [
    {"name": "audience", "label": {"en_US": "Audience"}, "fieldType": "select",
     "customProperties": {"options": {"en_US": [{"label": "HR", "value": "hr"}, {"label": "IT", "value": "it"}]}}},
    {"name": "expiry", "label": {"en_US": "Expiry"}, "fieldType": "date"},
    {"name": "spCreated", "label": {"en_US": "Created (SharePoint)"}, "fieldType": "date_time"},
    {"name": "spCreatedBy", "label": {"en_US": "Created by (SharePoint)"}, "fieldType": "text"} ] }
```

| Column type | Document type field |
|---|---|
| Text, URL, Person | `text` |
| Multiple lines (plain / rich) | `text` (multiline) / `rich_text` |
| Number, Currency | `numeric` |
| Date / Date and time | `date` / `date_time` |
| Yes/No | `checkbox` |
| Choice / Multi-choice | `select` (single / multiple) |

Documents are then uploaded into their folder with the type and values:

```
POST /o/headless-delivery/v1.0/document-folders/{folderId}/documents      (multipart)
  file     = handbook.pdf
  document = {"externalReferenceCode": "sp-doc-bbbbbbbb-…-11", "title": "Employee Handbook",
              "taxonomyCategoryIds": [41207],
              "documentType": {"name": "SP Policies",
                               "contentFields": [{"name": "audience", "contentFieldValue": {"data": "hr"}},
                                                 {"name": "expiry", "contentFieldValue": {"data": "2027-01-01"}}, …]}}
```

**Never lose a file.** If the document type can't be created, or a document is rejected because of
its metadata, the loader uploads the file again **without** the type. It writes the metadata into the
description in a readable form (`Audience: hr` / `Expiry: 2027-01-01`) and records a manual
follow-up. Set `libraries.metadata: description` to use that mode from the start.

## 8.4 Idempotency and re-runs

Folder and document IDs are stored in `out/state.json` by ERC, so re-running `--phase documents`
only uploads what's missing: new files from a delta extract, or files that failed before. Changed
files aren't re-uploaded. To refresh a file, delete it in Liferay and re-run.

## 8.5 Check

> With the sharepoint MCP server, call `get_library_contents` on the Policies library. With the liferay
> MCP server, list the folders and documents under the "Policies" folder of site "marketing". Compare
> the folder trees, the file counts per folder, and for 3 files the metadata values and categories.

Next: [9. Pages and news](09-pages-and-news.md)
