"""Page fetching module with deterministic Reddit routing."""

from __future__ import annotations

import re

REDDIT_SLUG_PATTERN = re.compile(r"redd\.it/([a-zA-Z0-9]+)")


def is_reddit_url(url: str) -> bool:
    """Check if a URL is a Reddit URL (reddit.com or redd.it)."""
    return url.startswith("https://www.reddit.com/") or bool(
        REDDIT_SLUG_PATTERN.search(url)
    )


async def fetch_page(url: str, max_chars: int = 40000) -> str:
    """Fetch a page and return its Markdown content with deterministic routing."""
    raise NotImplementedError
