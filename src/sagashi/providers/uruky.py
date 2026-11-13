"""Uruky search provider adapter."""

from __future__ import annotations

from typing import override

import httpx

from sagashi.providers import SearchProvider, SearchResult

# Uruky's `w` parameter takes single-letter codes (d/w/m/y), a YYYY-MM-DD
# date, or "a" for anytime — not the spelled-out values exposed by the tool.
_RECENCY_CODES = {"day": "d", "week": "w", "month": "m", "year": "y"}


class UrukyProvider(SearchProvider):
    """Uruky search provider adapter."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._base_url = "https://uruky.com/search"

    @override
    async def search(
        self,
        query: str,
        recency: str | None = None,
        language: str | None = None,
    ) -> list[SearchResult]:
        """Search via Uruky."""
        params: dict[str, str] = {"q": query, "f": "json", "i": self._token}
        if recency:
            code = _RECENCY_CODES.get(recency, recency)
            params["w"] = code
        if language:
            params["l"] = language.upper()

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(self._base_url, params=params)
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                return []

        results: list[SearchResult] = []
        for item in data.get("results", []):
            results.append(
                SearchResult(
                    url=str(item.get("url", "")),
                    title=str(item.get("title", "")),
                    snippet=str(item.get("description", "")),
                    date=item.get("date")
                    if isinstance(item.get("date"), str)
                    else None,
                )
            )
        return results
