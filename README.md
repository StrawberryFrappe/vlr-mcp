# VLR.gg MCP Server

Browse and extract vlr.gg content inside Antigravity.

---

## 1. Install Dependencies

```powershell
cd h:\mcp\vlr
pip install -r requirements.txt
```

---

## 2. Add to Antigravity (mcp_config.json)

Open your Antigravity MCP config file.  
Location: `C:\Users\<you>\.gemini\mcp_config.json`

Add this entry inside the `"mcpServers"` object:

```json
{
  "mcpServers": {
    "vlr-gg": {
      "command": "python",
      "args": ["-u", "h:\\mcp\\vlr\\vlr_server.py"],
      "env": {}
    }
  }
}
```

> **Note**: The `-u` flag forces unbuffered I/O. Required on Windows to prevent pipe deadlocks.

---

## 3. Restart Antigravity

Reload the app so it picks up the new server entry.

---

## 4. Available Tools

| Tool | Description | Key Parameter |
|---|---|---|
| `list_vlr_matches` | List recent/upcoming matches | `page` (int, default 1) |
| `list_vlr_threads` | List recent forum threads | `page` (int, default 1) |
| `get_vlr_resource` | Fetch full match or thread content | `resource_id` (digits only) |

---

## 5. Example Agent Flow

1. Call `list_vlr_matches` to discover match IDs.
2. Call `get_vlr_resource` with a specific ID for full content.
3. Perform sentiment analysis or summarization on the result.

---

## Security Notes

- All requests are strictly scoped to `https://www.vlr.gg`.
- `resource_id` accepts digits only — no path traversal possible.
- Page numbers are validated as positive integers.
