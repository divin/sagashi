"""Search provider protocol and adapter seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResult:
    """Single search result."""

    url: str
    title: str
    description: str
    date: str | None = None


class SearchProvider(Protocol):
    """Protocol for search provider adapters."""

    async def search(
        self, query: str, recency: str | None = None
    ) -> list[SearchResult]:
        """Search the web and return results."""
        ...
