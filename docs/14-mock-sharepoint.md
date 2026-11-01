# 14. Mock SharePoint (Online and Server) in Docker

There's no SharePoint you can run in a container: SharePoint Online is a Microsoft 365 service, and
SharePoint Server needs Windows Server, SQL Server and Active Directory. To develop and regression-test
the tooling without a tenant or a farm, this repo ships a **mock** that answers the Graph and
SharePoint REST calls `sp2lr` makes, for the test site in the [validation
checklist](VALIDATION-CHECKLIST.md#end-to-end-scenario).

> **What it proves, and what it doesn't.** The mock runs the whole pipeline (sign-in, paging,
> downloads, extract, plan, load into a real Liferay, verify) for both platforms. Its responses are
> written from Microsoft's documentation, not captured from SharePoint, so it can't prove that real
> SharePoint answers the same way. The S and S-SE checklist items still need a real tenant or farm.

## 14.1 Run it

```bash
docker compose -f docker/docker-compose.sharepoint-mock.yml up -d --build
curl localhost:8091/_health    # {"mode":"online", …}
curl localhost:8092/_health    # {"mode":"server", …}   (8093: server2019, 8094: server2016)
```

| Service | Site URL | Plays | Sign-in |
|---|---|---|---|
| `sharepoint-online` | `http://localhost:8091/sites/marketing` | SharePoint Online (Graph at `/v1.0` + REST) | bearer token `sp2lr-mock-token` |
| `sharepoint-server` | `http://localhost:8092/sites/marketing` | SharePoint Server SE (REST only) | **real NTLM**, `CONTOSO\spadmin` / `Passw0rd!` |
| `sharepoint-server2019` | `http://localhost:8093/sites/marketing` | SharePoint 2019: no v2.1 term store, client object model instead | real NTLM |
| `sharepoint-server2016` | `http://localhost:8094/sites/marketing` | SharePoint 2016: also no modern pages, so every page is a wiki page | real NTLM |

The server modes check NTLM credentials for real (with `pyspnego`), so a wrong password gets `401`,
as IIS would answer. They also answer `400` to a `$select` naming a column the list doesn't have,
and require a form digest for the client object model, as SharePoint does.

## 14.2 Migrate from it

```bash
# SharePoint Online (mock)
export SP_PLATFORM=online SP_AUTH_MODE=none \
       SP_SITE_URL=http://localhost:8091/sites/marketing SP_GRAPH_URL=http://localhost:8091/v1.0
# …or SharePoint Server (mock)
export SP_PLATFORM=server SP_USERNAME='CONTOSO\spadmin' SP_PASSWORD='Passw0rd!' \
       SP_SITE_URL=http://localhost:8092/sites/marketing

sp2lr extract && sp2lr plan && sp2lr load --dry-run && sp2lr load && sp2lr verify
```

`SP_AUTH_MODE=none` skips Entra ID sign-in. Only the mock accepts that. Both runs migrate the
same site, so use a fresh Liferay (or delete the *Marketing* site and Space) between them.

Results against Liferay DXP 2026.q3.9, for Online, SE, 2019 and 2016: every run loads with
**0 errors**, `verify` matches every check (11/11; 10/10 on 2016, which has no news), a second
`load` is a no-op, and Liferay logs no indexing errors.

## 14.3 What it contains

[`mock/sharepoint_mock/scenario.py`](../mock/sharepoint_mock/scenario.py) builds the site for either
platform:

* Lists *Product Catalog* (CMS) and *Orders* (Objects), with a lookup whose list GUID is in another
  case, a person column, choices, a default value, two public views and a personal one
* A list item attachment, an item with unique permissions
* Library *Policies*: folder *HR* with unique permissions, a PDF, a text file and a 300 MB video that
  extract skips, Choice/Date/managed metadata columns
* Term set *Departments* (Sales → EMEA), plus a term only seen on items
* Pages *Home* (two columns, text + Quick links), *About Us* (image + text) and the news post
  *Q3 results are in* (wiki pages on 2016), a classic wiki page *Old wiki* (link + image) and a
  publishing page *Press release* in a *Pages* library
* Owners / Members / Visitors groups. On Online, Members is an Entra security group. On Server, it's
  an AD group (not expandable) plus Bob added directly.

Lists answer one item per page, so OData paging (`odata.nextLink`) is always exercised.

The automated tests (`tests/test_platforms.py`) start the same mock in all four modes and check that
every platform produces the same plan where it should, and differs only where the platforms do.
