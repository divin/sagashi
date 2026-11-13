"""Unit tests for Sagashi."""

from __future__ import annotations

from datetime import UTC

import httpx
import pytest
from pytest_httpx import HTTPXMock

from sagashi.config import ConfigError, load_config
from sagashi.fetcher import is_blocked_url, is_reddit_url
from sagashi.providers.uruky import UrukyProvider
from sagashi.reddit import RedditError
from sagashi.rerank import lexical_score, rerank, rerank_results
from sagashi.reranker_client import RerankerClient, RerankerClientError


class FakeTransport:
    """Duck-typed transport recording the last request."""

    def __init__(
        self,
        payload: dict[str, object] | None = None,
        status_code: int = 200,
        exception: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.exception = exception
        self.last_request: dict[str, object] = {}
        self.last_headers: dict[str, str] = {}

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self.last_request = {"url": url, "json": json}
        self.last_headers = dict(headers or {})
        if self.exception is not None:
            raise self.exception
        if self.status_code != 200:
            return httpx.Response(
                status_code=self.status_code, text="reranker unavailable"
            )
        return httpx.Response(status_code=200, json=self.payload or {})


class TestConfig:
    """Tests for configuration loading and validation."""

    def test_valid_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that valid config loads without error."""
        monkeypatch.setenv("URUKY_TOKEN", "test-token")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "test-id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "test-agent")
        monkeypatch.setenv("MCP_SEARCH_AUTH_TOKEN", "auth-token")

        config = load_config()
        config.validate()

        assert config.uruky_token == "test-token"
        assert config.reddit_client_id == "test-id"
        assert config.reddit_client_secret == "test-secret"
        assert config.reddit_user_agent == "test-agent"
        assert config.mcp_auth_token == "auth-token"
        assert config.fetch_max_chars == 40000

    def test_missing_uruky_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing Uruky token raises ConfigError."""
        monkeypatch.setenv("URUKY_TOKEN", "")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "test-id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "test-agent")

        config = load_config()
        with pytest.raises(ConfigError, match="URUKY_TOKEN is required"):
            config.validate()

    def test_missing_reddit_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing Reddit credentials raises ConfigError."""
        monkeypatch.setenv("URUKY_TOKEN", "test-token")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "")
        monkeypatch.setenv("REDDIT_USER_AGENT", "")

        config = load_config()
        with pytest.raises(
            ConfigError,
            match="missing: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT",
        ):
            config.validate()

    def test_optional_mcp_auth_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that MCP_SEARCH_AUTH_TOKEN is optional."""
        monkeypatch.setenv("URUKY_TOKEN", "test-token")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "test-id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "test-agent")

        config = load_config()
        config.validate()

        # mcp_auth_token is None when not set, empty string when set to empty value
        assert config.mcp_auth_token is None or config.mcp_auth_token == ""


class TestIsRedditUrl:
    """Tests for Reddit URL detection."""

    def test_reddit_com_url(self) -> None:
        """Test detection of reddit.com URLs."""
        assert is_reddit_url("https://www.reddit.com/r/learnpython/comments/abc123/")
        assert is_reddit_url("https://www.reddit.com/r/programming/")

    def test_redd_it_short_url(self) -> None:
        """Test detection of redd.it short URLs."""
        assert is_reddit_url("https://redd.it/abc123")
        assert is_reddit_url("https://redd.it/xyz789/")

    def test_non_reddit_url(self) -> None:
        """Test that non-Reddit URLs are not detected."""
        assert not is_reddit_url("https://example.com")
        assert not is_reddit_url("https://github.com/user/repo")
        assert not is_reddit_url("https://news.ycombinator.com")


class TestIsBlockedUrl:
    """Tests for crawler blocklist detection."""

    def test_blocked_reddit_com(self) -> None:
        """Test that reddit.com is blocked."""
        assert is_blocked_url("https://www.reddit.com")
        assert is_blocked_url("https://www.reddit.com/r/learnpython/")

    def test_not_blocked_redd_it(self) -> None:
        """Test that redd.it is not blocked (goes to Reddit module)."""
        assert not is_blocked_url("https://redd.it/abc123")


class TestExtractSubmissionId:
    """Tests for Reddit submission ID extraction."""

    def test_reddit_com_thread_url(self) -> None:
        """Thread permalink yields the thread id."""
        from sagashi.reddit import _extract_submission_id

        url = "https://reddit.com/r/LocalLLaMA/comments/1xppbht/we_are_in_the_golden/"
        assert _extract_submission_id(url) == "1xppbht"

    def test_reddit_com_comment_permalink(self) -> None:
        """A comment permalink still yields the thread id, not the comment id."""
        from sagashi.reddit import _extract_submission_id

        url = "https://www.reddit.com/r/LocalLLaMA/comments/1xppbht/slug/abc1234/"
        assert _extract_submission_id(url) == "1xppbht"

    def test_redd_it_short_url(self) -> None:
        """redd.it short links yield the slug."""
        from sagashi.reddit import _extract_submission_id

        assert _extract_submission_id("https://redd.it/1xppbht") == "1xppbht"

    def test_trailing_slash_and_query(self) -> None:
        """Trailing slashes and query strings do not break extraction."""
        from sagashi.reddit import _extract_submission_id

        url = "https://www.reddit.com/r/x/comments/1xppbht/?utm_source=share"
        assert _extract_submission_id(url) == "1xppbht"

    def test_empty_url(self) -> None:
        """A URL without an id yields an empty string."""
        from sagashi.reddit import _extract_submission_id

        assert _extract_submission_id("https://www.reddit.com/") == ""


class TestFetchPageRouting:
    """Tests for deterministic Reddit routing inside fetch_page."""

    @pytest.mark.asyncio
    async def test_reddit_com_routes_to_reddit_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reddit.com URLs go to fetch_thread (official API), never the crawler."""
        calls: list[str] = []

        async def fake_fetch_thread(url: str, comment_count: int = 20) -> str:
            calls.append(url)
            return "thread markdown"

        import sagashi.reddit as reddit_module
        from sagashi.fetcher import fetch_page

        monkeypatch.setattr(reddit_module, "fetch_thread", fake_fetch_thread)
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        result = await fetch_page("https://www.reddit.com/r/x/comments/1xppbht/slug/")

        assert result == "thread markdown"
        assert calls == ["https://www.reddit.com/r/x/comments/1xppbht/slug/"]

    @pytest.mark.asyncio
    async def test_non_http_scheme_raises_clean_error(self) -> None:
        """Non-http schemes fail with a short message, no stack trace."""
        from sagashi.fetcher import FetchError, fetch_page

        with pytest.raises(FetchError, match="only http:// and https://"):
            await fetch_page("ftp://example.com/file")


class TestFriendlyError:
    """Tests for crawler error sanitization."""

    def test_dns_error(self) -> None:
        """DNS failures map to a domain typo hint."""
        from sagashi.fetcher import _friendly_error

        message = _friendly_error(
            "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://x.com/"
        )
        assert "domain" in message.lower()

    def test_timeout(self) -> None:
        """Timeouts map to a timeout message."""
        from sagashi.fetcher import _friendly_error

        assert "timed out" in _friendly_error("Navigation timed out after 30s")

    def test_no_trace_leak(self) -> None:
        """Unknown errors are generic: no paths, line numbers, or code context."""
        from sagashi.fetcher import _friendly_error

        message = _friendly_error(
            "Unexpected error in _crawl_web at line 779 in _crawl_web "
            "(.venv/lib/python3.12/site-packages/crawl4ai/async_crawler_strategy.py)"
        )
        assert ".py" not in message
        assert "line" not in message
        assert "site-packages" not in message


class TestToolParameterValidation:
    """Tests for tool-level parameter validation helpers."""

    def test_recency_valid_values(self) -> None:
        """Documented recency values pass validation."""
        from sagashi.server import _validate_recency

        for value in (None, "day", "week", "month", "year"):
            _validate_recency(value)

    def test_recency_invalid_value(self) -> None:
        """Unknown recency values raise a ToolError listing valid values."""
        from fastmcp.exceptions import ToolError

        from sagashi.server import _validate_recency

        with pytest.raises(ToolError, match="one of: day, week, month, year"):
            _validate_recency("banana")

    def test_language_valid_values(self) -> None:
        """ISO 639-1 codes and Chinese variants pass and are normalized."""
        from sagashi.server import _validate_language

        assert _validate_language(None) is None
        assert _validate_language("ja") == "JA"
        assert _validate_language(" en ") == "EN"
        assert _validate_language("zh-hant") == "ZH-HANT"

    def test_language_invalid_value(self) -> None:
        """Malformed language codes raise a ToolError."""
        from fastmcp.exceptions import ToolError

        from sagashi.server import _validate_language

        with pytest.raises(ToolError, match="Invalid language"):
            _validate_language("not-a-code")

    def test_max_chars_default_is_ceiling(self) -> None:
        """No max_chars means the configured ceiling."""
        from sagashi.server import _effective_max_chars

        assert _effective_max_chars(None, 40000) == 40000

    def test_max_chars_honored_below_ceiling(self) -> None:
        """Requested values below the ceiling are honored, not raised."""
        from sagashi.server import _effective_max_chars

        assert _effective_max_chars(200, 40000) == 200

    def test_max_chars_capped_at_ceiling(self) -> None:
        """Requested values above the ceiling are capped at the ceiling."""
        from sagashi.server import _effective_max_chars

        assert _effective_max_chars(100000, 40000) == 40000

    def test_max_chars_rejects_non_positive(self) -> None:
        """Zero or negative max_chars raise a ToolError."""
        from fastmcp.exceptions import ToolError

        from sagashi.server import _effective_max_chars

        with pytest.raises(ToolError, match="positive integer"):
            _effective_max_chars(0, 40000)
        with pytest.raises(ToolError, match="positive integer"):
            _effective_max_chars(-5, 40000)


class TestStaticTokenVerifier:
    """Tests for optional bearer-token auth."""

    @pytest.mark.asyncio
    async def test_correct_token_accepted(self) -> None:
        """The configured token verifies."""
        from sagashi.auth import StaticTokenVerifier

        verifier = StaticTokenVerifier("secret-token")
        access = await verifier.verify_token("secret-token")
        assert access is not None
        assert access.client_id == "sagashi-client"

    @pytest.mark.asyncio
    async def test_wrong_token_rejected(self) -> None:
        """A wrong token does not verify."""
        from sagashi.auth import StaticTokenVerifier

        verifier = StaticTokenVerifier("secret-token")
        assert await verifier.verify_token("wrong-token") is None
        assert await verifier.verify_token("") is None

    def test_build_server_with_token_sets_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A configured token wires a verifier into the server."""
        from sagashi.server import build_server

        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")
        monkeypatch.setenv("MCP_SEARCH_AUTH_TOKEN", "secret-token")

        server = build_server()
        assert server.auth is not None

    def test_build_server_without_token_is_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No token configured means no auth (trusted-LAN mode)."""
        from sagashi.server import build_server

        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")
        monkeypatch.delenv("MCP_SEARCH_AUTH_TOKEN", raising=False)

        server = build_server()
        assert server.auth is None

    @pytest.mark.asyncio
    async def test_healthz_is_available_without_bearer_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The operational health route remains unauthenticated."""
        from starlette.testclient import TestClient

        from sagashi.server import build_server

        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")
        monkeypatch.setenv("MCP_SEARCH_AUTH_TOKEN", "secret-token")

        app = build_server().http_app(path="/mcp", transport="streamable-http")
        with TestClient(app) as client:
            response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "service": "sagashi"}

    @pytest.mark.asyncio
    async def test_http_app_enforces_bearer_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With a token set, requests without it get 401; with it, they pass."""
        from starlette.testclient import TestClient

        from sagashi.server import build_server

        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")
        monkeypatch.setenv("MCP_SEARCH_AUTH_TOKEN", "secret-token")

        app = build_server().http_app(path="/mcp", transport="streamable-http")
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        with TestClient(app) as client:
            no_auth = client.post("/mcp", json=body)
            assert no_auth.status_code == 401

            bad_auth = client.post(
                "/mcp",
                json=body,
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert bad_auth.status_code == 401

            good_auth = client.post(
                "/mcp",
                json=body,
                headers={
                    "Authorization": "Bearer secret-token",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert good_auth.status_code == 200


class TestUrukyProvider:
    """Tests for Uruky search provider."""

    @pytest.mark.asyncio
    async def test_success(self, httpx_mock: HTTPXMock) -> None:
        """Test successful search."""
        httpx_mock.add_response(
            url="https://uruky.com/search?q=test+query&f=json&i=test-token",
            json={
                "results": [
                    {
                        "url": "https://example.com",
                        "title": "Example Title",
                        "description": "Example snippet",
                        "date": "2024-01-01",
                    }
                ]
            },
        )

        provider = UrukyProvider(token="test-token")
        results = await provider.search("test query")

        assert len(results) == 1
        assert results[0].url == "https://example.com"
        assert results[0].title == "Example Title"
        assert results[0].snippet == "Example snippet"
        assert results[0].date == "2024-01-01"

    @pytest.mark.asyncio
    async def test_auth_failure(self, httpx_mock: HTTPXMock) -> None:
        """Test that auth failure raises an error."""
        httpx_mock.add_response(
            url="https://uruky.com/search?q=test+query&f=json&i=invalid-token",
            status_code=401,
            text="Unauthorized",
        )

        provider = UrukyProvider(token="invalid-token")
        with pytest.raises(httpx.HTTPStatusError):
            await provider.search("test query")

    @pytest.mark.asyncio
    async def test_malformed_json(self, httpx_mock: HTTPXMock) -> None:
        """Test that malformed JSON is handled gracefully."""
        httpx_mock.add_response(
            url="https://uruky.com/search?q=test+query&f=json&i=test-token",
            text="not valid json",
            headers={"Content-Type": "application/json"},
        )

        provider = UrukyProvider(token="test-token")
        results = await provider.search("test query")

        # Should return empty list for malformed JSON
        assert results == []

    @pytest.mark.asyncio
    async def test_no_results(self, httpx_mock: HTTPXMock) -> None:
        """Test search with no results."""
        httpx_mock.add_response(
            url="https://uruky.com/search?q=test+query&f=json&i=test-token",
            json={"results": []},
        )

        provider = UrukyProvider(token="test-token")
        results = await provider.search("test query")

        assert results == []

    @pytest.mark.asyncio
    async def test_recency_and_language_params(self, httpx_mock: HTTPXMock) -> None:
        """Recency maps to Uruky's single-letter `w` codes; language is uppercased."""
        httpx_mock.add_response(
            url="https://uruky.com/search?q=test&f=json&i=test-token&w=m&l=JA",
            json={"results": []},
        )

        provider = UrukyProvider(token="test-token")
        results = await provider.search("test", recency="month", language="ja")

        assert results == []


