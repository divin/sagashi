"""Reranking of search results.

Two strategies behind one helper:

- **Endpoint rerank** (when ``RERANKER_URL`` is configured): documents are
  scored by a llama-server ``/v1/rerank`` cross-encoder against the search
  query. Raw logits are only used for ordering, never exposed.
- **Lexical rerank** (fallback, no dependencies, no models): pure-Python
  term-overlap scoring; query terms are matched against each result's title
  and body, with title matches weighted higher. ASCII terms match as whole
  tokens; CJK (Japanese/Chinese) terms match as substrings, so unsegmented
  CJK queries still rank correctly.

Both sort by score descending; ties keep their original (provider) order
thanks to Python's stable sort.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from sagashi.reranker_client import RerankerClient, RerankerClientError

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Contiguous CJK runs: hiragana, katakana (incl. halfwidth), CJK ideographs
# (incl. Extension A and compatibility). Matched as substring terms because
# CJK text is not whitespace-segmented.
_CJK_RE = re.compile(
    r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]+"
)

# Common Japanese particles and punctuation, stripped from run edges so
# e.g. "日本の" still matches documents containing only "日本".
_CJK_PARTICLES = "のはをにでとがもへやかね・、。"


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alphanumerics, drop single characters.

    Applies light plural stemming (strip trailing "s"). Both query and
    document tokens pass through the same normalization, so matching is
    symmetric (e.g. "keyboards" matches the query "keyboard").
    """
    tokens = {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1}
    return {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in tokens}


def _cjk_terms(text: str) -> list[str]:
    """Extract CJK substring terms from text.

    Contiguous CJK runs with edge particles stripped; empty runs dropped.
    """
    terms = []
    for run in _CJK_RE.findall(text):
        stripped = run.strip(_CJK_PARTICLES)
        if stripped:
            terms.append(stripped)
    return terms


def lexical_score(query: str, title: str, body: str) -> float:
    """Score how well a result matches a query.

    ASCII query tokens score 2.0 if they appear as whole tokens in the title,
    1.0 if they appear anywhere in the body. CJK query terms score 2.0 if they
    appear as substrings of the title, 1.0 if of the body. The total is
    normalized to [0.0, 1.0].
    """
    q = _tokenize(query)
    cjk = _cjk_terms(query)
    total = 2.0 * (len(q) + len(cjk))
    if total == 0:
        return 0.0
    title_tokens = _tokenize(title)
    body_tokens = _tokenize(body) | title_tokens
    score = sum(
        2.0 if t in title_tokens else 1.0 if t in body_tokens else 0.0 for t in q
    )
    score += sum(2.0 if t in title else 1.0 if t in body else 0.0 for t in cjk)
    return score / total


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


async def rerank_results[T](
    query: str,
    results: Sequence[T],
    title_of: Callable[[T], str],
    body_of: Callable[[T], str],
    *,
    reranker_url: str | None,
    reranker_token: str | None = None,
) -> list[T]:
    """Return results ranked against the query, endpoint rerank when available.

    With ``reranker_url`` set, each result's title and body are sent as one
    document to the ``/v1/rerank`` endpoint and results are ordered by
    relevance score descending (ties keep provider order via stable sort).
    Without it, falls back to :func:`rerank` (lexical scoring).

    Raises:
        RerankerClientError: On transport failure or a malformed/incomplete
            rerank response (propagated — a reranker outage is never silently
            swallowed into a degraded ranking).
    """
    if reranker_url is None:
        return rerank(query, results, title_of, body_of)

    documents = [f"{title_of(r)}\n\n{body_of(r)}" for r in results]
    client = RerankerClient(reranker_url, reranker_token)
    scored = await client.rerank(query, documents)
    scores = {result.index: result.score for result in scored}
    order = sorted(range(len(results)), key=lambda i: -scores[i])
    return [results[i] for i in order]


__all__ = ["RerankerClientError", "lexical_score", "rerank", "rerank_results"]
