"""
VLR.gg MCP Server
-----------------
Provides four tools for AI agents to navigate vlr.gg:
  - list_vlr_matches  : Browse upcoming/live matches by page.
  - list_vlr_threads  : Browse recent forum threads by page.
  - list_vlr_results  : Browse historical match results by page.
  - get_vlr_resource  : Fetch full content of a match or thread by ID.

Security: All requests are strictly scoped to https://www.vlr.gg.

Reference-Based Sanitization:
  Place match_template.html and/or forum_template.html in the same directory
  as this script. The server will derive a CSS-class whitelist from them and
  apply it when cleaning resource pages, yielding ultra-compact output.
  If templates are missing, it falls back to aggressive standard sanitization.
"""

import os
import re
import sys
import asyncio
import pathlib
from typing import Optional

import httpx
from bs4 import BeautifulSoup, Tag
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ── Windows: ensure UTF-8 on stdout/stderr to prevent pipe deadlocks ──────────
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL = "https://www.vlr.gg"
SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.vlr.gg/",
}
TIMEOUT = httpx.Timeout(20.0)

# Noise tags always stripped regardless of mode
NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]

# Fallback ad/clutter class substrings (used when no template is loaded)
FALLBACK_BLOCKLIST = [
    "ad", "ads", "advertisement", "sidebar", "banner",
    "cookie", "popup", "overlay", "promo",
]

# Hard-coded whitelists derived from the reference HTML files in the workspace.
# These are used as the *default* if a template file is absent but the type
# was still detected, providing a best-effort clean without template files.
MATCH_PAGE_WHITELIST: list[str] = [
    "match-header", "match-header-super", "match-header-note",
    "match-streams", "match-vods-container", "vm-stats",
    "match-bet", "wf-card", "match-stats", "match-series",
    "ge-text", "team", "map-name", "score", "mod-win",
]
FORUM_PAGE_WHITELIST: list[str] = [
    "post", "post-body", "post-header", "post-author",
    "post-content", "thread-header", "thread-item",
    "wf-card", "ge-text",
]
TEAM_PAGE_WHITELIST: list[str] = [
    "team-header", "wf-card", "team-roster-item", "ge-text",
    "team-roster-item-name", "team-roster-item-alias", "team-recent-matches",
    "team-roster-item-name-real"
]

# ── MCP Server ────────────────────────────────────────────────────────────────
server = Server("vlr-gg-server")


# ── Template Loading ──────────────────────────────────────────────────────────

