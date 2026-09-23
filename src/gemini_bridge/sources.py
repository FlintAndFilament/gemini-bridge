"""
gemini_bridge/sources.py
--------------------------
The web sources behind a web-grounded answer: resolving Google's grounding redirects to real
page URLs, and the footer listing them (#80, #82).

Responsibilities:
  - Resolve grounding redirect links to their destination with one HEAD request each
  - Render the footer appended to the reply and the transcript

Design notes:
  - Search sources arrive as vertexaisearch grounding-api-redirect links. A HEAD request
    that does not follow redirects returns a 302 whose Location is the real page, so
    resolution costs no Gemini tokens and downloads no page body
  - Only grounding redirect links are requested. Other URIs (url_context fetches) are
    already real, and requesting arbitrary URLs from model output is not this module's job
  - Every failure degrades per source: a timeout, error, or non-redirect answer keeps that
    source's redirect link, and the call never fails because of a source

Used by:  tools/base.py (call_gemini)
Imports:  httpx, tool_loop.py (ToolCallRecord)
"""

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping
from typing import Optional
from urllib.parse import urlsplit

import httpx

from gemini_bridge.tool_loop import ToolCallRecord

_log = logging.getLogger(__name__)

# Sources listed in the reply; the transcript lists every one.
MAX_SOURCES = 30
RESOLVE_TIMEOUT_SECONDS = 3.0

_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_REDIRECT_PATH = "/grounding-api-redirect/"

TYPED_URL_CAUTION = (
    "[gemini-bridge] The answer above contains links Gemini typed itself. Gemini is asked not "
    "to, but an explicit request for links overrides that, and the URLs it types are often "
    "plausible and wrong. Trust the recorded list below instead."
)

# A URL Gemini typed into its answer, as opposed to one the bridge recorded (#92).
_TYPED_URL = re.compile(r"https?://\S", re.IGNORECASE)

FOOTER_HEADING = (
    "[gemini-bridge] Sources the bridge recorded from Google's grounding metadata (these are "
    "not typed by Gemini):"
)


def is_grounding_redirect(uri: str) -> bool:
    parts = urlsplit(uri)
    return (
        parts.scheme == "https"
        and parts.hostname == _REDIRECT_HOST
        and parts.path.startswith(_REDIRECT_PATH)
    )


def _display_name(title: str, url: str) -> str:
    """What to call a source in the footer (#90).

    Google omits `title` on some grounding chunks, and an older record may carry the redirect
    link as its name. Either way the resolved URL's hostname is the readable name; an
    unresolved source can only fall back to the redirect's own host.
    """
    if title and not is_grounding_redirect(title):
        return title
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


async def _resolve_one(client: httpx.AsyncClient, uri: str) -> Optional[str]:
    try:
        response = await client.head(uri)
    # InvalidURL: httpx pre-builds the next request from a malformed Location even when
    # redirects are not followed, and it is not an HTTPError.
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        _log.info("grounding redirect not resolved (%s): %s", type(exc).__name__, uri)
        return None
    location = str(response.headers.get("location", ""))
    if response.is_redirect and urlsplit(location).scheme in ("http", "https"):
        return location
    _log.info("grounding redirect not resolved (HTTP %s): %s", response.status_code, uri)
    return None


async def resolve_redirects(
    uris: Iterable[str],
    *,
    timeout: float = RESOLVE_TIMEOUT_SECONDS,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> dict[str, str]:
    """{redirect: real URL} for every grounding redirect in `uris` that resolved.

    All requests run in parallel, each bounded by `timeout`. Anything unresolved is simply
    absent from the result. `transport` is for tests.
    """
    targets = list(dict.fromkeys(u for u in uris if is_grounding_redirect(u)))
    if not targets:
        return {}
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout, transport=transport
    ) as client:
        results = await asyncio.gather(*(_resolve_one(client, u) for u in targets))
    return {uri: real for uri, real in zip(targets, results) if real}


def record_uris(records: Iterable[ToolCallRecord]) -> list[str]:
    return [uri for record in records for _, uri in record.sources if uri]


def sources_footer(
    records: Iterable[ToolCallRecord],
    resolved: Optional[Mapping[str, str]] = None,
    limit: Optional[int] = MAX_SOURCES,
    answer: Optional[str] = None,
) -> str:
    """The web sources behind an answer, for the reply and the transcript (#80, #82).

    Each source shows its real URL when `resolved` has one, else the link as recorded.
    Sources are deduplicated by final URL. Empty when the call used no web source.
    `limit=None` lists every source, for the transcript.

    When `answer` holds URLs, the footer opens with TYPED_URL_CAUTION: those links came from
    the model, not from the grounding metadata, and may be fabricated (#92). The answer itself
    is never rewritten — stripping URLs would mangle code blocks and quoted text.
    """
    resolved = resolved or {}
    seen: dict[str, str] = {}
    for record in records:
        for title, uri in record.sources:
            url = resolved.get(uri, uri)
            seen.setdefault(url or title, title)
    if not seen:
        return ""
    lines = [TYPED_URL_CAUTION] if answer and _TYPED_URL.search(answer) else []
    lines.append(FOOTER_HEADING)
    shown = list(seen.items()) if limit is None else list(seen.items())[:limit]
    for i, (url, title) in enumerate(shown, 1):
        note = " (unresolved Google redirect)" if is_grounding_redirect(url) else ""
        name = _display_name(title, url)
        lines.append(f"{i}. {name} — {url}{note}" if name and name != url else f"{i}. {url}{note}")
    if len(seen) > len(shown):
        lines.append(f"… and {len(seen) - len(shown)} more (see the transcript)")
    return "\n".join(lines)
