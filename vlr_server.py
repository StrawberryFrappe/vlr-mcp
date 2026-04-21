"""
VLR.gg MCP Server
-----------------
Provides three tools for AI agents to navigate vlr.gg:
  - list_vlr_matches  : Browse recent/upcoming matches by page.
  - list_vlr_threads  : Browse recent forum threads by page.
  - get_vlr_resource  : Fetch full content of a match or thread.

Security: All requests are strictly scoped to https://www.vlr.gg.
"""

import re
import sys
import asyncio

import httpx
from bs4 import BeautifulSoup
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ── Windows: ensure UTF-8 on stdout/stderr to prevent pipe deadlocks ──────────
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL = "https://www.vlr.gg"
NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "iframe"]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = httpx.Timeout(15.0)

# ── MCP Server ────────────────────────────────────────────────────────────────
server = Server("vlr-gg-server")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_page(page: int) -> int:
    """Clamp page to a sane positive integer."""
    if not isinstance(page, int) or page < 1:
        raise ValueError(f"Invalid page number: {page!r}. Must be a positive integer.")
    return page


def _validate_resource_id(resource_id: str) -> str:
    """Allow only numeric-only resource IDs to prevent path traversal."""
    cleaned = resource_id.strip().lstrip("/")
    if not re.fullmatch(r"\d+", cleaned):
        raise ValueError(
            f"Invalid resource_id: {resource_id!r}. Must contain digits only."
        )
    return cleaned


def _clean_html(html: str) -> str:
    """Strip noise tags and return readable text with minimal structure."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    # Collapse excessive whitespace preserving line breaks
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned


async def _fetch(url: str) -> str:
    """Perform a GET request scoped strictly to vlr.gg."""
    if not url.startswith(BASE_URL):
        raise PermissionError(f"Request blocked: URL {url!r} is outside vlr.gg scope.")
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        response = await client.get(url)
        if response.status_code == 404:
            raise FileNotFoundError(f"Resource not found (404): {url}")
        response.raise_for_status()
        return response.text


# ── Tool A: list_vlr_matches ──────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_vlr_matches",
            description=(
                "Browse recent and upcoming Valorant esports matches from vlr.gg. "
                "Returns match titles, team names, match status, and resource IDs "
                "that can be passed to get_vlr_resource for full details. "
                "Use this tool to DISCOVER matches before reading them."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "description": "Pagination page number (default: 1, must be >= 1).",
                        "default": 1,
                        "minimum": 1,
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="list_vlr_threads",
            description=(
                "Browse recent community forum threads from vlr.gg. "
                "Returns thread titles, authors, reply counts, and resource IDs "
                "that can be passed to get_vlr_resource for full thread content. "
                "Use this tool to DISCOVER discussions before reading them."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "description": "Pagination page number (default: 1, must be >= 1).",
                        "default": 1,
                        "minimum": 1,
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="get_vlr_resource",
            description=(
                "Fetch the full content of a specific vlr.gg match page or forum thread. "
                "Use resource_id values obtained from list_vlr_matches or list_vlr_threads. "
                "Returns cleaned, structured text suitable for agent reasoning and sentiment analysis. "
                "Use this tool to READ a specific resource after DISCOVERING it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "resource_id": {
                        "type": "string",
                        "description": (
                            "Numeric ID of the vlr.gg resource (e.g. '12345'). "
                            "Must contain digits only. Obtain from list_vlr_matches or list_vlr_threads."
                        ),
                    }
                },
                "required": ["resource_id"],
            },
        ),
    ]


# ── Tool Handlers ─────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "list_vlr_matches":
            page = _validate_page(int(arguments.get("page", 1)))
            url = f"{BASE_URL}/matches/?page={page}"
            html = await _fetch(url)
            text = _clean_html(html)
            return [types.TextContent(type="text", text=f"[VLR Matches — Page {page}]\n\n{text}")]

        elif name == "list_vlr_threads":
            page = _validate_page(int(arguments.get("page", 1)))
            url = f"{BASE_URL}/threads/?page={page}"
            html = await _fetch(url)
            text = _clean_html(html)
            return [types.TextContent(type="text", text=f"[VLR Threads — Page {page}]\n\n{text}")]

        elif name == "get_vlr_resource":
            raw_id = arguments.get("resource_id", "")
            resource_id = _validate_resource_id(raw_id)
            url = f"{BASE_URL}/{resource_id}"
            html = await _fetch(url)
            text = _clean_html(html)
            return [types.TextContent(type="text", text=f"[VLR Resource — ID {resource_id}]\n\n{text}")]

        else:
            raise ValueError(f"Unknown tool: {name!r}")

    except ValueError as exc:
        return [types.TextContent(type="text", text=f"[Validation Error] {exc}")]
    except FileNotFoundError as exc:
        return [types.TextContent(type="text", text=f"[Not Found] {exc}")]
    except PermissionError as exc:
        return [types.TextContent(type="text", text=f"[Security Error] {exc}")]
    except httpx.TimeoutException:
        return [types.TextContent(type="text", text="[Timeout Error] vlr.gg did not respond in time. Try again.")]
    except httpx.HTTPStatusError as exc:
        return [types.TextContent(type="text", text=f"[HTTP Error] {exc.response.status_code}: {exc.request.url}")]
    except Exception as exc:  # noqa: BLE001
        return [types.TextContent(type="text", text=f"[Unexpected Error] {type(exc).__name__}: {exc}")]


# ── Entry Point ───────────────────────────────────────────────────────────────

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
