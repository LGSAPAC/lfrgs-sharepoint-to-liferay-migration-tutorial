# 13. SharePoint Server (on-premises)

The same tooling migrates from **SharePoint Server Subscription Edition** (SE). Set
`SP_PLATFORM=server` and every other chapter applies unchanged: the extract is written in the same
shape as for SharePoint Online, so `plan`, `load` and `verify` don't know the difference.

> **Status.** Developed and tested against the [mock SharePoint Server](14-mock-sharepoint.md), with real
> NTLM sign-in. It has **not** run against a real SE farm yet: the response shapes come from
> Microsoft's documentation. Items S-SE1 to S-SE7 in the [validation checklist](VALIDATION-CHECKLIST.md)
> track what to confirm on a real farm.

## 13.1 Supported versions

| Version | Supported | Why |
|---|---|---|
| Subscription Edition | ✅ target | Modern pages, REST API, current |
| 2019 | Likely, untested | Modern pages exist; `/_api/v2.1/termStore` may not (the loader falls back, see 13.4) |
| 2016 and older | ❌ | No modern pages: classic wiki and publishing pages have no `CanvasContent1` and are skipped |

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
| News posts | `promotionKind: newsPost` | `PromotedState = 2` |
| Users | Entra ID profile | Site user + `SP.UserProfiles.PeopleManager` (Active Directory) |
| User key (ERC) | `entra-<objectId>` | `ad-<domain>-<user>` |
| Login names | `i:0#.f\|membership\|jane@contoso.com` | `i:0#.w\|contoso\jane` |
| Security groups | Entra group, members expanded through Graph | AD group `c:0+.w\|<SID>`, **members not readable** (13.4) |
| Managed metadata | Graph `termStore` | `/_api/v2.1/termStore` |
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
* **Term store.** If `/_api/v2.1/termStore` isn't available on your build, the loader builds the
  vocabulary from the term labels seen on items, without the hierarchy, as it does on Online when a
  term set can't be read.
* **Site search.** The MCP server's `search_sites` returns only the configured site.
* **Everything else**, including the 2026.Q3 Liferay limits in the earlier chapters, is the same as for
  Online.
