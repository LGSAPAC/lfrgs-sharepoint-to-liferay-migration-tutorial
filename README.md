# SharePoint Online → Liferay DXP 2026.Q3 Migration Tutorial

A hands-on tutorial (with working tooling) for migrating a **live SharePoint Online site** to
**Liferay DXP 2026.Q3** and its **new CMS** (Spaces, content structures and assets built on
Liferay Objects):

| SharePoint Online | Liferay DXP 2026.Q3 |
|---|---|
| Site (team / communication site) | **Site** for pages + a connected CMS **Space** for content |
| Modern site pages (`SitePages/*.aspx`) | **Content pages** built from fragments, plus a navigation menu |
| Images on pages | **Documents and Media** files in the site, links rewritten |
| Custom lists (content: catalogs, FAQs, policies) | CMS **content structures** in a Space (depot-scoped object definitions) |
| Custom lists (apps: requests, orders, approvals) | Liferay **Objects** in the site, with **object views** from list views |
| List items + attachments | Structure **entries** (object entries) with Attachment fields |
| Choice / lookup columns | **Picklists** / object **relationships** |
| Entra ID users | Liferay **user accounts** keyed by Entra object ID (sign in with SSO) |
| SharePoint groups / Entra groups | **User groups** with site and space membership |
| Permission levels | **Site roles** and **space (asset library) roles** |
| Unique item permissions | **Entry-level permissions** via per-principal roles |

The migration is driven by **two MCP servers**, so an AI agent (Claude Code, Claude Desktop, Cursor, VS Code
Copilot) can inventory the source, help with mapping decisions and check the result:

* **SharePoint MCP server**: shipped in this repo (`sharepoint-mcp`), read-only, built on Microsoft
  Graph and the SharePoint REST API. It exposes pages, lists, schemas, items, groups and role assignments.
* **Liferay MCP server**: built into DXP 2026.Q3 at `/o/mcp`. It exposes the headless APIs as MCP tools.

Bulk data moves through a deterministic, idempotent CLI (`sp2lr`) rather than through the LLM. The agent
plans and verifies. The CLI copies the records.

```
              ┌────────────────────┐   MCP (stdio)   ┌─────────────────────────┐
              │  SharePoint Online │◄───────────────►│                         │
              │  Graph + REST API  │                 │  AI agent (Claude Code) │
              └─────────┬──────────┘                 │  discovery · mapping ·  │
                        │ extract                    │  validation             │
                        ▼                            └────────────┬────────────┘
   sp2lr extract → out/extract/*.json                             │ MCP (HTTP)
   sp2lr plan    → out/plan.json + plan.md  ◄── review ───────────┤
   sp2lr load    → Liferay headless APIs ──────────────┐          │
   sp2lr verify  → out/verify.md                       ▼          ▼
                                             ┌─────────────────────────────┐
                                             │ Liferay DXP 2026.Q3  /o/mcp │
                                             │ Site · CMS Space · Objects  │
                                             └─────────────────────────────┘
```

## Tutorial

| # | Chapter | What you do |
|---|---|---|
| 1 | [Prerequisites](docs/01-prerequisites.md) | Entra app registration, Liferay 2026.Q3 in Docker, Python toolchain |
| 2 | [MCP servers](docs/02-mcp-servers.md) | Enable Liferay's MCP server, run the SharePoint MCP server, wire both into Claude Code |
| 3 | [Discovery and inventory](docs/03-discovery.md) | Inventory the live site through MCP and extract it |
| 4 | [Mapping and plan](docs/04-mapping-and-plan.md) | Decide CMS vs Objects, field mappings, `mapping.yaml`, review `plan.md` |
| 5 | [Users and groups](docs/05-users-and-groups.md) | Entra users → Liferay users, SharePoint groups → user groups |
| 6 | [Lists: CMS structures or Objects](docs/06-lists-cms-or-objects.md) | Two scenarios: content lists → CMS structures, app lists → Objects (views, validations, actions) |
| 7 | [Pages](docs/07-pages.md) | Modern pages → content pages, images, navigation |
| 8 | [Permissions](docs/08-permissions.md) | Permission levels → roles, unique permissions → entry permissions |
| 9 | [Validation and cutover](docs/09-validation-and-cutover.md) | Reconcile, agent-assisted spot checks, delta re-runs, cutover |
| — | [Validation checklist](docs/VALIDATION-CHECKLIST.md) | API assumptions to confirm against a real 2026.Q3 instance |
| — | [Troubleshooting](docs/10-troubleshooting.md) | Common errors and fixes |

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env          # fill in tenant, app ID, site URL, Liferay credentials
cp mapping.example.yaml mapping.yaml

docker compose -f docker/docker-compose.yml up -d   # Liferay DXP 2026.Q3 (needs a DXP license)

sp2lr extract                 # read the live SharePoint site
sp2lr plan                    # writes out/plan.md, so review it
sp2lr load --dry-run          # writes out/dry-run.jsonl, so review it
sp2lr load                    # site → users → groups → structures → entries → pages → permissions
sp2lr verify                  # writes out/verify.md
```

Then open Claude Code in this folder (`.mcp.json` is already configured) and run the prompts in
[`prompts/`](prompts/) to inventory, review and validate with both MCP servers.

## Repository layout

```
docs/                  tutorial chapters
prompts/               agent prompts for discovery, plan review and validation
src/sp2lr/
  sharepoint.py        read-only Graph + SharePoint REST client
  mcp_server.py        SharePoint MCP server (stdio)
  extract.py           phase 1: SharePoint → out/extract
  transform.py         pure SharePoint → Liferay mappings (unit-tested)
  plan.py              phase 2: out/plan.json + plan.md
  liferay.py           Liferay headless client (dry-run aware)
  load.py              phase 3: idempotent loader
  verify.py            phase 4: reconciliation report
tests/                 offline tests with a synthetic extract
docker/                Liferay DXP 2026.Q3 docker compose + portal-ext.properties
.mcp.json              Claude Code config for both MCP servers
mapping.example.yaml   per-site overrides
```

## Scope

Included: modern site pages, custom lists (and their attachments), users, SharePoint and Entra groups,
site-level permissions, and list- and item-level unique permissions.

Not included, but listed as manual follow-ups: document libraries (use Liferay's Documents and Media
bulk import or a separate DAM migration), classic wiki/publishing pages, SPFx and third-party web parts,
Power Automate flows, list views and formatting, version history, and page-level unique permissions.

## Status

This tutorial targets **Liferay DXP 2026.Q3**, where the new CMS is GA and the MCP server is a release
feature (flag `LPD-63311`). A few endpoints that the CMS docs don't document yet are marked
`VERIFY` and collected in [docs/VALIDATION-CHECKLIST.md](docs/VALIDATION-CHECKLIST.md). Every
one of those calls falls back to a recorded manual step, so a migration doesn't stop when one fails.
