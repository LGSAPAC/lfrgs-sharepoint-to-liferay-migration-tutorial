# 5. Users and groups

Start with a dry run of every phase. It records each write to `out/dry-run.jsonl` without changing
Liferay (it still reads from Liferay to look up existing records):

```bash
sp2lr load --dry-run
jq -r '.method + " " + .path' out/dry-run.jsonl | sort | uniq -c | sort -rn | head
```

Then run this chapter's phases for real:

```bash
sp2lr load --phase site --phase users --phase groups
```

## 5.1 Site and Space (`site` phase)

| Step | API |
|---|---|
| Find site by friendly URL | `GET /o/headless-admin-user/v1.0/sites/by-friendly-url-path/{key}` |
| Create site if missing (blank, private membership) | `POST /o/headless-site/v1.0/sites` **VERIFY** |
| Create/update the CMS Space | `PUT /o/headless-asset-library/v1.0/asset-libraries/by-external-reference-code/{erc}` with `"type": "Space"` |

```json
{ "assetLibraryKey": "marketing-content", "name": "Marketing Content",
  "description": "Content migrated from SharePoint site https://contoso.sharepoint.com/sites/marketing",
  "settings": {}, "type": "Space" }
```

**Manual step:** connect the Space to the site under *CMS → Spaces → Marketing Content → Settings →
Connected Sites*, so site pages can display Space content. The response exposes a `connect-sites`
action, but its request body isn't documented yet. See the validation checklist.

## 5.2 Users (`users` phase)

Every Entra user found in site users, in SharePoint group members, or in expanded Entra/M365 groups
becomes a Liferay user:

```json
PUT /o/headless-admin-user/v1.0/user-accounts/by-external-reference-code/entra-8f1c…
{ "externalReferenceCode": "entra-8f1c…", "emailAddress": "jane@contoso.com",
  "alternateName": "jane", "givenName": "Jane", "familyName": "Doe", "jobTitle": "Marketing Lead" }
```

* **Matching:** if a Liferay user with the same e-mail already exists (for example, one provisioned by
  SSO), it's linked and not duplicated.
* **Excluded:** disabled Entra accounts (`users.skipDisabled`), anything in `users.exclude`, and system
  principals (SharePoint App, `SHAREPOINT\system`).
* **Passwords:** none are set. Users sign in through Entra SSO ([Chapter 1](01-prerequisites.md#single-sign-on)).
* **Screen name** (`alternateName`): the UPN local part, sanitized.

## 5.3 Groups (`groups` phase)

| SharePoint principal | Liferay |
|---|---|
| SharePoint group (`PrincipalType 8`) | **User group** `SP <title>` with the group's resolved members |
| Entra security group or M365 group (`PrincipalType 4`) | **User group** `SP <name>` with the **transitive** members |
| Individual user granted directly | Direct site/space membership, with no group |
| *Everyone except external users* | Not migrated as a group; flagged in the plan |

Calls:

```
PUT  /o/headless-admin-user/v1.0/user-groups/by-external-reference-code/{erc}   {"name": "SP Marketing Members"}
POST /o/headless-admin-user/v1.0/user-groups/{id}/user-group-users              [userId, …]
POST /api/jsonws/invoke  {"/usergroup/add-group-user-groups": {"groupId": <site|space>, "userGroupIds": [id]}}
POST /api/jsonws/invoke  {"/usergroupgrouprole/add-user-group-group-roles": {"userGroupId": id, "groupId": …, "roleIds": […]}}
```

> Site and space **membership** for user groups has no headless REST endpoint yet, so the loader uses
> the JSON Web Services `invoke` endpoint for those two operations. JSON-WS is still available in
> 2026.Q3 at `/api/jsonws`. If your security policy disables it, assign the user groups in *Site Settings →
> Memberships → User Groups* and *CMS → Space → Members*.

The phase also creates two custom roles that Edit and Contribute map to (see [Chapter 10](10-permissions.md)):

* `SP Contributor`, a site role
* `SP Space Contributor`, a depot (space) role

## 5.4 Check with the Liferay MCP server

> Using the liferay MCP server, list the user groups whose name starts with "SP " and the number of
> users in each. Compare with `list_site_groups` from the sharepoint MCP server and flag any
> difference in membership counts.

Next: [6. Managed metadata](06-managed-metadata.md)
