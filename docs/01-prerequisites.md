# 1. Prerequisites

You need three things: access to the **source** SharePoint Online site, a **target** Liferay DXP
2026.Q3 instance, and a workstation with Python and an MCP-capable AI client.

## 1.1 Source: SharePoint Online

| Requirement | Why |
|---|---|
| A Microsoft 365 tenant with the site to migrate | Source of pages, lists, users and permissions |
| A migration account that is **Site Collection Administrator** on the site | Reading role assignments needs *Enumerate Permissions*. Reading Entra profiles needs directory read access. |
| Rights to register an app in Microsoft Entra ID, or an admin who can do it for you | API access |

### Register the Entra ID application

1. Go to [Microsoft Entra admin center](https://entra.microsoft.com) → **Identity → Applications → App registrations → New registration**.
2. Name it `Liferay Migration`, choose **Accounts in this organizational directory only** and leave Redirect URI empty. Click **Register**.
3. Copy the **Application (client) ID** and the **Directory (tenant) ID** into `.env` as `SP_CLIENT_ID` and `SP_TENANT_ID`.
4. **Authentication** → *Advanced settings* → set **Allow public client flows** to **Yes**. Device-code sign-in needs this.
5. **API permissions → Add a permission**:

   | API | Type | Permission | Used for |
   |---|---|---|---|
   | Microsoft Graph | Delegated | `Sites.Read.All` | Site resolution, modern pages (`canvasLayout`) |
   | Microsoft Graph | Delegated | `User.Read.All` | Entra profiles for Liferay user accounts |
   | Microsoft Graph | Delegated | `GroupMember.Read.All` | Expanding Entra and M365 groups used in SharePoint permissions |
   | Microsoft Graph | Delegated | `TermStore.Read.All` | Managed metadata term sets → Liferay vocabularies |
   | SharePoint | Delegated | `AllSites.FullControl` | SharePoint REST: list schemas, items, SharePoint groups, **role assignments** |

   > `AllSites.FullControl` is requested because SharePoint only returns role assignments to callers
   > with *Enumerate Permissions*. The tooling only ever sends **GET** requests to SharePoint.
   > Check `src/sp2lr/sharepoint.py`: it has no write method.

6. Click **Grant admin consent for <tenant>**.

> Added `TermStore.Read.All` after your first sign-in? Delete `~/.sp2lr/token_cache.json` and run
> `sp2lr login` again, so the cached token picks up the new scope.

#### Option: app-only (unattended) access

For scheduled delta runs, use a certificate instead of device code:

* Add **Application** permissions: Graph `Sites.Read.All`, `User.Read.All`, `GroupMember.Read.All`, `TermStore.Read.All`, and
  SharePoint `Sites.FullControl.All`. Alternatively, use `Sites.Selected` and grant `fullcontrol` on the
  one source site.
* Upload a certificate under **Certificates & secrets**. SharePoint REST **rejects app-only tokens
  obtained with a client secret**, so it must be a certificate.
* In `.env`, set `SP_AUTH_MODE=certificate`, `SP_CERT_PATH` (PEM private key) and `SP_CERT_THUMBPRINT`.

## 1.2 Target: Liferay DXP 2026.Q3

Use 2026.Q3 or later. That's the first quarterly release where:

* the new **CMS** (Spaces, content structures, assets) is **GA** and needs no feature flag, and
* the **MCP server** is a release feature (flag `LPD-63311`).

### Local instance with Docker

```bash
cp ~/Downloads/activation-key-*.xml docker/liferay/deploy/   # DXP license
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f liferay  # wait for "Server startup in"
```

[`docker/liferay/files/portal-ext.properties`](../docker/liferay/files/portal-ext.properties) sets the
tutorial admin to `test@liferay.com` / `learn` and turns on the MCP feature flags.

> Pin `LIFERAY_TAG` to the exact build you validate against, for example
> `LIFERAY_TAG=2026.q3.0 docker compose ...`. Check the tag list on Docker Hub (`liferay/dxp`).

### Migration account in Liferay

For anything other than a sandbox, create a dedicated **Administrator** user, such as
`migration@yourcompany.com`, and put its credentials in `.env` (`LIFERAY_USER`, `LIFERAY_PASSWORD`,
`LIFERAY_BASIC`). Every record the migration creates is attributed to this account.

```bash
printf '%s' 'migration@yourcompany.com:<password>' | base64   # -> LIFERAY_BASIC
```

For production, use OAuth 2.0 instead of Basic auth. See [Chapter 2](02-mcp-servers.md#oauth).

### Single sign-on

Migrated users have **no Liferay password**. Configure Entra ID as the identity provider before
cutover, using **OpenID Connect** (Control Panel → Instance Settings → SSO → OpenID Connect Provider
Connection) or **SAML**, and match users on e-mail address. User accounts carry the Entra object ID as
their External Reference Code (`entra-<objectId>`), which is useful for a later SCIM provisioning setup.

## 1.3 Workstation

| Tool | Version |
|---|---|
| Python | 3.11+ |
| Node.js | 18+ (only for `npx @modelcontextprotocol/inspector` and `mcp-remote`) |
| Docker | 24+ |
| Claude Code (or another MCP client) | current |

```bash
git clone https://github.com/<org>/lfrgs-sharepoint-to-liferay-migration-tutorial.git
cd lfrgs-sharepoint-to-liferay-migration-tutorial
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q                     # offline tests should pass
cp .env.example .env          # then edit
cp mapping.example.yaml mapping.yaml
```

Next: [2. MCP servers](02-mcp-servers.md)
