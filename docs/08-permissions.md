# 8. Permissions

```bash
sp2lr load --phase permissions
```

SharePoint and Liferay model permissions differently, so read this chapter before you sign off.

| | SharePoint Online | Liferay DXP |
|---|---|---|
| Who | Users, SharePoint groups, Entra/M365 groups | Users, user groups (and organizations) |
| What | **Permission levels** (bundles of base permissions) | **Roles** (bundles of resource actions) |
| Where | Web → list → item, inherited until broken | Site / Space membership + role; resource permissions per entry |
| Granted by | Role assignment = principal + level on a securable | Role assigned to a user/user group in a site or space; actions granted to roles per resource |

## 8.1 Site-level permissions

Each role assignment on the web becomes membership plus a role in both the **site** and the **space**:

| SharePoint level | Liferay site role | Liferay space role |
|---|---|---|
| Full Control | Site Owner | Asset Library Owner |
| Design | Site Content Reviewer | Asset Library Administrator |
| Edit | `SP Contributor` (created) | `SP Space Contributor` (created) |
| Contribute | `SP Contributor` | `SP Space Contributor` |
| Read, View Only | membership only (Site Member) | membership only (Asset Library Member) |
| Limited Access | ignored (SharePoint internal) | ignored |
| Custom level | mapped to Read with a warning, unless `mapping.yaml` says otherwise | ditto |

The default mapping is in `src/sp2lr/transform.py` (`DEFAULT_LEVEL_MAP`). Override it per tenant under
`permissions.levels` in `mapping.yaml`.

### Configure the two custom roles

The `groups` phase creates the roles empty. Give them actions once per environment, in the UI or with
the Liferay MCP server:

* **SP Contributor** (site role): *Site Builder → Pages*: View, Update. *Documents and Media*: Add Document, View.
* **SP Space Contributor** (depot role): *Content structures (each migrated one)*: View, Add Entry,
  Update, Delete. *Folders*: View, Add Entry.

And on every migrated **content structure**, grant **View** to *Asset Library Member*. Custom
structures have View disabled by default, so space members won't see entries otherwise.

> Agent prompt: *"With the liferay MCP server, show the permissions of role SP Space Contributor and
> of the content structures ProductCatalog and Orders. Tell me which actions from chapter 8 are
> missing."*

## 8.2 Unique (broken-inheritance) permissions

For each list with unique permissions, and each item with unique permissions, the loader:

1. Creates a depot role `SP Principal - <name>` for every principal that appears in those
   assignments, and assigns it to that principal's user group in the Space.
2. Replaces the entry's permissions. The baseline roles that would otherwise inherit access (*Asset
   Library Member*, *Asset Library Connected Site Member*, *Site Member*, *Guest*, *User*) get **no
   actions**, and each principal's role gets the actions of its level:

| Level | Entry actions |
|---|---|
| Full Control | VIEW, UPDATE, DELETE, PERMISSIONS |
| Design, Edit | VIEW, UPDATE, DELETE |
| Contribute | VIEW, UPDATE |
| Read, View Only | VIEW |

```json
PUT /o/c/orders/{entryId}/permissions
[ {"roleName": "Asset Library Member", "actionIds": []},
  {"roleName": "Site Member", "actionIds": []},
  {"roleName": "SP Principal - SP Marketing Owners", "actionIds": ["VIEW","UPDATE","DELETE","PERMISSIONS"]} ]
```

A list-level unique assignment is applied to every entry of that list, unless an item has its own.

## 8.3 What isn't migrated automatically

* **Page-level unique permissions.** They're listed as manual follow-ups. Set them in *Site Builder →
  Pages → ⋮ → Permissions*.
* **Sharing links** ("Anyone with the link", "People in your organization"). Liferay has no equivalent
  for anonymous share links. Review them in SharePoint (*Site usage → Shared with external users*).
* **Item-level grants to single users.** These are migrated through a per-user principal role. If there
  are many, consider redesigning them around groups.
* **Deny permissions.** SharePoint Online has no deny at site level. Liferay has none either, so nothing
  is lost.

## 8.4 Security review checklist

- [ ] Every user in `SP <Owners>` holds Site Owner in the site and Asset Library Owner in the Space
- [ ] A user who was Read-only in SharePoint can view but not edit pages and entries
- [ ] An entry that was restricted in SharePoint is invisible to a regular member (test with an
      impersonated user: *Control Panel → Users → ⋮ → Impersonate User*)
- [ ] Guests (`#EXT#`) only see what they saw before
- [ ] The migration account is disabled or demoted after cutover

Next: [9. Validation and cutover](09-validation-and-cutover.md)
