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


## 3. Available Tools

| Tool | Description | Key Parameter |
|---|---|---|
| `list_matches` | Discover matches globally or by entity | `page`, `status`, `team_id`, `player_id`, `event_id` |
| `list_vlr_events` | List ongoing and upcoming events | `page` |
| `list_vlr_threads` | List recent forum threads | `page` |
| `get_vlr_resource` | Fetch full context of an entity (match, thread, team, player, event) | `resource_id`, `team_id`, `player_id`, `event_id` |
| `search_vlr` | Search for players, teams, or events | `query` |

---

## 5. Example Agent Flow

1. Call `list_matches` to discover match IDs.
2. Call `get_vlr_resource` with a specific ID (`resource_id` etc) for full content.
3. Perform sentiment analysis or summarization on the result.

---

## Security Notes

- All requests are strictly scoped to `https://www.vlr.gg`.
- `resource_id` accepts digits only — no path traversal possible.
- Page numbers are validated as positive integers.
