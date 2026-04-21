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
import urllib.parse
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
    "team-roster-item-name-real", "wf-module-item", "team-event-item"
]

NEWS_PAGE_WHITELIST: list[str] = [
    "article-header", "article-body", "article-title", "article-meta", "article-meta-author",
    "post", "post-body", "post-header", "post-author", "post-content"
]
PLAYER_PAGE_WHITELIST: list[str] = [
    "player-header", "wf-avatar", "player-real-name", "ge-text-light",
    "wf-card", "player-event-item", "wf-module-item", "m-item",
    "m-item-team", "m-item-team-name", "m-item-team-tag", "m-item-event",
    "m-item-result", "m-item-date", "player-summary-container"
]
EVENT_PAGE_WHITELIST: list[str] = [
    "event-header", "event-desc", "event-desc-item", "wf-title", 
    "event-desc-subtitle", "wf-ptable", "row", "cell", "wf-card", "ge-text"
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
    path = SCRIPT_DIR / "templates" / filename
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

_EVENT_TEMPLATE_CLASSES: Optional[list[str]] = _load_template_whitelist("event_template.html")
if _EVENT_TEMPLATE_CLASSES:
    sys.stderr.write(f"[vlr-server] event_template.html loaded ({len(_EVENT_TEMPLATE_CLASSES)} classes)\n")
else:
    sys.stderr.write("[vlr-server] event_template.html not found — using built-in whitelist\n")


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
    Heuristically determine if a resource page is a 'match', 'forum', 'team', 'news', or 'player' type.
    """
    if "player-header" in html or "player-real-name" in html:
        return "player"
    if "article-body" in html or "article-header" in html:
        return "news"
    if "vm-stats" in html or "match-header" in html:
        return "match"
    if "post-body" in html or "thread-header" in html or "post-content" in html:
        return "forum"
    if "team-header" in html or "team-roster-item" in html:
        return "team"
    if "event-header" in html or "wf-ptable" in html:
        return "event"
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
        combined = list(set(whitelist + ["wf-module-item", "team-event-item"]))
        return _whitelist_clean(html, combined)
    elif page_type == "event":
        whitelist = _EVENT_TEMPLATE_CLASSES or EVENT_PAGE_WHITELIST
        return _whitelist_clean(html, whitelist)
    elif page_type == "player":
        return _whitelist_clean(html, PLAYER_PAGE_WHITELIST)
    elif page_type == "news":
        whitelist = _FORUM_TEMPLATE_CLASSES or NEWS_PAGE_WHITELIST
        # Fallback to FORUM template classes if present, otherwise use strict news whitelist
        # We append news-specific classes ensuring they aren't stripped if using forum template
        combined = list(set(whitelist + NEWS_PAGE_WHITELIST))
        return _whitelist_clean(html, combined)
    else:
        return _fallback_clean(html)


async def _fetch(url: str) -> str:
    """Perform a GET request scoped strictly to vlr.gg."""
    if not url.startswith(BASE_URL):
        raise PermissionError(f"Request blocked: URL {url!r} is outside vlr.gg scope.")
        
    sys.stderr.write(f"[vlr-server] Fetching URL: {url}\n")
    sys.stderr.flush()

    if os.environ.get("VLR_SIMULATE_TIMEOUT") == "1":
        sys.stderr.write("[vlr-server] SIMULATING TIMEOUT...\n")
        raise httpx.ConnectTimeout("Simulated connection timeout to vlr.gg")

    if os.environ.get("VLR_SIMULATE_ERROR") == "1":
        sys.stderr.write("[vlr-server] SIMULATING 503 ERROR...\n")
        raise httpx.HTTPStatusError(
            "Simulated 503 Service Unavailable",
            request=httpx.Request("GET", url),
            response=httpx.Response(503, content=b"Server Down")
        )

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


def _parse_search_list(html: str, query: str) -> str:
    """
    Parse a vlr.gg search page into structured lines.
    """
    soup = BeautifulSoup(html, "lxml")
    items = soup.find_all("a", class_=re.compile(r"search-item"))
    lines: list[str] = [f"[VLR Search Results for '{query}']",
                        "Use IDs below with get_vlr_resource (for matches/threads/events) or get_vlr_team / get_vlr_player.",
                        ""]

    for item in items:
        href = item.get("href", "")
        # e.g. /search/r/player/9/idx
        m = re.search(r"/(player|team|event|match)/(\d+)", href)
        if not m:
            continue
        item_type = m.group(1)
        item_id = m.group(2)

        # Title
        title_el = item.find("div", class_=re.compile(r"search-item-title"))
        title = _text(title_el) if title_el else _text(item)

        # Desc
        desc_el = item.find("div", class_=re.compile(r"search-item-desc"))
        desc = _text(desc_el) if desc_el else ""

        parts: list[str] = [f"[{item_type.capitalize()} ID: {item_id}] {title}"]
        if desc:
            parts.append(desc)

        lines.append(" | ".join(parts))

    if len(lines) == 3:
        lines.append("(No results found)")
    return "\n".join(lines)


def _parse_event_list(html: str, page: int) -> str:
    """
    Parse a vlr.gg events page into structured lines.
    """
    soup = BeautifulSoup(html, "lxml")
    items = soup.find_all("a", class_=re.compile(r"event-item"))
    lines: list[str] = [f"[VLR Events — Page {page}]",
                        "Use IDs below with get_vlr_event for full event details.",
                        ""]

    for item in items:
        href = item.get("href", "")
        event_id = _extract_id_from_href(href)
        if not event_id:
            continue

        title_el = item.find("div", class_=re.compile(r"event-item-title"))
        title = _text(title_el) if title_el else "Untitled Event"

        status_el = item.find(class_=re.compile(r"event-item-desc-item-status"))
        status = _text(status_el) if status_el else ""

        prize_el = item.find("div", class_=re.compile(r"mod-prize"))
        prize = _text(prize_el).replace("Prize Pool", "").strip() if prize_el else ""

        dates_el = item.find("div", class_=re.compile(r"mod-dates"))
        dates = _text(dates_el).replace("Dates", "").strip() if dates_el else ""

        parts: list[str] = [f"[ID: {event_id}] {title}"]
        if status:
            parts.append(f"Status: {status}")
        if dates:
            parts.append(f"Dates: {dates}")
        if prize:
            parts.append(f"Prize: {prize}")

        lines.append(" | ".join(parts))

    if len(lines) == 3:
        lines.append("(No events found on this page)")
    return "\n".join(lines)


def _parse_entity_matches_list(html: str, page: int, entity_id: str, is_player: bool = False) -> str:
    """
    Parse a vlr.gg team or player matches page into structured lines.
    """
    soup = BeautifulSoup(html, "lxml")
    items = soup.find_all("a", class_=re.compile(r"m-item"))
    label = "Player" if is_player else "Team"
    lines: list[str] = [f"[VLR {label} {entity_id} Matches — Page {page}]",
                        "Use IDs below with get_vlr_resource for full match details.",
                        ""]

    for item in items:
        if "m-item-games-item" in item.get("class", []):
            continue

        href = item.get("href", "")
        match_id = _extract_id_from_href(href)
        if not match_id:
            continue

        team_els = item.find_all(class_=re.compile(r"m-item-team-name"))
        teams = [_text(t) for t in team_els if _text(t)]
        matchup = " vs ".join(teams) if teams else "TBD vs TBD"

        result_el = item.find(class_=re.compile(r"m-item-result"))
        if result_el:
            spans = result_el.find_all("span")
            scores = [_text(s) for s in spans if _text(s)]
            score_str = "-".join(scores) if scores else ""
        else:
            score_str = ""

        event_el = item.find(class_=re.compile(r"m-item-event"))
        event = " ".join(_text(event_el).split())

        date_el = item.find(class_=re.compile(r"m-item-date"))
        date = _text(date_el)

        parts: list[str] = [f"[ID: {match_id}] {matchup}"]
        if score_str:
            parts.append(f"Score: {score_str}")
        if event:
            parts.append(event)
        if date:
            parts.append(date)

        lines.append(" | ".join(parts))

    if len(lines) == 3:
        lines.append(f"(No matches found on this page)")
    return "\n".join(lines)


# ── Tool Registry ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_matches",
            description=(
                "Browse a paginated list of matches (upcoming, live, or completed) across the entire vlr.gg database. "
                "You can optionally filter by 'status' (upcoming / results), or provide exactly one "
                "of 'team_id', 'player_id', or 'event_id' to list matches for that specific entity. "
                "Returns structured entries with match IDs, scores, events/teams and dates. "
                "IMPORTANT: To investigate a specific match, pass its ID to get_vlr_resource. "
                "NOTE: Players do not have upcoming matches. If using 'player_id', you MUST set 'status' to 'results'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "description": "Pagination page number (default: 1, must be >= 1).",
                        "default": 1,
                        "minimum": 1,
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter matches by status ('upcoming' or 'results'). Works globally and across entity filters.",
                        "enum": ["upcoming", "results"],
                        "default": "upcoming"
                    },
                    "team_id": {
                        "type": "string",
                        "description": "Numeric ID of a team to filter their matches exclusively.",
                    },
                    "player_id": {
                        "type": "string",
                        "description": "Numeric ID of a player to filter their matches exclusively.",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Numeric ID of an event to filter its matches exclusively.",
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
            name="get_vlr_resource",
            description=(
                "Fetch the full content of a specific vlr.gg entity. "
                "Uses reference-based whitelist sanitization to return ultra-clean, "
                "structured text for agent reasoning. "
                "Provide EXACTLY ONE of the id parameters depending on the target type."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "resource_id": {
                        "type": "string",
                        "description": "Numeric ID for a match, forum thread, or news article.",
                    },
                    "team_id": {
                        "type": "string",
                        "description": "Numeric ID for a team profile.",
                    },
                    "player_id": {
                        "type": "string",
                        "description": "Numeric ID for a player profile.",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Numeric ID for an event/tournament overview.",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="search_vlr",
            description=(
                "Search vlr.gg for terms like players, teams, or events. "
                "Returns structured entries with type (Player, Team, Event, Match) and ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search term (e.g. 'tenz').",
                    }
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="list_vlr_events",
            description=(
                "Browse ongoing and upcoming Valorant esports events. "
                "Returns structured entries with event titles, status, dates, and a numeric ID. "
                "IMPORTANT: To read full event details (standings, brackets), pass its ID to get_vlr_resource."
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
    ]


# ── Tool Handlers ─────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "list_matches":
            page = _validate_page(int(arguments.get("page", 1)))
            team_id = arguments.get("team_id")
            player_id = arguments.get("player_id")
            event_id = arguments.get("event_id")
            status = arguments.get("status", "upcoming")

            # Validate exclusive filtering
            entities = [eid for eid in (team_id, player_id, event_id) if eid]
            if len(entities) > 1:
                raise ValueError("You must not provide more than one of team_id, player_id, or event_id.")

            if player_id and status == "upcoming":
                raise ValueError("Players do not have upcoming matches. You MUST set status='results' when filtering by player_id.")

            group_param = "completed" if status == "results" else "upcoming"

            if team_id:
                tid = _validate_resource_id(team_id)
                url = f"{BASE_URL}/team/matches/{tid}/?page={page}&group={group_param}"
                html = await _fetch(url)
                text = _parse_entity_matches_list(html, page, tid, is_player=False)
            elif player_id:
                pid = _validate_resource_id(player_id)
                url = f"{BASE_URL}/player/matches/{pid}/?page={page}&group={group_param}"
                html = await _fetch(url)
                text = _parse_entity_matches_list(html, page, pid, is_player=True)
            elif event_id:
                eid = _validate_resource_id(event_id)
                url = f"{BASE_URL}/event/matches/{eid}/?page={page}&group={group_param}"
                html = await _fetch(url)
                text = _parse_match_list(html, page, f"Event {eid} Matches ({status})")
            else:
                if status == "results":
                    url = f"{BASE_URL}/matches/results/?page={page}"
                    html = await _fetch(url)
                    text = _parse_match_list(html, page, "Results")
                else:
                    url = f"{BASE_URL}/matches/?page={page}"
                    html = await _fetch(url)
                    text = _parse_match_list(html, page, "Upcoming/Live")
            
            return [types.TextContent(type="text", text=f"[Source: {url}]\n" + text)]

        elif name == "list_vlr_threads":
            page = _validate_page(int(arguments.get("page", 1)))
            url = f"{BASE_URL}/threads/?page={page}"
            html = await _fetch(url)
            text = _parse_thread_list(html, page)
            return [types.TextContent(type="text", text=f"[Source: {url}]\n" + text)]

        elif name == "get_vlr_resource":
            resource_id = arguments.get("resource_id", "")
            team_id = arguments.get("team_id", "")
            player_id = arguments.get("player_id", "")
            event_id = arguments.get("event_id", "")

            # Validate exclusive ID parameter
            entities = [eid for eid in (resource_id, team_id, player_id, event_id) if eid]
            if len(entities) != 1:
                raise ValueError("You must provide exactly one of resource_id, team_id, player_id, or event_id.")

            if team_id:
                target_id = _validate_resource_id(team_id)
                url = f"{BASE_URL}/team/{target_id}"
                label = "Team Profile"
            elif player_id:
                target_id = _validate_resource_id(player_id)
                url = f"{BASE_URL}/player/{target_id}"
                label = "Player Profile"
            elif event_id:
                target_id = _validate_resource_id(event_id)
                url = f"{BASE_URL}/event/{target_id}"
                label = "Event Details"
            else:
                target_id = _validate_resource_id(resource_id)
                url = f"{BASE_URL}/{target_id}"
                label = "Resource"

            html = await _fetch(url)
            text = _clean_resource(html, target_id)
            
            # Additional hint text for template status
            template_note = ""
            if event_id and _EVENT_TEMPLATE_CLASSES:
                template_note = " [whitelist from template]"
            elif team_id and _TEAM_TEMPLATE_CLASSES:
                template_note = " [whitelist from template]"
            elif not event_id and not team_id and (_MATCH_TEMPLATE_CLASSES or _FORUM_TEMPLATE_CLASSES):
                template_note = " [whitelist from template]"
                
            header = f"[Source: {url}]\n[VLR {label} — ID {target_id}{template_note}]\n\n"
            return [types.TextContent(type="text", text=header + text)]

        elif name == "search_vlr":
            query = arguments.get("query", "").strip()
            if not query:
                raise ValueError("Search query cannot be empty.")
            url = f"{BASE_URL}/search/?q={urllib.parse.quote(query)}"
            html = await _fetch(url)
            text = _parse_search_list(html, query)
            return [types.TextContent(type="text", text=f"[Source: {url}]\n" + text)]

        elif name == "list_vlr_events":
            page = _validate_page(int(arguments.get("page", 1)))
            url = f"{BASE_URL}/events/?page={page}"
            html = await _fetch(url)
            text = _parse_event_list(html, page)
            return [types.TextContent(type="text", text=f"[Source: {url}]\n" + text)]

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