class TestRedditError:
    """Tests for Reddit-specific errors."""

    def test_error_message(self) -> None:
        """Test that RedditError has a user-friendly message."""
        error = RedditError("Test error message")
        assert str(error) == "Test error message"


class TestRerank:
    """Tests for lexical reranking."""

    def test_title_match_scores_higher_than_body_match(self) -> None:
        """A query term in the title scores double one in the body."""
        title_hit = lexical_score("keyboard", "Best Keyboard Guide", "nothing here")
        body_hit = lexical_score("keyboard", "Some Guide", "keyboard reviews")
        assert title_hit == pytest.approx(1.0)
        assert body_hit == pytest.approx(0.5)

    def test_partial_match_scores_proportionally(self) -> None:
        """Score is normalized by the number of query tokens."""
        score = lexical_score(
            "mechanical keyboard", "Keyboard Guide", "mechanical tips"
        )
        # one title hit (keyboard=2.0) + one body hit (mechanical=1.0) / 4.0
        assert score == pytest.approx(0.75)

    def test_no_match_scores_zero(self) -> None:
        """A result matching nothing scores 0."""
        assert lexical_score("keyboard", "Cooking", "recipes") == 0.0

    def test_empty_query_scores_zero(self) -> None:
        """An empty query yields score 0, not a division error."""
        assert lexical_score("", "anything", "anything") == 0.0

    def test_rerank_sorts_by_relevance(self) -> None:
        """Results with lexical matches are boosted above non-matches."""
        results = [
            {"title": "Cooking pasta", "body": "delicious recipes"},
            {"title": "Keyboard review", "body": "mechanical switches tested"},
            {"title": "Best keyboards", "body": "unrelated"},
        ]
        ranked = rerank(
            "mechanical keyboard",
            results,
            title_of=lambda r: r["title"],
            body_of=lambda r: r["body"],
        )
        assert ranked[0]["title"] == "Keyboard review"
        assert ranked[1]["title"] == "Best keyboards"
        assert ranked[2]["title"] == "Cooking pasta"

    def test_plural_normalization(self) -> None:
        """Plurals match their singular form symmetrically."""
        assert lexical_score("keyboard", "Best Keyboards", "") == pytest.approx(1.0)
        assert lexical_score("keyboards", "Best Keyboard", "") == pytest.approx(1.0)

    def test_cjk_title_match(self) -> None:
        """CJK query terms match titles as substrings."""
        # "日本の" has its edge particle stripped, so "日本" matches.
        score = lexical_score("日本のGDP", "日本の経済", "GDP は...")
        # 日本 title hit (2.0) + gdp body hit (1.0) / (2.0 * (1 + 1))
        assert score == pytest.approx(0.75)

    def test_cjk_body_match(self) -> None:
        """CJK query terms matching only the body score half."""
        score = lexical_score("順位", "世界の経済", "GDPランキングの順位について")
        # 順位 body hit (1.0) / 2.0
        assert score == pytest.approx(0.5)

    def test_cjk_no_match_scores_zero(self) -> None:
        """A result matching no CJK term scores 0."""
        assert lexical_score("日本のGDP", "フランス料理", "パリのレストラン") == 0.0

    def test_cjk_rerank_boosts_relevant(self) -> None:
        """Relevant Japanese results rank above junk matching only one term."""
        results = [
            {"title": "日本", "body": "日本の国についての基本情報"},
            {"title": "世界GDPランキング 2025", "body": "日本は3位、GDP統計と順位"},
            {"title": "日本のアンテナ会社", "body": "暮らしと社会インフラ"},
        ]
        ranked = rerank(
            "日本のGDP 2025年 順位",
            results,
            title_of=lambda r: r["title"],
            body_of=lambda r: r["body"],
        )
        assert ranked[0]["title"] == "世界GDPランキング 2025"

    def test_rerank_is_stable_on_ties(self) -> None:
        """Equal scores keep the provider's original order."""
        results = ["first", "second", "third"]
        ranked = rerank(
            "query",
            results,
            title_of=lambda r: r,
            body_of=lambda r: "",
        )
        assert ranked == ["first", "second", "third"]


