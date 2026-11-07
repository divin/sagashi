"""FastMCP server assembly."""

from __future__ import annotations

import textwrap
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from sagashi.auth import StaticTokenVerifier
from sagashi.config import Config, ConfigError, load_config
from sagashi.fetcher import fetch_page
from sagashi.providers import SearchProvider
from sagashi.providers.uruky import UrukyProvider
from sagashi.rerank import rerank


class CrawlerHolder:
    """Holds a single AsyncWebCrawler instance for the process lifetime."""

    _instance: Any | None = None

    @classmethod
    async def get_crawler(cls) -> Any:
        """Get or create the crawler instance."""
        if cls._instance is None:
            from crawl4ai import AsyncWebCrawler

            cls._instance = AsyncWebCrawler(verbose=False)
            await cls._instance.start()
        return cls._instance

    @classmethod
    async def close(cls) -> None:
        """Close the crawler instance."""
        if cls._instance is not None:
            await cls._instance.stop()
            cls._instance = None


def _load_provider(config: Config) -> SearchProvider:
    """Create the appropriate search provider based on config."""
    if config.search_provider != "uruky":
        raise ConfigError(f"Unknown search provider: {config.search_provider}")
    if config.uruky_token is None:
        raise ConfigError("URUKY_TOKEN is required")
    return UrukyProvider(config.uruky_token)


_RECENCY_FILTERS = ("day", "week", "month", "year")


def _validate_recency(recency: str | None) -> None:
    """Raise a clear error for unknown recency values."""
    if recency is not None and recency not in _RECENCY_FILTERS:
        raise ToolError(
            f"Invalid recency: {recency!r}. Use one of: {', '.join(_RECENCY_FILTERS)}."
        )


def _effective_max_chars(max_chars: int | None, ceiling: int) -> int:
    """Resolve the requested truncation against the configured ceiling."""
    if max_chars is not None and max_chars < 1:
        raise ToolError("max_chars must be a positive integer.")
    if max_chars is None:
        return ceiling
    return min(max_chars, ceiling)


def build_server() -> FastMCP:
    """Build and return the FastMCP server instance."""
    config = load_config()
    config.validate()

    # Optional bearer auth: enforced only when MCP_SEARCH_AUTH_TOKEN is set.
    # Unset means an open server, which is acceptable on a trusted LAN.
    auth = StaticTokenVerifier(config.mcp_auth_token) if config.mcp_auth_token else None

    mcp = FastMCP(
        "Sagashi",
        auth=auth,
        instructions=textwrap.dedent("""\
            You are a helpful search assistant. Use the available tools to:
            1. `web_search` - General web search via Uruky
            2. `search_reddit` - Search Reddit via the official API
            3. `fetch_page` - Fetch a web page's content

            Choose the right tool based on the user's request:
            - General search queries → `web_search`
            - Reddit-specific questions → `search_reddit`
            - Fetching specific URLs → `fetch_page`

            When calling `fetch_page`, a reddit.com or redd.it URL is
            automatically served by the Reddit Source (official API): the
            thread's title, selftext, and top comments come back as Markdown
            and the crawler is never involved.
            """),
    )

    provider = _load_provider(config)

    @mcp.tool(
        name="web_search",
        description="""Search the web using Uruky.

        Use this for general web search queries. Returns a compact Markdown list
        of search results with title, URL, snippet, and date.
        Optional param: recency - one of day, week, month, year.
        """,
    )
    async def web_search(
        query: str,
        recency: str | None = None,
    ) -> str:
        """Search the web and return results as Markdown."""
        _validate_recency(recency)
        results = await provider.search(query, recency=recency)
        if not results:
            return "No results found."

        # Lexical rerank: boost results whose title/snippet match the query
        results = rerank(
            query,
            results,
            title_of=lambda r: r.title,
            body_of=lambda r: r.snippet,
        )

        lines: list[str] = []
        for result in results:
            lines.append(f"- **{result.title}** ({result.url})")
            if result.snippet:
                lines.append(f"  {result.snippet}")
            if result.date:
                lines.append(f"  Date: {result.date}")

        return "\n".join(lines)

    @mcp.tool(
        name="search_reddit",
        description="""Search Reddit via the official API.

        Use this for Reddit-specific questions or when the user explicitly asks
        about Reddit. Returns a compact Markdown list of search results.
        Optional params: time_filter (day/week/month/year/all, default month),
        limit (1-25, default 10).
        """,
    )
    async def search_reddit_tool(
        query: str,
        time_filter: str = "month",
        limit: int = 10,
    ) -> str:
        """Search Reddit and return results as Markdown."""
        from sagashi.reddit import search_reddit

        return await search_reddit(query, time_filter=time_filter, limit=limit)

    @mcp.tool(
        name="fetch_page",
        description="""Fetch a web page and return its Markdown content.

        Use this when the user provides a specific URL to fetch. A reddit.com
        or redd.it URL is served by the Reddit Source instead: the thread's
        title, selftext, and top comments are returned via the official API.
        Content is truncated at max_chars (default: FETCH_MAX_CHARS, 40000;
        requests are capped at the configured ceiling).
        """,
    )
    async def fetch_page_tool(
        url: str,
        max_chars: Annotated[int | None, Field(ge=1)] = None,
    ) -> str:
        """Fetch a page and return its Markdown content."""
        effective_max = _effective_max_chars(max_chars, config.fetch_max_chars)
        crawler = await CrawlerHolder.get_crawler()
        return await fetch_page(url, max_chars=effective_max, crawler=crawler)

    return mcp