def _extract_classes_from_template(path: pathlib.Path) -> list[str]:
    """Return all unique CSS class tokens found in a reference HTML file."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
    classes: set[str] = set()
    for tag in soup.find_all(True):
        for cls in tag.get("class", []):
            # Keep only meaningful class names (drop utility noise)
            if cls and not cls.startswith("js-") and len(cls) > 2:
                classes.add(cls)
    return sorted(classes)


def _load_template_whitelist(filename: str) -> Optional[list[str]]:
    """Load class whitelist from a workspace template if it exists."""
    path = SCRIPT_DIR / filename
    if path.exists():
        classes = _extract_classes_from_template(path)
        return classes if classes else None
    return None


# Load templates once at startup
_MATCH_TEMPLATE_CLASSES: Optional[list[str]] = _load_template_whitelist("match_template.html")
_FORUM_TEMPLATE_CLASSES: Optional[list[str]] = _load_template_whitelist("forum_template.html")
_TEAM_TEMPLATE_CLASSES: Optional[list[str]] = _load_template_whitelist("team_template.html")

if _MATCH_TEMPLATE_CLASSES:
    sys.stderr.write(f"[vlr-server] match_template.html loaded ({len(_MATCH_TEMPLATE_CLASSES)} classes)\n")
else:
    sys.stderr.write("[vlr-server] match_template.html not found — using built-in whitelist\n")

if _FORUM_TEMPLATE_CLASSES:
    sys.stderr.write(f"[vlr-server] forum_template.html loaded ({len(_FORUM_TEMPLATE_CLASSES)} classes)\n")
else:
    sys.stderr.write("[vlr-server] forum_template.html not found — using built-in whitelist\n")

if _TEAM_TEMPLATE_CLASSES:
    sys.stderr.write(f"[vlr-server] team_template.html loaded ({len(_TEAM_TEMPLATE_CLASSES)} classes)\n")
else:
    sys.stderr.write("[vlr-server] team_template.html not found — using built-in whitelist\n")


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


def _text(el: Optional[Tag]) -> str:
    """Safe text extraction from a BS4 element."""
    if el is None:
        return ""
    return el.get_text(separator=" ", strip=True)


def _extract_id_from_href(href: str) -> Optional[str]:
    """Pull the leading numeric segment from a vlr.gg relative URL."""
    m = re.search(r"/(\d+)/", href)
    if m:
        return m.group(1)
    m = re.search(r"/(\d+)$", href)
    if m:
        return m.group(1)
    return None


def _whitelist_clean(html: str, whitelist: list[str]) -> str:
    """
    Whitelist-based sanitization strategy.

    Keeps only elements whose class list intersects the whitelist.
    Everything else (nav, ads, scripts, etc.) is pruned, yielding
    far less noise than generic tag-removal.
    """
    soup = BeautifulSoup(html, "lxml")

    # Inject Team IDs into team links before extracting text
    for a in soup.find_all("a", href=re.compile(r"/team/(\d+)")):
        team_id = _extract_id_from_href(a["href"])
        if team_id:
            a.append(f" [Team ID: {team_id}]")

    # Always remove pure noise tags first
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()

    # Build a set for O(1) lookups
    wl_set = set(whitelist)

    # Collect root-level elements that have at least one whitelisted class
    body = soup.find("body") or soup
    kept_blocks: list[str] = []

    for el in body.find_all(True, recursive=False):
        _collect_whitelisted(el, wl_set, kept_blocks, depth=0)

    result = "\n".join(kept_blocks)
    # Collapse excessive blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _collect_whitelisted(el: Tag, wl_set: set, output: list, depth: int) -> None:
    """Recursively walk the DOM, collecting text from whitelisted elements."""
    if not isinstance(el, Tag):
        return
    el_classes = set(el.get("class", []))
    if el_classes & wl_set:
        text = el.get_text(separator=" ", strip=True)
        if text:
            indent = "  " * min(depth, 4)
            output.append(f"{indent}{text}")
        return  # Don't recurse into already-captured element
    for child in el.children:
        _collect_whitelisted(child, wl_set, output, depth + 1)


def _fallback_clean(html: str) -> str:
    """Aggressive standard sanitization when no whitelist is available."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    # Remove elements whose class names suggest ads/clutter
    for tag in soup.find_all(True):
        tag_classes = " ".join(tag.get("class", []))
        if any(bl in tag_classes.lower() for bl in FALLBACK_BLOCKLIST):
            tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _detect_page_type(resource_id: str, html: str) -> str:
    """
    Heuristically determine if a resource page is a 'match', 'forum', or 'team' type.
    """
    if "vm-stats" in html or "match-header" in html:
        return "match"
    if "post-body" in html or "thread-header" in html or "post-content" in html:
        return "forum"
    if "team-header" in html or "team-roster-item" in html:
        return "team"
    return "unknown"


def _clean_resource(html: str, resource_id: str) -> str:
    """
    Apply the best available sanitization strategy to a resource page.
    Uses template-derived or built-in whitelist when page type is known.
    """
    page_type = _detect_page_type(resource_id, html)

    if page_type == "match":
        whitelist = _MATCH_TEMPLATE_CLASSES or MATCH_PAGE_WHITELIST
        return _whitelist_clean(html, whitelist)
    elif page_type == "forum":
        whitelist = _FORUM_TEMPLATE_CLASSES or FORUM_PAGE_WHITELIST
        return _whitelist_clean(html, whitelist)
    elif page_type == "team":
        whitelist = _TEAM_TEMPLATE_CLASSES or TEAM_PAGE_WHITELIST
        return _whitelist_clean(html, whitelist)
    else:
        return _fallback_clean(html)