class TestRerankerClient:
    """Tests for the llama-server /v1/rerank client."""

    URL = "http://reranker.test:9093/v1/rerank"

    @staticmethod
    def _client(
        transport: object, token: str | None = None, timeout: float = 120.0
    ) -> RerankerClient:
        return RerankerClient(
            TestRerankerClient.URL, token, transport=transport, timeout=timeout
        )

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """A well-formed response returns one result per document."""
        transport = FakeTransport(
            payload={
                "results": [
                    {"index": 2, "relevance_score": -1.0},
                    {"index": 0, "relevance_score": -5.6},
                    {"index": 1, "relevance_score": 0.4},
                ]
            }
        )
        results = await self._client(transport).rerank("q", ["a", "b", "c"])
        assert [(r.index, r.score) for r in results] == [
            (2, -1.0),
            (0, -5.6),
            (1, 0.4),
        ]

    @pytest.mark.asyncio
    async def test_request_body_and_bearer_token(self) -> None:
        """The body carries model/query/documents; a token sets the header."""
        transport = FakeTransport(
            payload={"results": [{"index": 0, "relevance_score": 1.0}]}
        )
        await self._client(transport, token="secret").rerank("best keyboard", ["doc"])
        assert transport.last_request["url"] == self.URL
        assert transport.last_headers == {"Authorization": "Bearer secret"}
        assert transport.last_request["json"] == {
            "model": "reranker",
            "query": "best keyboard",
            "documents": ["doc"],
        }

    @pytest.mark.asyncio
    async def test_missing_results_key(self) -> None:
        """A response without a results list is an integrity failure."""
        with pytest.raises(RerankerClientError, match="missing 'results'"):
            await self._client(FakeTransport(payload={"unexpected": True})).rerank(
                "q", ["a"]
            )

    @pytest.mark.asyncio
    async def test_duplicate_index_rejected(self) -> None:
        """Duplicate indices are an integrity failure, not a silent drop."""
        transport = FakeTransport(
            payload={
                "results": [
                    {"index": 0, "relevance_score": 1.0},
                    {"index": 0, "relevance_score": 2.0},
                ]
            }
        )
        with pytest.raises(RerankerClientError, match="duplicate index 0"):
            await self._client(transport).rerank("q", ["a", "b"])

    @pytest.mark.asyncio
    async def test_wrong_result_count_rejected(self) -> None:
        """Fewer results than documents is an integrity failure."""
        transport = FakeTransport(
            payload={"results": [{"index": 0, "relevance_score": 1.0}]}
        )
        with pytest.raises(RerankerClientError, match="expected 2 results, got 1"):
            await self._client(transport).rerank("q", ["a", "b"])

    @pytest.mark.asyncio
    async def test_non_numeric_score_rejected(self) -> None:
        """A non-numeric relevance_score is rejected."""
        transport = FakeTransport(
            payload={"results": [{"index": 0, "relevance_score": "high"}]}
        )
        with pytest.raises(RerankerClientError, match="non-numeric relevance_score"):
            await self._client(transport).rerank("q", ["a"])

    @pytest.mark.asyncio
    async def test_http_error_propagates(self) -> None:
        """Transport failures raise RerankerClientError."""
        transport = FakeTransport(exception=httpx.ConnectError("connection refused"))
        with pytest.raises(RerankerClientError, match="rerank request failed"):
            await self._client(transport).rerank("q", ["a"])

    @pytest.mark.asyncio
    async def test_http_500_propagates(self) -> None:
        """Non-200 responses raise RerankerClientError."""
        transport = FakeTransport(status_code=500)
        with pytest.raises(RerankerClientError, match="HTTP 500"):
            await self._client(transport).rerank("q", ["a"])


