# 9. Pages and news

```bash
sp2lr load --phase pages --phase news
```

## 9.1 How a modern page maps to a content page

A SharePoint modern page is a **canvas**: horizontal sections, each with a layout and 1–3 columns,
each column holding web parts, plus an optional vertical section. A Liferay content page is a
**page definition**: a tree of Section → Row → Column → Fragment.

```
SharePoint canvasLayout                      Liferay pageDefinition
───────────────────────                      ──────────────────────
                                             Section  (heading fragment = page title)
horizontalSection layout=twoColumns    ──►   Section (Fixed) ─ Row (2 cols)
  column width=6                                 Column size=6
    textWebPart innerHtml              ──►         Fragment BASIC_COMPONENT-paragraph
  column width=6                                 Column size=6
    standardWebPart Image              ──►         Fragment paragraph with <img src="/documents/…">
horizontalSection layout=fullWidth     ──►   Section (Fluid) ─ Row (1 col)
verticalSection                        ──►   Section (Fixed) at the end
```

| Canvas layout | Column sizes (of 12) |
|---|---|
| `oneColumn`, `fullWidth` | 12 |
| `twoColumns` | 6 + 6 |
| `threeColumns` | 4 + 4 + 4 |
| `oneThirdLeftColumn` | 4 + 8 |
| `oneThirdRightColumn` | 8 + 4 |

Graph returns each column's `width` directly. The table above is only a fallback.

## 9.2 Web parts

| Web part | Result |
|---|---|
| **Text** (`textWebPart`) | Paragraph fragment with the HTML. Image and page links are rewritten |
| **Image** (`d1d91016-…`) | Image uploaded to the site's *SharePoint Images* folder, `<img>` in a paragraph fragment |
| Anything else (Hero, Quick links, News, Events, List, Document library, Embed, SPFx…) | A visible placeholder `[Unmigrated SharePoint web part: <title>]`, listed in `plan.md` → *Manual follow-ups* |

Suggested Liferay replacements for the common ones:

| SharePoint web part | Liferay 2026.Q3 |
|---|---|
| Hero | *Banner* fragment, or a custom fragment collection |
| Quick links | *Button* fragments in a grid, or a Navigation Menu fragment |
| News / Highlighted content | **Collection Display** fragment over a CMS collection (blogs or structure entries) |
| List web part (of a migrated list) | **Collection Display** over the content structure, with a display page template |
| Events | A content structure `Event` + Collection Display filtered by date |
| Embed | *HTML* fragment (with care) or a Remote App |

## 9.3 The API call

```json
POST /o/headless-delivery/v1.0/sites/{siteId}/site-pages
{
  "externalReferenceCode": "sp-page-6d3e…",
  "title": "About Us", "title_i18n": {"en_US": "About Us"},
  "friendlyUrlPath": "/about-us", "friendlyUrlPath_i18n": {"en_US": "/about-us"},
  "pageDefinition": { "pageElement": { "type": "Root", "pageElements": [
    { "type": "Section", "definition": {"layout": {"widthType": "Fixed"}}, "pageElements": [
      { "type": "Fragment", "definition": {
          "fragment": {"key": "BASIC_COMPONENT-heading"},
          "fragmentFields": [{"id": "element-text", "value": {"text": {"value_i18n": {"en_US": "About Us"}}}}] } } ] },
    { "type": "Section", "definition": {"layout": {"widthType": "Fixed"}}, "pageElements": [
      { "type": "Row", "definition": {"numberOfColumns": 1, "modulesPerRow": 1, "gutters": true}, "pageElements": [
        { "type": "Column", "definition": {"size": 12}, "pageElements": [
          { "type": "Fragment", "definition": {
              "fragment": {"key": "BASIC_COMPONENT-paragraph"},
              "fragmentFields": [{"id": "element-text", "value": {"text": {"value_i18n": {"en_US": "<p>…</p>"}}}}] } } ] } ] } ] } ] } },
  "pageSettings": {"seoSettings": {"description_i18n": {"en_US": "Who we are"}}}
}
```

* **Friendly URL**: the page file name, slugified (`About-Us.aspx` → `/about-us`).
* **Idempotency**: if a page with the same friendly URL exists, it's skipped. To re-migrate a page,
  delete it in Liferay first.
* **Links**: `/sites/marketing/SitePages/About-Us.aspx` and its absolute form are rewritten to
  `/web/marketing/about-us` in every text web part.
* **Images**: uploaded with `POST /o/headless-delivery/v1.0/document-folders/{id}/documents`, and the
  `contentUrl` replaces the SharePoint URL.

## 9.4 Navigation

Quick Launch (or Top navigation, if Quick Launch is empty) becomes a navigation menu called
*SharePoint Navigation*, with nested items and rewritten URLs:

```
POST /o/headless-delivery/v1.0/sites/{siteId}/navigation-menus
{"name": "SharePoint Navigation", "navigationMenuItems": [{"name": "About", "type": "url", "url": "/web/marketing/about-us", "navigationMenuItems": […]}]}
```

**Manual steps:** mark the menu as *Primary Navigation* (*Site Builder → Navigation Menus*) and move
the migrated home page to first position (*Site Builder → Pages*).

## 9.5 News posts

SharePoint news is a modern page with `promotionKind = newsPost`. In Liferay, news belongs in
**Blogs** or in a **News** content structure, not in the page tree. Choose in `mapping.yaml`:

```yaml
news:
  target: blogs        # blogs | structure | pages
  structureName: News  # only for target: structure
```

| `news.target` | Result | Good for |
|---|---|---|
| `blogs` (default) | One **blog posting** per news post in the site | Classic intranet news with RSS, comments, the Blogs widget |
| `structure` | Entries of a `News` **CMS content structure** in the Space (title, summary, rich-text body, published date + audit fields) | Headless or multi-site news, Collection Display, display page templates |
| `pages` | Migrated as ordinary content pages | Keeping the old behaviour |

The page canvas is flattened into one HTML body, with text web parts in order and images uploaded
and rewritten. The original **first-published date** is kept:

```json
POST /o/headless-delivery/v1.0/sites/{siteId}/blog-postings
{ "externalReferenceCode": "sp-news-6d3e…", "headline": "Q3 results are in",
  "alternativeHeadline": "Record quarter", "description": "Record quarter",
  "articleBody": "<p>Revenue up 20%</p>", "friendlyUrlPath": "q3-results",
  "datePublished": "2026-09-30T07:00:00Z" }
```

Links to news pages from other pages and navigation are rewritten to
`/web/<site>/-/blogs/<slug>`. The blog author is the migration user unless
`audit.impersonate: true` is set ([Chapter 4](04-mapping-and-plan.md#45-created-modified-and-author)).

## 9.6 Visual check

> With the liferay MCP server, list the site pages of site "marketing" (fields: title, friendlyUrlPath).
> For each, compare with the SharePoint page from `list_site_pages`. Then fetch the page definition of
> "/about-us" and the SharePoint canvas of About-Us.aspx and confirm that the section and column
> structure and the text match.

Next: [10. Permissions](10-permissions.md)
