# How to Create HTML Template Files for Reference-Based Sanitization

The VLR MCP server can load local HTML snapshots of vlr.gg pages to derive a
**CSS-class whitelist**. When this whitelist is active, `get_vlr_resource`
returns far less noise — keeping only the data containers that matter.

---

## Why Templates Improve Output

Without templates, the server uses a built-in list of ~15 known useful classes.
With templates, the server extracts **every class** present in your saved page,
giving it a much richer map of what to keep vs. discard.

---

## Step 1 — Save a Match Page as `match_template.html`

1. Open any completed match page in your browser, e.g.:
   `https://www.vlr.gg/650945/lazer-vs-fuego-challengers-2026-latam-north-ace-masters-elim-b`
2. Press **Ctrl+S** (Save As).
3. Choose **"Webpage, Complete"** or **"Webpage, HTML Only"** (either works).
4. Rename the saved file to exactly: **`match_template.html`**
5. Move it into: `H:\mcp\vlr\match_template.html`

---

## Step 2 — Save a Forum Thread as `forum_template.html`

1. Open any forum thread in your browser, e.g.:
   `https://www.vlr.gg/660861/we-need-to-talk-about-boaster`
2. Press **Ctrl+S** → Save As HTML.
3. Rename to: **`forum_template.html`**
4. Move it into: `H:\mcp\vlr\forum_template.html`

---

## Step 3 — Restart the MCP Server

After placing the files, restart the server. At startup it will print to stderr:

```
[vlr-server] match_template.html loaded (312 classes)
[vlr-server] forum_template.html loaded (198 classes)
```

If a template is not found, it falls back to the built-in whitelist silently.

---

## Verification

Call `get_vlr_resource` on any match ID. The header line will say:

```
[VLR Resource — ID 650945 [whitelist from template]]
```

The `[whitelist from template]` tag confirms the sanitizer is using your files.

---

## Notes

- Templates only need to be saved **once**. They are read at server startup.
- The server does not send template files anywhere — they are parsed locally.
- You can refresh templates at any time by saving a new page and restarting.
- The filenames are **case-sensitive** on Linux but not on Windows.