async def _fetch(url: str) -> str:
    """Perform a GET request scoped strictly to vlr.gg."""
    if not url.startswith(BASE_URL):
        raise PermissionError(f"Request blocked: URL {url!r} is outside vlr.gg scope.")
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
    ) as client:
        response = await client.get(url)
        if response.status_code == 404:
            raise FileNotFoundError(f"Resource not found (404): {url}")
        response.raise_for_status()
        return response.text


# ── Structured List Parsers ───────────────────────────────────────────────────

def _parse_match_list(html: str, page: int, label: str) -> str:
    """
    Parse a vlr.gg matches page (schedule or results) into structured lines.

    Output format per entry:
      [ID: 12345] Team A vs Team B - Score: 2-1 | Event Name | Date/Time

    Use the ID with get_vlr_resource to fetch full match details.
    """
    soup = BeautifulSoup(html, "lxml")
    items = soup.find_all("a", class_=re.compile(r"match-item"))
    lines: list[str] = [f"[VLR {label} — Page {page}]",
                        "Use IDs below with get_vlr_resource for full match details.",
                        ""]

    for item in items:
        href = item.get("href", "")
        match_id = _extract_id_from_href(href)
        if not match_id:
            continue

        # Teams
        team_els = item.find_all("div", class_=re.compile(r"match-item-vs-team-name"))
        teams = [_text(t) for t in team_els if _text(t)]
        team_str = " vs ".join(teams) if teams else "TBD vs TBD"

        # Scores
        score_els = item.find_all("div", class_=re.compile(r"match-item-vs-team-score"))
        scores = [_text(s) for s in score_els if _text(s)]
        score_str = "-".join(scores) if scores else ""

        # Event / series
        event_el = item.find("div", class_=re.compile(r"match-item-event-series"))
        event = _text(event_el) if event_el else ""
        if not event:
            event_el2 = item.find("div", class_=re.compile(r"match-item-event"))
            event = _text(event_el2) if event_el2 else ""

        # Date / time / status
        time_el = item.find("div", class_=re.compile(r"match-item-time"))
        eta_el = item.find("div", class_=re.compile(r"match-item-eta"))
        time_str = _text(time_el) or _text(eta_el) or ""

        # Build line
        parts: list[str] = [f"[ID: {match_id}] {team_str}"]
        if score_str:
            parts.append(f"Score: {score_str}")
        if event:
            parts.append(event)
        if time_str:
            parts.append(time_str)

        lines.append(" | ".join(parts))

    if len(lines) == 3:
        lines.append("(No matches found on this page)")
    return "\n".join(lines)


def _parse_thread_list(html: str, page: int) -> str:
    """
    Parse a vlr.gg threads page into structured lines.

    Output format per entry:
      [ID: 24252] Thread Title - Author | N replies

    Use the ID with get_vlr_resource to fetch full thread content.
    """
    soup = BeautifulSoup(html, "lxml")
    items = soup.find_all("a", class_=re.compile(r"thread-item-header-title"))
    lines: list[str] = [f"[VLR Threads — Page {page}]",
                        "Use IDs below with get_vlr_resource for full thread content.",
                        ""]

    for item in items:
        href = item.get("href", "")
        thread_id = _extract_id_from_href(href)
        if not thread_id:
            continue

        title = item.get("title") or _text(item) or "Untitled"

        # Climb to thread-item for metadata
        parent = item.find_parent(class_=re.compile(r"thread-item"))
        author = ""
        replies = ""
        if parent:
            author_el = parent.find(class_=re.compile(r"thread-item-author|post-author"))
            author = _text(author_el) if author_el else ""
            reply_el = parent.find(class_=re.compile(r"thread-item-replies|thread-item-stat"))
            replies = _text(reply_el) if reply_el else ""

        parts: list[str] = [f"[ID: {thread_id}] {title}"]
        if author:
            parts.append(f"by {author}")
        if replies:
            parts.append(f"{replies} replies")

        lines.append(" | ".join(parts))

    if len(lines) == 3:
        lines.append("(No threads found on this page)")
    return "\n".join(lines)


