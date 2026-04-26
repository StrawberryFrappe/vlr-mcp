# VCT Research Agent

Terminal-based AI for Valorant Champions Tour intelligence.
Browses vlr.gg autonomously to analyze rosters and sentiment.

---

## Quick Start

1. **Install dependencies**
   ```powershell
   pip install -r requirements.txt
   ```

2. **Set API Key**
   Create a `.env` file with:
   `DEEPSEEK_API_KEY=your_key_here`

3. **Run the Agent**
   ```powershell
   python run_vct_demo.py
   ```

---

## Agent Workflow

1. **Discovery**: Scans events and matches for valid IDs.
2. **Investigation**: Reads threads and match data for context.
3. **Synthesis**: Generates data-backed intelligence reports.

---

## Antigravity Integration (MCP)

Add this to your `mcp_config.json` to use tools directly:

```json
{
  "mcpServers": {
    "vlr-gg": {
      "command": "python",
      "args": ["-u", "h:\\mcp\\vlr\\vlr_server.py"]
    }
  }
}
```

---

## Available Tools

- `list_matches`: Discover ongoing and upcoming matches.
- `list_vlr_events`: Browse tournament brackets and standings.
- `get_vlr_resource`: Fetch full content for any entity.
- `search_vlr`: Find specific players or teams.
- `list_vlr_threads`: Monitor community forum sentiment.
