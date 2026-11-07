"""Unit tests for Sagashi."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from sagashi.config import ConfigError, load_config
from sagashi.fetcher import is_blocked_url, is_reddit_url
from sagashi.providers.uruky import UrukyProvider
from sagashi.reddit import RedditError
from sagashi.rerank import lexical_score, rerank


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
