"""Tests for grounding-redirect resolution and the sources footer (#82)."""

import httpx
import pytest

from gemini_bridge.sources import (
    FOOTER_HEADING,
    resolve_redirects,
    sources_footer,
)
from gemini_bridge.tool_loop import ToolCallRecord

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIabc"
REDIRECT_2 = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIdef"
REAL = "https://ai.google.dev/gemini-api/docs/models/gemini-3.1-pro-preview"


def _transport(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


class TestResolveRedirects:
    async def test_302_location_becomes_the_real_url(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(302, headers={"location": REAL})

        assert await resolve_redirects([REDIRECT], transport=_transport(handler)) == {
            REDIRECT: REAL
        }
        assert seen[0].method == "HEAD"  # no page body is downloaded

    async def test_redirect_is_not_followed(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": REAL})

        await resolve_redirects([REDIRECT], transport=_transport(handler))
        assert calls == [REDIRECT]

    async def test_timeout_leaves_that_source_unresolved(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == REDIRECT:
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(302, headers={"location": REAL})

        got = await resolve_redirects([REDIRECT, REDIRECT_2], transport=_transport(handler))
        assert got == {REDIRECT_2: REAL}

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200),
            httpx.Response(404),
            httpx.Response(302),  # no Location
            httpx.Response(302, headers={"location": "javascript:alert(1)"}),
        ],
    )
    async def test_non_redirect_answer_leaves_it_unresolved(self, response: httpx.Response) -> None:
        got = await resolve_redirects([REDIRECT], transport=_transport(lambda r: response))
        assert got == {}

    async def test_only_grounding_redirects_are_requested(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": REAL})

        await resolve_redirects(
            [
                "https://docs.test/page",
                "http://vertexaisearch.cloud.google.com/grounding-api-redirect/x",
                "https://evil.test/grounding-api-redirect/x",
                REDIRECT,
            ],
            transport=_transport(handler),
        )
        assert calls == [REDIRECT]

    async def test_duplicates_are_requested_once(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": REAL})

        await resolve_redirects([REDIRECT, REDIRECT], transport=_transport(handler))
        assert calls == [REDIRECT]

    async def test_nothing_to_resolve_opens_no_client(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        def boom(*a, **k):  # type: ignore[no-untyped-def]
            raise AssertionError("client opened")

        monkeypatch.setattr(httpx, "AsyncClient", boom)
        assert await resolve_redirects(["https://docs.test/page"]) == {}


def _search(*sources: tuple[str, str]) -> ToolCallRecord:
    return ToolCallRecord("google_search", {"query": "q"}, True, "", tuple(sources))


class TestFooter:
    def test_heading_says_recorded_not_typed(self) -> None:
        text = sources_footer([_search(("ai.google.dev", REDIRECT))], {REDIRECT: REAL})
        assert text.splitlines()[0] == FOOTER_HEADING
        assert "not typed by Gemini" in FOOTER_HEADING

    def test_resolved_source_shows_the_real_url(self) -> None:
        text = sources_footer([_search(("ai.google.dev", REDIRECT))], {REDIRECT: REAL})
        assert f"1. ai.google.dev — {REAL}" in text
        assert REDIRECT not in text

    def test_unresolved_source_falls_back_to_its_redirect_alone(self) -> None:
        text = sources_footer(
            [_search(("a.test", REDIRECT), ("b.test", REDIRECT_2))], {REDIRECT_2: REAL}
        )
        assert f"a.test — {REDIRECT} (unresolved Google redirect)" in text
        assert f"b.test — {REAL}" in text
        assert "unresolved" not in text.splitlines()[2]

    def test_two_redirects_to_one_page_list_it_once(self) -> None:
        text = sources_footer(
            [_search(("a", REDIRECT), ("a", REDIRECT_2))], {REDIRECT: REAL, REDIRECT_2: REAL}
        )
        assert text.count(REAL) == 1

    def test_limit_none_lists_everything(self) -> None:
        many = [(f"s{i}", f"https://s{i}.test") for i in range(50)]
        assert "more" not in sources_footer([_search(*many)], limit=None)
        assert "https://s49.test" in sources_footer([_search(*many)], limit=None)

    def test_cap_is_raised_past_ten(self) -> None:
        from gemini_bridge.sources import MAX_SOURCES

        assert MAX_SOURCES >= 25


class TestSourceNames:
    """A source with no title must not be named after the opaque redirect link (#90)."""

    PYTHON = "https://www.python.org/downloads/"

    def test_titleless_source_is_named_after_the_resolved_host(self) -> None:
        text = sources_footer([_search(("", REDIRECT))], {REDIRECT: self.PYTHON})
        assert f"1. python.org — {self.PYTHON}" in text

    def test_name_that_is_itself_a_redirect_is_replaced(self) -> None:
        text = sources_footer([_search((REDIRECT, REDIRECT))], {REDIRECT: self.PYTHON})
        assert f"1. python.org — {self.PYTHON}" in text
        assert "vertexaisearch" not in text

    def test_unresolved_titleless_source_falls_back_to_the_redirect_host(self) -> None:
        text = sources_footer([_search(("", REDIRECT))], {})
        assert f"1. vertexaisearch.cloud.google.com — {REDIRECT} (unresolved" in text

    def test_a_real_title_is_left_alone(self) -> None:
        text = sources_footer([_search(("Python Downloads", REDIRECT))], {REDIRECT: self.PYTHON})
        assert f"1. Python Downloads — {self.PYTHON}" in text
