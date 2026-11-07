"""Page fetching module with deterministic Reddit routing."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from crawl4ai import AsyncWebCrawler

REDDIT_SLUG_PATTERN = re.compile(r"redd\.it/([a-zA-Z0-9]+)")


class FetchError(Exception):
    """Page fetch error with a user-friendly, trace-free message."""


# Crawler blocklist: reddit.com must never be crawled (ToS compliance and
# IP-flagging risk, see docs/adr/0001). Reddit URLs are routed to the
# official API before this list is consulted; it stays as a last-resort
# guard so the crawler path stays closed even if routing is bypassed.
BLOCKED_DOMAINS = ("www.reddit.com", "reddit.com")

# Boilerplate removal: drop tags that carry no page content and skip tiny
# text nodes so navigation menus and banners stay out of the Markdown.
_EXCLUDED_TAGS = ("nav", "header", "footer", "aside", "form", "script", "style")


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


def _friendly_error(text: str) -> str:
    """Map a crawler error to a short, actionable message.

    Never includes stack traces, file paths, or library internals: those
    leak server details to the calling model and waste context tokens.
    """
    lowered = text.lower()
    if "url must start with" in lowered:
        return "Invalid URL: only http:// and https:// URLs are supported."
    if "err_name_not_resolved" in lowered:
        return "Could not resolve the domain. Check the URL for typos."
    if "err_connection_refused" in lowered:
        return "The server refused the connection."
    if "err_connection_reset" in lowered:
        return "The connection was reset by the server."
    if "err_access_denied" in lowered or "err_blocked_by_client" in lowered:
        return "The page is blocking automated access."
    if "err_cert" in lowered:
        return "The site's TLS certificate is invalid."
    if "timed out" in lowered or "timeout" in lowered:
        return "The request timed out."
    status = re.search(r"\b(40[13458]|41[0358]|429|50[0234])\b", text)
    if status:
        return (
            f"The page returned HTTP {status.group(1)}. "
            "It may not exist, require a login, or block crawlers."
        )
    return "Failed to fetch the page. It may be unreachable or blocking crawlers."


def _crawler_config() -> Any:
    """Build the crawl4ai run config for clean, model-friendly Markdown."""
    from crawl4ai import (
        CrawlerRunConfig,
        DefaultMarkdownGenerator,
        PruningContentFilterLXML,
    )

    return CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilterLXML(
                threshold=0.45, threshold_type="fixed"
            )
        ),
        excluded_tags=list(_EXCLUDED_TAGS),
        word_count_threshold=10,
    )


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
    FetchError
        If the page cannot be fetched (message is user-friendly, no traces).
    RedditError
        If the URL is a Reddit URL and the Reddit API call fails.
    """
    from sagashi.config import load_config
    from sagashi.reddit import fetch_thread

    load_config()

    # Reddit Routing (docs/adr/0001): any reddit.com or redd.it URL is served
    # by the Reddit Source (official API) and never reaches the crawler.
    if is_reddit_url(url):
        return await fetch_thread(url)

    # Last-resort guard: the crawler must never touch the blocklist, even if
    # routing above is bypassed by a future edit.
    if is_blocked_url(url):
        raise FetchError(
            "This domain is blocked from crawling. "
            "Reddit content is served through the official API instead."
        )

    if not url.startswith(("http://", "https://")):
        raise FetchError("Invalid URL: only http:// and https:// URLs are supported.")

    config = _crawler_config()
    try:
        if crawler is None:
            from crawl4ai import AsyncWebCrawler

            crawler = AsyncWebCrawler(verbose=False)
            async with crawler:
                result = await crawler.arun(url=url, config=config)
        else:
            result = await crawler.arun(url=url, config=config)
    except Exception as e:  # noqa: BLE001 - third-party pages raise arbitrary errors; we sanitize
        raise FetchError(_friendly_error(str(e))) from None

    if not result.success or not result.markdown:
        detail = getattr(result, "error_message", None) or ""
        raise FetchError(_friendly_error(detail))

    markdown: str = result.markdown

    # Truncate if needed with visible marker
    if len(markdown) > max_chars:
        markdown = (
            markdown[:max_chars]
            + "\n\n... (truncated at "
            + str(max_chars)
            + " characters)"
        )

    return markdown