class TestRerankResults:
    """Tests for the endpoint-vs-lexical rerank dispatcher."""

    URL = "http://reranker.test:9093/v1/rerank"

    @pytest.mark.asyncio
    async def test_no_url_falls_back_to_lexical(self) -> None:
        """Without a reranker URL the lexical scorer is used."""
        results = ["Cooking pasta", "Keyboard review"]
        ranked = await rerank_results(
            "keyboard",
            results,
            title_of=lambda r: r,
            body_of=lambda r: "",
            reranker_url=None,
        )
        assert ranked == ["Keyboard review", "Cooking pasta"]

    @pytest.mark.asyncio
    async def test_endpoint_orders_by_relevance_score(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """With a URL, results are ordered by the endpoint's scores."""
        httpx_mock.add_response(
            url=self.URL,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": -3.2},
                    {"index": 2, "relevance_score": 0.9},
                ]
            },
        )
        results = ["first", "second", "third"]
        ranked = await rerank_results(
            "q",
            results,
            title_of=lambda r: r,
            body_of=lambda r: "",
            reranker_url=self.URL,
        )
        # second/third tie at 0.9 — stable sort keeps provider order
        assert ranked == ["second", "third", "first"]

    @pytest.mark.asyncio
    async def test_endpoint_receives_title_and_body_documents(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Each document is the title and body joined with a blank line."""
        httpx_mock.add_response(
            url=self.URL, json={"results": [{"index": 0, "relevance_score": 0.0}]}
        )
        await rerank_results(
            "q",
            ["only"],
            title_of=lambda r: r,
            body_of=lambda r: "",
            reranker_url=self.URL,
        )
        sent = httpx_mock.get_requests()[-1]
        assert b'"documents":["only\\n\\n"]' in sent.read()

    @pytest.mark.asyncio
    async def test_endpoint_error_propagates(self, httpx_mock: HTTPXMock) -> None:
        """Reranker failures propagate — no silent fallback to lexical."""
        httpx_mock.add_response(url=self.URL, status_code=500, text="boom")
        with pytest.raises(RerankerClientError):
            await rerank_results(
                "q",
                ["only"],
                title_of=lambda r: r,
                body_of=lambda r: "",
                reranker_url=self.URL,
            )


class TestRedditParameterClamping:
    """Tests for tool parameter clamping."""

    def test_clamp_within_bounds(self) -> None:
        """Values inside bounds pass through."""
        from sagashi.reddit import _clamp

        assert _clamp(10, 1, 25) == 10

    def test_clamp_respects_bounds(self) -> None:
        """Out-of-range values are clamped."""
        from sagashi.reddit import _clamp

        assert _clamp(0, 1, 25) == 1
        assert _clamp(100, 1, 25) == 25


class TestSearchRedditSubredditParameter:
    """Tests for search_reddit with optional subreddit parameter."""

    @pytest.mark.asyncio
    async def test_default_subreddit_is_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default behavior (no subreddit param) searches r/all."""
        calls: list[str] = []

        class FakeSubreddit:
            def __init__(self, name: str) -> None:
                calls.append(name)
                self.name = name

            async def search(
                self,
                query: str,
                sort: str,
                time_filter: str,
                limit: int,
            ):
                # Return empty generator
                return
                yield

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit(name)

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import search_reddit

        await search_reddit("test query")

        # Should have called subreddit("all")
        assert calls == ["all"]

    @pytest.mark.asyncio
    async def test_custom_subreddit_search(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Custom subreddit parameter searches that subreddit."""
        calls: list[str] = []

        class FakeSubreddit:
            def __init__(self, name: str) -> None:
                calls.append(name)
                self.name = name

            async def search(
                self,
                query: str,
                sort: str,
                time_filter: str,
                limit: int,
            ):
                return
                yield

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit(name)

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import search_reddit

        await search_reddit("test query", subreddit="learnpython")

        # Should have called subreddit("learnpython")
        assert calls == ["learnpython"]

    @pytest.mark.asyncio
    async def test_subreddit_with_r_prefix_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Subreddit with r/ prefix should work."""
        calls: list[str] = []

        class FakeSubreddit:
            def __init__(self, name: str) -> None:
                calls.append(name)
                self.name = name

            async def search(
                self,
                query: str,
                sort: str,
                time_filter: str,
                limit: int,
            ):
                return
                yield

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit(name)

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import search_reddit

        # asyncpraw accepts r/ prefix, so it should work
        await search_reddit("test query", subreddit="r/learnpython")

        # asyncpraw handles r/ prefix, so it passes through
        assert "r/learnpython" in calls or "learnpython" in calls


class TestListSubredditPosts:
    """Tests for list_subreddit_posts function."""

    @pytest.mark.asyncio
    async def test_returns_structured_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that list_subreddit_posts returns structured metadata."""
        from datetime import datetime

        class FakeSubreddit:
            def __init__(self, name: str) -> None:
                self.name = name

            async def hot(self, **kwargs: object):
                # Fake post
                class FakePost:
                    id = "abc123"
                    title = "Test Post"
                    score = 42
                    author = type("Author", (), {"name": "user1"})()
                    created_utc = datetime(2024, 1, 1).timestamp()
                    num_comments = 5
                    url = "https://reddit.com/url"
                    permalink = "/r/test/comments/abc123/"
                    selftext = "Test body"

                yield FakePost()

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit(name)

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import list_subreddit_posts

        result = await list_subreddit_posts("test", sort="hot", limit=10)

        assert "posts" in result
        assert "metadata" in result
        assert len(result["posts"]) == 1
        assert result["posts"][0]["id"] == "abc123"
        assert result["posts"][0]["title"] == "Test Post"
        assert result["posts"][0]["score"] == 42
        assert result["posts"][0]["author"] == "user1"
        assert result["metadata"]["subreddit"] == "test"
        assert result["metadata"]["sort"] == "hot"
        assert result["metadata"]["count"] == 1

    @pytest.mark.asyncio
    async def test_time_window_parameters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that before/after parameters are parsed as UTC timestamps."""
        from datetime import datetime

        class FakeSubreddit:
            async def new(self, **kwargs):
                class FakePost:
                    id = "abc123"
                    title = "Test Post"
                    score = 42
                    author = type("Author", (), {"name": "user1"})()
                    created_utc = datetime(2024, 6, 15).timestamp()
                    num_comments = 5
                    url = "https://reddit.com/url"
                    permalink = "/r/test/comments/abc123/"
                    selftext = ""

                yield FakePost()

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit()

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import list_subreddit_posts

        # Test with ISO-8601 strings (naive values must be treated as UTC)
        result = await list_subreddit_posts(
            "test",
            sort="new",
            time_filter="day",
            before="2024-06-16T00:00:00",
            after="2024-06-14T00:00:00",
        )

        # Should return the post since it's within the window (2024-06-14 < 2024-06-15 < 2024-06-16)
        assert len(result["posts"]) == 1

    @pytest.mark.asyncio
    async def test_naive_iso_timestamps_treated_as_utc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Naive ISO strings and explicit-UTC strings must behave identically."""
        from datetime import datetime

        class FakeSubreddit:
            async def new(self, **kwargs):
                class FakePost:
                    id = "abc123"
                    title = "Test Post"
                    score = 42
                    author = type("Author", (), {"name": "user1"})()
                    # 2024-06-15 12:00 UTC
                    created_utc = datetime(2024, 6, 15, 12, 0, tzinfo=UTC).timestamp()
                    num_comments = 5
                    url = "https://reddit.com/url"
                    permalink = "/r/test/comments/abc123/"
                    selftext = ""

                yield FakePost()

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit()

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import list_subreddit_posts

        naive = await list_subreddit_posts(
            "test", sort="new", after="2024-06-15T11:00:00"
        )
        explicit = await list_subreddit_posts(
            "test", sort="new", after="2024-06-15T11:00:00+00:00"
        )

        assert len(naive["posts"]) == 1
        assert len(explicit["posts"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_timestamp_raises_reddit_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed before/after value raises a clear RedditError."""

        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import RedditError, list_subreddit_posts

        with pytest.raises(RedditError, match="invalid timestamp"):
            await list_subreddit_posts("test", after="not-a-date")

    @pytest.mark.asyncio
    async def test_invalid_sort_raises_reddit_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unknown sort value raises a clear RedditError."""
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import RedditError, list_subreddit_posts

        with pytest.raises(RedditError, match="Invalid sort"):
            await list_subreddit_posts("test", sort="gilded")


class TestFetchRedditThreads:
    """Tests for fetch_reddit_threads function."""

    @pytest.mark.asyncio
    async def test_returns_structured_thread_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that fetch_reddit_threads returns structured data with errors."""
        from datetime import datetime

        class FakeSubmission:
            id = "abc123"
            title = "Test Thread"
            score = 100
            created_utc = datetime(2024, 1, 1).timestamp()
            permalink = "/r/test/comments/abc123/"
            selftext = "Self text content"
            num_comments = 10
            author = type("Author", (), {"name": "user1"})()

            class Subreddit:
                display_name = "test"

            subreddit = Subreddit()

            class CommentsManager:
                async def replace_more(self, limit: int | None = None) -> None:
                    pass

                def list(self):
                    class FakeComment:
                        author = type("Author", (), {"name": "commenter"})()
                        score = 50
                        body = "Great comment!"
                        created_utc = datetime(2024, 1, 1).timestamp()

                    return [FakeComment()]

            comments = CommentsManager()

        class FakeReddit:
            async def submission(self, id: str) -> FakeSubmission:
                return FakeSubmission()

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import fetch_reddit_threads

        result = await fetch_reddit_threads(
            ["https://redd.it/abc123"], comment_count=10
        )

        assert "results" in result
        assert "errors" in result
        assert "metadata" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["id"] == "abc123"
        assert result["results"][0]["title"] == "Test Thread"
        assert result["results"][0]["score"] == 100
        assert result["results"][0]["subreddit"] == "test"
        assert len(result["results"][0]["comments"]) == 1
        assert result["metadata"]["success_count"] == 1
        assert result["metadata"]["error_count"] == 0

    @pytest.mark.asyncio
    async def test_per_item_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that failed items return per-item errors."""
        call_count = 0

        class FakeSubmission:
            id = "valid123"
            title = "Valid Thread"
            score = 100
            created_utc = 1704067200.0
            permalink = "/r/test/comments/valid123/"
            selftext = ""
            num_comments = 5
            author = type("Author", (), {"name": "user1"})()

            class Subreddit:
                display_name = "test"

            subreddit = Subreddit()

            class CommentsManager:
                async def replace_more(self, limit: int | None = None) -> None:
                    pass

                def list(self):
                    class FakeComment:
                        author = type("Author", (), {"name": "c1"})()
                        score = 10
                        body = "Comment"
                        created_utc = 1704067200.0

                    return [FakeComment()]

            comments = CommentsManager()

        class SubmissionNotFound(RedditError):
            """Custom exception for testing."""

        class FakeReddit:
            async def submission(self, id: str) -> FakeSubmission:
                nonlocal call_count
                call_count += 1
                if id == "invalid":
                    raise SubmissionNotFound("NotFound")
                return FakeSubmission()

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import fetch_reddit_threads

        result = await fetch_reddit_threads(
            [
                "https://redd.it/valid123",
                "",  # Empty string - no ID to extract
                {"id": "valid123"},
            ],
            comment_count=5,
        )

        assert len(result["results"]) == 2
        assert len(result["errors"]) == 1
        assert result["errors"][0]["index"] == 1
        assert result["metadata"]["total"] == 3
        assert result["metadata"]["success_count"] == 2
        assert result["metadata"]["error_count"] == 1

    @pytest.mark.asyncio
    async def test_handles_dict_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that fetch_reddit_threads handles dict items with id/url keys."""
        received_ids: list[str] = []

        class FakeSubmission:
            def __init__(self, id: str) -> None:
                self.id = id
                received_ids.append(id)
                self.title = f"Title {id}"
                self.score = 10
                self.created_utc = 1704067200.0
                self.permalink = f"/r/test/comments/{id}/"
                self.selftext = ""
                self.num_comments = 2
                self.author = type("Author", (), {"name": "u"})()

                class Subreddit:
                    display_name = "test"

                self.subreddit = Subreddit()

                class CommentsManager:
                    async def replace_more(self, limit: int | None = None) -> None:
                        pass

                    def list(self):
                        class FakeComment:
                            author = type("Author", (), {"name": "c"})()
                            score = 5
                            body = "Comment"
                            created_utc = 1704067200.0

                        return [FakeComment()]

                self.comments = CommentsManager()

        class FakeReddit:
            async def submission(self, id: str) -> FakeSubmission:
                return FakeSubmission(id)

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import fetch_reddit_threads

        result = await fetch_reddit_threads(
            [
                {"url": "https://redd.it/dict123"},
                {"id": "dict456"},
                {"permalink": "https://reddit.com/r/test/comments/dict789/"},
            ],
            comment_count=1,
        )

        assert len(result["results"]) == 3
        assert "dict123" in received_ids
        assert "dict456" in received_ids
        assert "dict789" in received_ids


class TestListSubredditPostsTimeWindow:
    """Tests for list_subreddit_posts UTC time window filtering."""

    @pytest.mark.asyncio
    async def test_filters_by_after_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that posts before 'after' timestamp are excluded."""
        from datetime import datetime

        class FakeSubreddit:
            async def hot(self, **kwargs):
                class FakePost:
                    def __init__(self, ts: float) -> None:
                        self.created_utc = ts
                        self.id = "post1"
                        self.title = "Old Post"
                        self.score = 10
                        self.author = type("Author", (), {"name": "user1"})()
                        self.num_comments = 0
                        self.url = "https://reddit.com/url"
                        self.permalink = "/r/test/comments/post1/"
                        self.selftext = ""

                yield FakePost(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
                yield FakePost(datetime(2024, 6, 1, tzinfo=UTC).timestamp())
                yield FakePost(datetime(2024, 12, 1, tzinfo=UTC).timestamp())

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit()

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import list_subreddit_posts

        # Filter to only posts after 2024-06-01 (exclusive)
        result = await list_subreddit_posts("test", after="2024-06-01")

        # Should only have the Dec 1 post
        assert len(result["posts"]) == 1
        assert result["posts"][0]["id"] == "post1"

    @pytest.mark.asyncio
    async def test_filters_by_before_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that posts on/after 'before' timestamp are excluded."""
        from datetime import datetime

        class FakeSubreddit:
            async def hot(self, **kwargs):
                class FakePost:
                    def __init__(self, ts: float) -> None:
                        self.created_utc = ts
                        self.id = "post1"
                        self.title = "Post"
                        self.score = 10
                        self.author = type("Author", (), {"name": "user1"})()
                        self.num_comments = 0
                        self.url = "https://reddit.com/url"
                        self.permalink = "/r/test/comments/post1/"
                        self.selftext = ""

                yield FakePost(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
                yield FakePost(datetime(2024, 6, 1, tzinfo=UTC).timestamp())
                yield FakePost(datetime(2024, 12, 1, tzinfo=UTC).timestamp())

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit()

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import list_subreddit_posts

        # Filter to only posts before 2024-06-01 (exclusive)
        result = await list_subreddit_posts("test", before="2024-06-01")

        # Should have Jan 1 and Jun 1 posts (Jun 1 is excluded since before is exclusive)
        assert len(result["posts"]) == 1


class TestSubredditPrefixStripping:
    """Tests for subreddit name prefix handling."""

    @pytest.mark.asyncio
    async def test_r_prefix_stripped_correctly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that r/ and r prefixes are stripped."""
        received_names: list[str] = []

        class FakeSubreddit:
            def __init__(self, name: str) -> None:
                received_names.append(name)
                self.name = name

            async def hot(self, **kwargs):
                return
                yield

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit(name)

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import list_subreddit_posts

        # Test various prefix forms
        await list_subreddit_posts("rust")
        await list_subreddit_posts("r/rust")
        await list_subreddit_posts("r/rust")

        # All should result in "rust"
        assert received_names == ["rust", "rust", "rust"]

    @pytest.mark.asyncio
    async def test_r_prefix_in_search_reddit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that search_reddit also strips r/ prefix."""
        received_names: list[str] = []

        class FakeSubreddit:
            def __init__(self, name: str) -> None:
                received_names.append(name)
                self.name = name

            async def search(self, query: str, **kwargs):
                return
                yield

        class FakeReddit:
            async def subreddit(self, name: str) -> FakeSubreddit:
                return FakeSubreddit(name)

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import search_reddit

        await search_reddit("test query", subreddit="r/learnpython")

        assert received_names == ["learnpython"]


class TestFetchRedditThreadsPerItemErrors:
    """Tests for fetch_reddit_threads per-item error handling."""

    @pytest.mark.asyncio
    async def test_continues_on_reddit_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that batch continues even when one item raises RedditError."""
        call_count = 0

        class RedditNotFound(RedditError):
            pass

        class FakeSubmission:
            id = "valid123"
            title = "Valid Thread"
            score = 100
            created_utc = 1704067200.0
            permalink = "/r/test/comments/valid123/"
            selftext = ""
            num_comments = 5
            author = type("Author", (), {"name": "user1"})()

            class Subreddit:
                display_name = "test"

            subreddit = Subreddit()

            class CommentsManager:
                async def replace_more(self, limit: int | None = None) -> None:
                    pass

                def list(self):
                    class FakeComment:
                        author = type("Author", (), {"name": "c1"})()
                        score = 10
                        body = "Comment"
                        created_utc = 1704067200.0

                    return [FakeComment()]

            comments = CommentsManager()

        class FakeReddit:
            async def submission(self, id: str) -> FakeSubmission:
                nonlocal call_count
                call_count += 1
                if id == "invalid":
                    raise RedditNotFound("NotFound")
                return FakeSubmission()

            async def close(self) -> None:
                pass

        import sagashi.reddit as reddit_module

        monkeypatch.setattr(
            reddit_module, "_create_reddit_client", lambda c: FakeReddit()
        )
        monkeypatch.setenv("URUKY_TOKEN", "t")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("REDDIT_USER_AGENT", "agent")

        from sagashi.reddit import fetch_reddit_threads

        result = await fetch_reddit_threads(
            [
                "https://redd.it/valid123",
                "https://redd.it/invalid",  # This will raise RedditError
            ],
            comment_count=5,
        )

        assert len(result["results"]) == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["index"] == 1
        assert "NotFound" in result["errors"][0]["error"]
        assert result["metadata"]["total"] == 2
        assert result["metadata"]["success_count"] == 1
        assert result["metadata"]["error_count"] == 1


class TestToolBackwardCompatibility:
    """Tests for backward compatibility of existing tools."""

    def test_search_reddit_preserves_backward_compatibility(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """search_reddit without subreddit param should work as before."""
        import inspect

        import sagashi.reddit as reddit_module

        # Get the signature of search_reddit
        sig = inspect.signature(reddit_module.search_reddit)
        params = sig.parameters

        # Check that all old parameters still exist and work
        assert "query" in params
        assert "time_filter" in params
        assert "limit" in params
        # The new subreddit parameter should be optional
        assert "subreddit" in params
        assert params["subreddit"].default is None

        # Verify the function can be called with old-style arguments
        async def test_backward_compat():
            from sagashi.reddit import search_reddit

            # This should work without errors (will fail due to no credentials, but that's OK)
            try:
                await search_reddit("test query")
            except RedditError as e:
                # We expect an error about missing credentials, not about parameters
                assert "Reddit API error" in str(e) or "REDDIT_CLIENT_ID" in str(e)

        import asyncio

        asyncio.run(test_backward_compat())

    def test_search_reddit_params_clamping(self) -> None:
        """Test that parameter clamping works for search_reddit."""
        from sagashi.reddit import REDDIT_LIMIT_MAX, REDDIT_LIMIT_MIN, _clamp

        # Default limit should be clamped
        assert _clamp(10, REDDIT_LIMIT_MIN, REDDIT_LIMIT_MAX) == 10
        # Too small should be clamped up
        assert _clamp(0, REDDIT_LIMIT_MIN, REDDIT_LIMIT_MAX) == REDDIT_LIMIT_MIN
        # Too large should be clamped down
        assert _clamp(100, REDDIT_LIMIT_MIN, REDDIT_LIMIT_MAX) == REDDIT_LIMIT_MAX


class TestPickEnglishLang:
    """Tests for the caption-track selection logic."""

    def test_prefers_manual_en(self) -> None:
        """Manual 'en' beats auto-generated and other languages."""
        from sagashi.youtube import pick_english_lang

        info = {
            "subtitles": {"de": {}, "en": {}},
            "automatic_captions": {"en": {}, "en-GB": {}},
        }
        assert pick_english_lang(info) == ("en", False)

    def test_prefers_manual_en_variants(self) -> None:
        """Manual en-US/en-GB are tried in order before any other en*."""
        from sagashi.youtube import pick_english_lang

        info = {
            "subtitles": {"en-GB": {}, "en-XX": {}, "en-US": {}},
            "automatic_captions": {},
        }
        assert pick_english_lang(info) == ("en-US", False)

    def test_falls_back_to_any_manual_en(self) -> None:
        """Any manual en* track is used when the preferred ones are absent."""
        from sagashi.youtube import pick_english_lang

        info = {"subtitles": {"en-AU": {}, "de": {}}, "automatic_captions": {"en": {}}}
        assert pick_english_lang(info) == ("en-AU", False)

    def test_falls_back_to_auto_en(self) -> None:
        """Auto-generated English is used when no manual captions exist."""
        from sagashi.youtube import pick_english_lang

        info = {"subtitles": {"de": {}}, "automatic_captions": {"en": {}}}
        assert pick_english_lang(info) == ("en", True)

    def test_falls_back_to_any_auto_en(self) -> None:
        """Any auto en* track is used when preferred auto ones are absent."""
        from sagashi.youtube import pick_english_lang

        info = {"subtitles": {}, "automatic_captions": {"en-GB": {}}}
        assert pick_english_lang(info) == ("en-GB", True)

    def test_no_english_captions(self) -> None:
        """None is returned when there are no English tracks at all."""
        from sagashi.youtube import pick_english_lang

        assert (
            pick_english_lang(
                {"subtitles": {"de": {}}, "automatic_captions": {"de": {}}}
            )
            is None
        )


class TestExtractTextFromJson3:
    """Tests for json3 caption parsing."""

    def test_strips_timing_and_formatting(self, tmp_path) -> None:
        """Events/segments are joined into plain text, dropping newlines."""
        import json

        from sagashi.youtube import extract_text_from_json3

        path = tmp_path / "video.json3"
        path.write_text(
            json.dumps(
                {
                    "events": [
                        {"segs": [{"utf8": "Hello"}, {"utf8": "\n"}]},
                        {"segs": [{"utf8": " world"}]},
                        {"segs": []},
                        {"no_segs": True},
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert extract_text_from_json3(str(path)) == "Hello world"


class TestFriendlyYtdlpError:
    """Tests for yt-dlp error mapping to user-friendly messages."""

    def test_bot_check(self) -> None:
        """YouTube bot-checks point at the cookies file."""
        from sagashi.youtube import _friendly_ytdlp_error

        message = _friendly_ytdlp_error("Sign in to confirm you're not a bot")
        assert "cookies.txt" in message
        assert "YOUTUBE_COOKIES_FILE" in message

    def test_read_only_cookies(self) -> None:
        """A read-only cookies mount gets an actionable message."""
        from sagashi.youtube import _friendly_ytdlp_error

        message = _friendly_ytdlp_error(
            "OSError: [Errno 30] Read-only file system: '/app/cookies.txt'"
        )
        assert "read-write" in message

    def test_no_subtitles(self) -> None:
        """Missing captions get a clear message."""
        from sagashi.youtube import _friendly_ytdlp_error

        assert "No English captions" in _friendly_ytdlp_error("No subtitles found")

    def test_private_video(self) -> None:
        """Private videos get a clear message."""
        from sagashi.youtube import _friendly_ytdlp_error

        assert "private" in _friendly_ytdlp_error("This video is private")

    def test_unknown_error_truncated(self) -> None:
        """Unknown errors are passed through, truncated to 200 chars."""
        from sagashi.youtube import _friendly_ytdlp_error

        message = _friendly_ytdlp_error("x" * 500)
        assert len(message) < 250


class TestFetchYoutubeTranscript:
    """Tests for the transcript fetcher (yt-dlp subprocess mocked out)."""

    @staticmethod
    def _mock_yt_dlp(monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]) -> None:
        """Replace the yt-dlp subprocess with a recorder."""

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return object()

        monkeypatch.setattr("sagashi.youtube.subprocess.run", fake_run)

    @pytest.mark.asyncio
    async def test_returns_title_and_transcript(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A successful fetch returns the title and plain-text transcript."""
        import asyncio
        import json

        import sagashi.youtube as yt

        calls: list[list[str]] = []
        self._mock_yt_dlp(monkeypatch, calls)

        def fake_metadata(url: str, cookies_path: str | None) -> dict[str, object]:
            return {"title": "Test Video", "subtitles": {"en": {}}}

        monkeypatch.setattr(yt, "_get_metadata", fake_metadata)

        json3 = {"events": [{"segs": [{"utf8": "spoken"}, {"utf8": " text"}]}]}

        def fake_download(
            url: str, lang: str, is_auto: bool, outdir: str, cookies_path: str | None
        ) -> str:
            path = f"{outdir}/video.json3"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(json3, f)
            return path

        monkeypatch.setattr(yt, "_download_subtitle", fake_download)

        result = await asyncio.to_thread(
            yt.fetch_youtube_transcript, "https://youtube.com/watch?v=abc", None
        )
        assert result == {"title": "Test Video", "transcript": "spoken text"}

    @pytest.mark.asyncio
    async def test_no_english_captions_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Videos without English captions raise a friendly error naming the video."""
        import asyncio

        import sagashi.youtube as yt

        calls: list[list[str]] = []
        self._mock_yt_dlp(monkeypatch, calls)

        def fake_metadata(url: str, cookies_path: str | None) -> dict[str, object]:
            return {"title": "My Recipe Video", "subtitles": {}}

        monkeypatch.setattr(yt, "_get_metadata", fake_metadata)

        with pytest.raises(yt.YoutubeTranscriptError, match="My Recipe Video"):
            await asyncio.to_thread(
                yt.fetch_youtube_transcript, "https://youtube.com/watch?v=abc", None
            )

    @pytest.mark.asyncio
    async def test_metadata_failure_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A yt-dlp metadata failure propagates its friendly error."""
        import asyncio

        import sagashi.youtube as yt

        calls: list[list[str]] = []
        self._mock_yt_dlp(monkeypatch, calls)

        def fake_metadata(url: str, cookies_path: str | None) -> dict[str, object]:
            raise yt.YoutubeTranscriptError("yt-dlp failed: boom")

        monkeypatch.setattr(yt, "_get_metadata", fake_metadata)

        with pytest.raises(yt.YoutubeTranscriptError, match="yt-dlp failed"):
            await asyncio.to_thread(
                yt.fetch_youtube_transcript, "https://youtube.com/watch?v=abc", None
            )

    @pytest.mark.asyncio
    async def test_missing_binary_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing yt-dlp binary raises a friendly error."""
        import asyncio

        import sagashi.youtube as yt

        monkeypatch.setattr(yt.shutil, "which", lambda name: None)

        with pytest.raises(yt.YoutubeTranscriptError, match="yt-dlp is not installed"):
            await asyncio.to_thread(
                yt.fetch_youtube_transcript, "https://youtube.com/watch?v=abc", None
            )

    @pytest.mark.asyncio
    async def test_cookies_passed_to_yt_dlp(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """When a cookies file exists, yt-dlp is invoked with --cookies."""
        import subprocess

        import sagashi.youtube as yt

        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        monkeypatch.setattr("sagashi.youtube.subprocess.run", fake_run)
        monkeypatch.setattr(yt.shutil, "which", lambda name: name == "yt-dlp")
        yt._run_yt_dlp("https://youtube.com/watch?v=abc", ["--dump-json"], str(cookies))
        assert "--cookies" in calls[-1]
        assert str(cookies) in calls[-1]

    @pytest.mark.asyncio
    async def test_js_runtime_node_used_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """yt-dlp is pointed at node when it is on PATH."""
        import subprocess

        import sagashi.youtube as yt

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        monkeypatch.setattr("sagashi.youtube.subprocess.run", fake_run)
        monkeypatch.setattr(yt.shutil, "which", lambda name: name in ("yt-dlp", "node"))
        yt._run_yt_dlp("https://youtube.com/watch?v=abc", ["--dump-json"], None)
        assert calls[-1][1:4] == ["--no-warnings", "--js-runtimes", "node"]

    @pytest.mark.asyncio
    async def test_no_js_runtime_arg_without_node(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No --js-runtimes flag when node is missing (deno default applies)."""
        import subprocess

        import sagashi.youtube as yt

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        monkeypatch.setattr("sagashi.youtube.subprocess.run", fake_run)
        monkeypatch.setattr(yt.shutil, "which", lambda name: name == "yt-dlp")
        yt._run_yt_dlp("https://youtube.com/watch?v=abc", ["--dump-json"], None)
        assert "--js-runtimes" not in calls[-1]
