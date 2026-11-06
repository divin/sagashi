"""Uruky search provider adapter."""

from __future__ import annotations

import os
from typing import cast

import httpx
from typing_extensions import override

from sagashi.providers import SearchResult, SearchProvider


class UrukyProvider(SearchProvider):
    """Uruky search provider adapter."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._base_url = "https://uruky.com/search"

    @override
    async def search(self, query: str, recency: str | None = None) -> list[SearchResult]:
        """Search via Uruky."""
        params: dict[str, str] = {"q": query, "f": "json", "i": self._token}
        if recency:
            params["r"] = recency

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(self._base_url, params=params)
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResult] = []
        for item in data.get("results", []):
            results.append(
                SearchResult(
                    url=str(item.get("url", "")),
                    title=str(item.get("title", "")),
                    description=str(item.get("description", "")),
                    date=item.get("date") if isinstance(item.get("date"), str) else None,
                )
            )
        return results
