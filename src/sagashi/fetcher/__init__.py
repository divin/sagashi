"""Page fetching module with deterministic Reddit routing."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crawl4ai import AsyncWebCrawler

REDDIT_SLUG_PATTERN = re.compile(r"redd\.it/([a-zA-Z0-9]+)")


# Crawler blocklist: reddit.com is blocked to prevent ToS violations
# and IP flagging risks. Reddit content goes through the official API.
BLOCKED_DOMAINS = ("www.reddit.com", "reddit.com")


def is_reddit_url(url: str) -> bool:
    """Check if a URL is a Reddit URL (reddit.com or redd.it)."""
    # Check for reddit.com URLs
    if url.startswith(("https://www.reddit.com/", "https://reddit.com/")):
        return True
    # Check for redd.it short URLs
    return bool(REDDIT_SLUG_PATTERN.search(url))


def is_blocked_url(url: str) -> bool:
    """Check if a URL is on the crawler blocklist."""
    for blocked in BLOCKED_DOMAINS:
        if url.startswith(f"https://{blocked}/") or url == f"https://{blocked}":
            return True
    return False


async def fetch_page(
    url: str,
    max_chars: int = 40000,
    crawler: AsyncWebCrawler | None = None,
) -> str:
    """Fetch a page and return its Markdown content with deterministic routing.

    Parameters
    ----------
    url : str
        The URL to fetch.
    max_chars : int, optional
        Maximum characters to return. Default is 40000.
    crawler : AsyncWebCrawler, optional
        Optional crawler instance. If None, a new one is created.

    Returns
    -------
    str
        Markdown content of the page, truncated with visible marker if needed.

    Raises
    ------
    RuntimeError
        If the URL is blocked (reddit.com) or Reddit credentials are missing.
    """
    from sagashi.config import ConfigError, load_config
    from sagashi.reddit import fetch_thread

    load_config()

    # Reddit routing: redirect to Reddit module
    if is_reddit_url(url):
        if is_blocked_url(url):
            raise ConfigError(
                "reddit.com URLs are blocked. Use https://www.reddit.com/ or redd.it links instead."
            )
        # Use the fetch_thread function from reddit module
        return await fetch_thread(url)

    # Non-Reddit pages: use crawl4ai
    try:
        if crawler is None:
            from crawl4ai import AsyncWebCrawler

            crawler = AsyncWebCrawler(verbose=False)
            async with crawler:
                result = await crawler.arun(url=url)
        else:
            result = await crawler.arun(url=url)

        if result.success and result.markdown:
            markdown = result.markdown
        else:
            raise RuntimeError(f"Failed to fetch page: {result.error_message}")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch page: {e}") from e

    # Truncate if needed with visible marker
    if len(markdown) > max_chars:
        markdown = (
            markdown[:max_chars]
            + "\n\n... (truncated at "
            + str(max_chars)
            + " characters)"
        )

    return markdown
