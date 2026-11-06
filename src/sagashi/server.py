"""FastMCP server assembly."""

from __future__ import annotations

import textwrap
from typing import Any

from fastmcp import FastMCP

from sagashi.config import Config, ConfigError, load_config
from sagashi.fetcher import fetch_page
from sagashi.providers import SearchProvider
from sagashi.providers.uruky import UrukyProvider


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


def build_server() -> FastMCP:
    """Build and return the FastMCP server instance."""
    config = load_config()
    config.validate()

    mcp = FastMCP(
        "Sagashi",
        instructions=textwrap.dedent("""\
            You are a helpful search assistant. Use the available tools to:
            1. `web_search` - General web search via Uruky
            2. `search_reddit` - Search Reddit via the official API
            3. `fetch_page` - Fetch a web page's content

            Choose the right tool based on the user's request:
            - General search queries → `web_search`
            - Reddit-specific questions → `search_reddit`
            - Fetching specific URLs → `fetch_page`

            When calling `fetch_page`, if the URL is from reddit.com or redd.it,
            the server will automatically route to `search_reddit` instead.
            """),
    )

    provider = _load_provider(config)

    @mcp.tool(
        name="web_search",
        description="""Search the web using Uruky.

        Use this for general web search queries. Returns a compact Markdown list
        of search results with title, URL, snippet, and date.
        """,
    )
    async def web_search(
        query: str,
        recency: str | None = None,
    ) -> str:
        """Search the web and return results as Markdown."""
        results = await provider.search(query, recency=recency)
        if not results:
            return "No results found."

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
        """,
    )
    async def search_reddit_tool(
        query: str,
        time_filter: str = "month",
    ) -> str:
        """Search Reddit and return results as Markdown."""
        from sagashi.reddit import search_reddit

        return await search_reddit(query, time_filter=time_filter)

    @mcp.tool(
        name="fetch_page",
        description="""Fetch a web page and return its Markdown content.

        Use this when the user provides a specific URL to fetch. If the URL is
        from reddit.com or redd.it, it will be routed to `search_reddit` instead.
        Content is truncated at FETCH_MAX_CHARS (default 40000).
        """,
    )
    async def fetch_page_tool(url: str) -> str:
        """Fetch a page and return its Markdown content."""
        crawler = await CrawlerHolder.get_crawler()
        return await fetch_page(url, max_chars=config.fetch_max_chars, crawler=crawler)

    return mcp
