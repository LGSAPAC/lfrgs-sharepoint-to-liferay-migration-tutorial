# 13. SharePoint Server (on-premises)

The same tooling migrates from **SharePoint Server**: Subscription Edition (SE), 2019 and 2016.
SharePoint 2016 and 2019 left extended support on July 14, 2026, so they're the farms most likely
to be moving now. Set
`SP_PLATFORM=server` and every other chapter applies unchanged: the extract is written in the same
shape as for SharePoint Online, so `plan`, `load` and `verify` don't know the difference.

> **Status.** Developed and tested against the [mock SharePoint Server](14-mock-sharepoint.md) in
> SE, 2019 and 2016 modes, with real NTLM sign-in. It has **not** run against a real farm yet: the
> response shapes come from Microsoft's documentation. Items S-SE1 to S-SE10 in the
> [validation checklist](VALIDATION-CHECKLIST.md) track what to confirm on a real farm.

## 13.1 Supported versions

| Version | Modern pages | Term store read through | Classic pages |
|---|---|---|---|
| Subscription Edition | ✅ `CanvasContent1` | `/_api/v2.1/termStore` | ✅ |
| 2019 | ✅ `CanvasContent1` | client object model (`ProcessQuery`) | ✅ |
| 2016 | none in the product: all pages are wiki or publishing pages | client object model | ✅ |
| 2013 and older | not tested | | |

The client works out what the farm has instead of asking for a version: it builds every `$select`
from the list's real fields, tries the v2.1 term store first and falls back to the client object
model on `400`/`404`.

## 13.2 Configuration

```bash
SP_PLATFORM=server
SP_SITE_URL=https://sharepoint.contoso.local/sites/marketing
SP_USERNAME=CONTOSO\migration    # a site collection administrator
SP_PASSWORD=…
# SP_AUTH_MODE defaults to ntlm on SharePoint Server. SP_TENANT_ID / SP_CLIENT_ID aren't needed.
```

Sign-in is **NTLM** (`httpx-ntlm`). The account must be a **site collection administrator**, for the
same reason as on Online: reading role assignments needs *Enumerate Permissions*.

Not covered yet: Kerberos-only farms (NTLM disabled) and ADFS/SAML (trusted identity provider)
sign-in. Both would need another `SP_AUTH_MODE`.

## 13.3 What reads differently

SharePoint Server has no Microsoft Graph, so the client reads these through REST instead and
returns them in the Graph shape:

| Data | SharePoint Online | SharePoint Server |
|---|---|---|
| Site | Graph `/sites/{host}:{path}` | `/_api/site` + `/_api/web` (same `host,siteId,webId` id, so ERCs match) |
| Modern pages | Graph `sitePage` + `canvasLayout` | Site Pages items, **`CanvasContent1`** converted to `canvasLayout` (`transform.canvas_to_layout`) |
| Classic pages | REST (`WikiField`, `PublishingPageContent`) | the same REST ([Chapter 9](09-pages-and-news.md#96-classic-pages)) |
| News posts | `promotionKind: newsPost` | `PromotedState = 2` |
| Users | Entra ID profile | Site user + `SP.UserProfiles.PeopleManager` (Active Directory) |
| User key (ERC) | `entra-<objectId>` | `ad-<domain>-<user>` |
| Login names | `i:0#.f\|membership\|jane@contoso.com` | `i:0#.w\|contoso\jane` |
| Security groups | Entra group, members expanded through Graph | AD group `c:0+.w\|<SID>`, **members not readable** (13.4) |
| Managed metadata | Graph `termStore` | `/_api/v2.1/termStore` (SE), else the client object model (2019, 2016) |
| Lists, libraries, items, views, fields, files, permissions | SharePoint REST | the same SharePoint REST |

`CanvasContent1` stores each control with `position.zoneIndex` (section), `sectionIndex` (column),
`controlIndex` (order) and `sectionFactor` (width out of 12, `0` = full width). Text controls
(`controlType: 4`) become text web parts; web parts (`controlType: 3`) keep their `webPartId`,
properties and image sources. The resulting Liferay page definition is identical to the one built from
Graph for the same page, which the tests check.

## 13.4 Limits and what to do about them

* **AD group members.** SharePoint REST shows that an AD group has access, not who is in it. The
  group becomes an empty user group and `plan.md` warns about it. Fill it with Liferay's **LDAP
  import** (*Instance Settings → Security → LDAP*), which can import AD groups as user groups, or add
  members by hand. SharePoint groups that list users directly migrate with their members.
* **Disabled accounts.** REST doesn't say whether an AD account is disabled, so `users.skipDisabled`
  can't apply. List leavers in `users.exclude` in `mapping.yaml`.
* **User Profile Service.** Given name, surname, job title and department come from
  `PeopleManager`. Without the User Profile Service the client splits the display name instead.
* **Term store.** On 2019 and 2016 the term set is read with the client object model:
  `POST /_api/contextinfo` for a form digest, then `POST /_vti_bin/client.svc/ProcessQuery` with
  `TaxonomySession → GetDefaultSiteCollectionTermStore → GetTermSet → GetAllTerms`. The hierarchy
  comes from each term's `PathOfTerm` (`Sales;EMEA`). These are the only POSTs the tool sends to
  SharePoint, and they only read. If that fails too, the loader builds the vocabulary from the term
  labels seen on items, without the hierarchy.
* **News on 2016.** There are no news posts before 2019; pages promoted as news on later versions
  are ordinary pages there.
* **Site search.** The MCP server's `search_sites` returns only the configured site.
* **Everything else**, including the 2026.Q3 Liferay limits in the earlier chapters, is the same as for
  Online.
