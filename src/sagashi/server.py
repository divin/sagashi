"""FastMCP server assembly."""

from __future__ import annotations

import asyncio
import logging
import textwrap
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from sagashi.auth import StaticTokenVerifier
from sagashi.config import Config, ConfigError, load_config
from sagashi.fetcher import fetch_page
from sagashi.providers import SearchProvider
from sagashi.providers.uruky import UrukyProvider
from sagashi.rerank import RerankerClientError, rerank_results

logger = logging.getLogger(__name__)


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
            4. `fetch_youtube_transcript` - Fetch a YouTube video's title
               and transcript

            Choose the right tool based on the user's request:
            - General search queries → `web_search`
            - Reddit-specific questions → `search_reddit`
            - Fetching specific URLs → `fetch_page`
            - YouTube URLs → `fetch_youtube_transcript`

            When calling `fetch_page`, a reddit.com or redd.it URL is
            automatically served by the Reddit Source (official API): the
            thread's title, selftext, and top comments come back as Markdown
            and the crawler is never involved.
            """),
    )

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy", "service": "sagashi"})

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

        # Rerank: cross-encoder endpoint when configured, lexical fallback otherwise
        try:
            results = await rerank_results(
                query,
                results,
                title_of=lambda r: r.title,
                body_of=lambda r: r.snippet,
                reranker_url=config.reranker_url,
                reranker_token=config.reranker_token,
            )
        except RerankerClientError as e:
            raise ToolError(f"Reranker failed: {e}") from e

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
        limit (1-25, default 10), subreddit (specific subreddit name without r/).
        """,
    )
    async def search_reddit_tool(
        query: str,
        time_filter: str = "month",
        limit: int = 10,
        subreddit: str | None = None,
    ) -> str:
        """Search Reddit and return results as Markdown."""
        from sagashi.reddit import search_reddit

        return await search_reddit(
            query, time_filter=time_filter, limit=limit, subreddit=subreddit
        )

    @mcp.tool(
        name="list_subreddit_posts",
        description="""List posts from a subreddit via the official Reddit API.

        Use this to browse posts from a specific subreddit with sorting and
        time filtering. Returns structured data including post metadata,
        scores, and permalinks. Useful for Matomeru-style rolling 24-hour
        queries with explicit UTC time windows.
        
        Parameters:
        - subreddit: subreddit name (without r/ prefix)
        - sort: hot, new, top, rising, controversial (default: hot;
          time_filter only applies to top/controversial)
        - time_filter: day, week, month, year, all (default: all)
        - limit: 1-100 posts (default: 10)
        - before/after: exclusive time-window bounds, epoch seconds or ISO-8601
          (naive values treated as UTC)
        - cursor: fullname (t3_...) to resume listing after (pagination)
        
        Returns: {posts: [...], pagination: {cursor}, metadata: {subreddit, sort, count}}
        """,
    )
    async def list_subreddit_posts_tool(
        subreddit: str,
        sort: str = "hot",
        time_filter: str = "all",
        limit: int = 10,
        before: str | None = None,
        after: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List subreddit posts and return structured metadata."""
        from sagashi.reddit import list_subreddit_posts

        return await list_subreddit_posts(
            subreddit,
            sort=sort,
            time_filter=time_filter,
            limit=limit,
            before=before,
            after=after,
            cursor=cursor,
        )

    @mcp.tool(
        name="fetch_reddit_threads",
        description="""Fetch multiple Reddit threads in batch via the official API.

        Use this when you need to retrieve multiple threads efficiently in one
        call. Accepts a list of post IDs or URLs and returns structured thread
        data including top comments. Returns per-item errors for robustness.
        
        Parameters:
        - items: list of Reddit post IDs or URLs (strings or dicts with id/url keys)
        - comment_count: 1-50 top comments per thread (default: 20)
        
        Returns: {
            results: [{id, subreddit, title, score, comments: [...]}, ...],
            errors: [{index, item, error}, ...],
            metadata: {total, success_count, error_count}
        }
        """,
    )
    async def fetch_reddit_threads_tool(
        items: list[str | dict],
        comment_count: int = 20,
    ) -> dict[str, Any]:
        """Fetch multiple threads and return structured data."""
        from sagashi.reddit import fetch_reddit_threads

        return await fetch_reddit_threads(items, comment_count=comment_count)

    @mcp.tool(
        name="fetch_youtube_transcript",
        description="""Fetch a YouTube video's title and full transcript.

        Use this when the user provides a YouTube URL and you need the spoken
        content (captions) for analysis, summarization, quoting, or search.
        Prefers manual English captions, falls back to auto-generated English.
        Returns {title, transcript} where the transcript is plain text with
        timing/formatting stripped. The server needs a cookies.txt (Netscape
        format, exported while signed in to YouTube) at YOUTUBE_COOKIES_FILE
        when YouTube bot-checks its IP.
        """,
    )
    async def fetch_youtube_transcript_tool(url: str) -> dict[str, str]:
        """Fetch a YouTube video's title and transcript as structured data."""
        from sagashi.youtube import YoutubeTranscriptError, fetch_youtube_transcript

        try:
            return await asyncio.to_thread(
                fetch_youtube_transcript,
                url,
                cookies_path=config.youtube_cookies_path,
            )
        except YoutubeTranscriptError as e:
            logger.warning("fetch_youtube_transcript failed: %s", e)
            raise ToolError(str(e)) from e

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
        from sagashi.fetcher import FetchError

        effective_max = _effective_max_chars(max_chars, config.fetch_max_chars)
        crawler = await CrawlerHolder.get_crawler()
        try:
            return await fetch_page(url, max_chars=effective_max, crawler=crawler)
        except FetchError as e:
            logger.warning("fetch_page failed: %s", e)
            raise ToolError(str(e)) from e

    return mcp
