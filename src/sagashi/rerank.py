"""Lexical reranking of search results.

Pure-Python term-overlap scoring (no dependencies, no models):
query terms are matched against each result's title and body, with
title matches weighted higher. Results are sorted by score descending;
ties keep their original (provider) order thanks to Python's stable sort.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alphanumerics, drop single characters.

    Applies light plural stemming (strip trailing "s"). Both query and
    document tokens pass through the same normalization, so matching is
    symmetric (e.g. "keyboards" matches the query "keyboard").
    """
    tokens = {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1}
    return {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in tokens}


def lexical_score(query: str, title: str, body: str) -> float:
    """Score how well a result matches a query.

    Each query token scores 2.0 if it appears in the title, 1.0 if it
    appears anywhere in the body, else 0. The result is normalized to
    [0.0, 1.0].
    """
    q = _tokenize(query)
    if not q:
        return 0.0
    title_tokens = _tokenize(title)
    body_tokens = _tokenize(body) | title_tokens
    score = sum(
        2.0 if t in title_tokens else 1.0 if t in body_tokens else 0.0 for t in q
    )
    return score / (2.0 * len(q))


def rerank[T](
    query: str,
    results: Sequence[T],
    title_of: Callable[[T], str],
    body_of: Callable[[T], str],
) -> list[T]:
    """Return results sorted by lexical relevance to the query.

    Stable: results with equal scores keep their original order, so a
    provider's own ranking is preserved among lexical ties.
    """
    return sorted(
        results,
        key=lambda r: lexical_score(query, title_of(r), body_of(r)),
        reverse=True,
    )