# ── Tool Registry ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_vlr_matches",
            description=(
                "Browse upcoming and live Valorant esports matches from vlr.gg. "
                "Returns structured entries with team names, status, event, and a numeric ID. "
                "IMPORTANT: To investigate a specific match, pass its ID to get_vlr_resource. "
                "Use this tool first to DISCOVER matches before reading full details."
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
                "Returns structured entries with thread titles, authors, reply counts, and a numeric ID. "
                "IMPORTANT: To read a full thread, pass its ID to get_vlr_resource. "
                "Use this tool first to DISCOVER threads before reading full content."
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
            name="list_vlr_results",
            description=(
                "Browse historical Valorant esports match results from vlr.gg. "
                "Returns structured entries with team names, final score, event name, "
                "match date, and a numeric ID per match. "
                "IMPORTANT: To investigate a specific result in detail (maps, stats, VODs), "
                "pass its ID to get_vlr_resource. "
                "Use this tool to DISCOVER past results before fetching full match data."
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
                "Uses reference-based whitelist sanitization to return ultra-clean, "
                "structured text for agent reasoning. "
                "IMPORTANT: Obtain the resource_id from list_vlr_matches, "
                "list_vlr_threads, or list_vlr_results first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "resource_id": {
                        "type": "string",
                        "description": (
                            "Numeric ID of the vlr.gg resource (e.g. '12345'). "
                            "Digits only. Obtain from list_vlr_matches, "
                            "list_vlr_threads, or list_vlr_results."
                        ),
                    }
                },
                "required": ["resource_id"],
            },
        ),
        types.Tool(
            name="get_vlr_team",
            description=(
                "Fetch the roster, recent form, and upcoming matches for a specific VCT team. "
                "Returns clean, structured text based on the team's profile page. "
                "IMPORTANT: To find a team_id, you must first read a match page using get_vlr_resource "
                "and look for the '[Team ID: XXX]' tags next to the team names."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "team_id": {
                        "type": "string",
                        "description": "Numeric ID of the VCT team (e.g. '490'). Digits only.",
                    }
                },
                "required": ["team_id"],
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
            text = _parse_match_list(html, page, "Matches")
            return [types.TextContent(type="text", text=text)]

        elif name == "list_vlr_threads":
            page = _validate_page(int(arguments.get("page", 1)))
            url = f"{BASE_URL}/threads/?page={page}"
            html = await _fetch(url)
            text = _parse_thread_list(html, page)
            return [types.TextContent(type="text", text=text)]

        elif name == "list_vlr_results":
            page = _validate_page(int(arguments.get("page", 1)))
            url = f"{BASE_URL}/matches/results/?page={page}"
            html = await _fetch(url)
            text = _parse_match_list(html, page, "Results")
            return [types.TextContent(type="text", text=text)]

        elif name == "get_vlr_resource":
            raw_id = arguments.get("resource_id", "")
            resource_id = _validate_resource_id(raw_id)
            url = f"{BASE_URL}/{resource_id}"
            html = await _fetch(url)
            text = _clean_resource(html, resource_id)
            template_note = ""
            if _MATCH_TEMPLATE_CLASSES or _FORUM_TEMPLATE_CLASSES:
                template_note = " [whitelist from template]"
            header = f"[VLR Resource — ID {resource_id}{template_note}]\n\n"
            return [types.TextContent(type="text", text=header + text)]

        elif name == "get_vlr_team":
            raw_id = arguments.get("team_id", "")
            team_id = _validate_resource_id(raw_id)
            url = f"{BASE_URL}/team/{team_id}"
            html = await _fetch(url)
            text = _clean_resource(html, team_id)
            template_note = ""
            if _TEAM_TEMPLATE_CLASSES:
                template_note = " [whitelist from template]"
            header = f"[VLR Team Profile — ID {team_id}{template_note}]\n\n"
            return [types.TextContent(type="text", text=header + text)]

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
