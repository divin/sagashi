"""Reddit search module."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import praw


async def search_reddit(query: str, time_filter: str = "month") -> str:
    """Search Reddit via the official API and render a markdown summary."""
    raise NotImplementedError
