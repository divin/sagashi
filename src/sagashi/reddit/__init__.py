"""Reddit search module (asyncpraw)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from asyncpraw import Reddit
from asyncpraw.models import MoreComments

from sagashi.config import Config, load_config
from sagashi.rerank import rerank


class RedditError(Exception):
    """Reddit API error with a user-friendly message."""


def _create_reddit_client(config: Config) -> Reddit:
    """Create an asyncpraw Reddit client from configured credentials."""
    if config.reddit_client_id is None:
        raise RedditError("REDDIT_CLIENT_ID is required")
    if config.reddit_client_secret is None:
        raise RedditError("REDDIT_CLIENT_SECRET is required")
    if config.reddit_user_agent is None:
        raise RedditError("REDDIT_USER_AGENT is required")

    return Reddit(
        client_id=config.reddit_client_id,
        client_secret=config.reddit_client_secret,
        user_agent=config.reddit_user_agent,
        check_for_updates=False,
    )


REDDIT_LIMIT_MIN = 1
REDDIT_LIMIT_MAX = 25
COMMENT_COUNT_MIN = 1
COMMENT_COUNT_MAX = 50


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp value into [lo, hi]."""
    return max(lo, min(hi, value))


_LIST_SORTS = ("hot", "new", "top", "rising", "controversial")
_TIME_FILTERS = ("day", "week", "month", "year", "all")


def _normalize_subreddit(name: str) -> str:
    """Strip a leading ``r/`` (or ``/r/``) prefix from a subreddit name."""
    name = name.strip().lstrip("/")
    name = name.removeprefix("r/")
    return name


def _parse_utc_timestamp(value: str) -> float:
    """Parse a UTC timestamp from an epoch-seconds or ISO-8601 string.

    Naive ISO-8601 strings (no timezone offset) are interpreted as UTC, not
    as the server's local timezone.

    Raises
    ------
    ValueError
        If the value is neither valid epoch seconds nor valid ISO-8601.
    """
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError(
            f"invalid timestamp {value!r}: expected epoch seconds or ISO-8601"
        ) from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _extract_submission_id(url: str) -> str:
    """Extract the submission id from a reddit.com or redd.it URL.

    Handles thread permalinks (``/comments/<id>/...``), comment permalinks
    (``/comments/<id>/<slug>/<comment_id>/`` — the thread id wins), and
    redd.it short links. Falls back to the last non-empty path segment.
    """
    match = re.search(r"/comments/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"redd\.it/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1)
    segments = [
        segment
        for segment in url.split("/")
        if segment and ":" not in segment and "." not in segment
    ]
    return segments[-1] if segments else ""


async def search_reddit(
    query: str,
    time_filter: str = "month",
    limit: int = 10,
    subreddit: str | None = None,
) -> str:
    """Search Reddit via the official API and render a markdown summary.

    Parameters
    ----------
    query : str
        Search query string.
    time_filter : str, optional
        Time range to search (day, week, month, year, all). Default is "month".
    limit : int, optional
        Maximum number of results (1-25). Default is 10.
    subreddit : str, optional
        Specific subreddit to search. If None, searches r/all (default behavior).

    Returns
    -------
    str
        Markdown-formatted search results.
    """
    limit = _clamp(limit, REDDIT_LIMIT_MIN, REDDIT_LIMIT_MAX)
    config = load_config()
    reddit = _create_reddit_client(config)

    try:
        # Default to r/all if no subreddit specified (preserves backward compatibility)
        subreddit_name = "all" if subreddit is None else _normalize_subreddit(subreddit)
        subreddit_obj = await reddit.subreddit(subreddit_name)
        submissions = [
            sub
            async for sub in subreddit_obj.search(
                query,
                sort="relevance",
                time_filter=time_filter,
                limit=limit,
            )
        ]
    except Exception as e:
        raise RedditError(f"Reddit API error: {e}") from e
    finally:
        await reddit.close()

    if not submissions:
        return "No Reddit results found."

    # Lexical rerank: boost submissions whose title/selftext match the query
    submissions = rerank(
        query,
        submissions,
        title_of=lambda s: s.title,
        body_of=lambda s: s.selftext or "",
    )

    lines: list[str] = []
    for submission in submissions:
        lines.append(
            f"- **{submission.title}** (https://reddit.com{submission.permalink})"
        )
        lines.append(f"  Score: {submission.score}")
        author = submission.author
        if author is not None:
            lines.append(f"  Author: /u/{author.name}")

    return "\n".join(lines)


async def list_subreddit_posts(
    subreddit: str,
    sort: str = "hot",
    time_filter: str = "all",
    limit: int = 10,
    before: str | None = None,
    after: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List posts from a subreddit via the official API.

    Parameters
    ----------
    subreddit : str
        Subreddit name (without r/ prefix) to list posts from.
    sort : str, optional
        Subreddit listing sort: hot, new, top, rising, controversial.
        Default is "hot". ``time_filter`` only applies to top/controversial.
    time_filter : str, optional
        Time range for top/controversial sorting (day, week, month, year, all).
        Default is "all".
    limit : int, optional
        Maximum number of posts (1-100). Default is 10.
    before : str, optional
        Exclusive upper bound on post creation time: epoch seconds or ISO-8601
        (naive values are treated as UTC).
    after : str, optional
        Exclusive lower bound on post creation time: epoch seconds or ISO-8601
        (naive values are treated as UTC).
    cursor : str, optional
        Fullname (``t3_...``) of the post to start listing after (pagination).

    Returns
    -------
    dict
        Structured metadata including:
        - posts: list of post data with id, title, score, author, created_utc, permalink
        - pagination: {cursor} for next/prev page (fullname)
        - metadata: {subreddit, sort, time_filter, count}
    """
    limit = _clamp(limit, REDDIT_LIMIT_MIN, 100)  # Allow up to 100 for listing
    try:
        before_ts = _parse_utc_timestamp(before) if before is not None else None
        after_ts = _parse_utc_timestamp(after) if after is not None else None
    except ValueError as e:
        raise RedditError(str(e)) from e

    if sort not in _LIST_SORTS:
        raise RedditError(
            f"Invalid sort: {sort!r}. Use one of: {', '.join(_LIST_SORTS)}."
        )
    if sort in ("top", "controversial") and time_filter not in _TIME_FILTERS:
        raise RedditError(
            f"Invalid time_filter: {time_filter!r}. "
            f"Use one of: {', '.join(_TIME_FILTERS)}."
        )

    config = load_config()
    reddit = _create_reddit_client(config)

    try:
        subreddit_name = _normalize_subreddit(subreddit)
        subreddit_obj = await reddit.subreddit(subreddit_name)

        # Subreddit listing generators are the correct API path for listing;
        # search() requires a query and has different sort semantics.
        listing_kwargs: dict[str, Any] = {"limit": int(limit)}
        if cursor is not None:
            listing_kwargs["after"] = cursor

        if sort == "hot":
            listing = subreddit_obj.hot(**listing_kwargs)
        elif sort == "new":
            listing = subreddit_obj.new(**listing_kwargs)
        elif sort == "rising":
            listing = subreddit_obj.rising(**listing_kwargs)
        elif sort == "top":
            listing = subreddit_obj.top(time_filter=time_filter, **listing_kwargs)
        else:  # controversial
            listing = subreddit_obj.controversial(
                time_filter=time_filter, **listing_kwargs
            )

        posts_data: list[dict[str, Any]] = []
        async for post in listing:
            author = None
            if post.author is not None:
                author = post.author.name

            posts_data.append(
                {
                    "id": post.id,
                    "title": post.title,
                    "score": post.score,
                    "author": author,
                    "created_utc": post.created_utc,
                    "num_comments": post.num_comments,
                    "url": post.url,
                    "permalink": post.permalink,
                    "selftext": post.selftext or "",
                }
            )

        # Client-side time window filtering (exclusive bounds).
        filtered_posts = [
            p
            for p in posts_data
            if not (
                (before_ts is not None and p["created_utc"] >= before_ts)
                or (after_ts is not None and p["created_utc"] <= after_ts)
            )
        ]

        # The last fetched post's fullname serves as the next-page cursor.
        cursor_token: str | None = None
        if posts_data:
            cursor_token = f"t3_{posts_data[-1]['id']}"

        return {
            "posts": filtered_posts,
            "pagination": {"cursor": cursor_token},
            "metadata": {
                "subreddit": subreddit_name,
                "sort": sort,
                "time_filter": time_filter,
                "count": len(filtered_posts),
            },
        }
    except Exception as e:
        raise RedditError(f"Reddit API error: {e}") from e
    finally:
        await reddit.close()


async def batch_fetch_threads(
    items: list[str | dict],
    comment_count: int = 20,
) -> dict[str, Any]:
    """Fetch multiple Reddit threads in batch via the official API.

    Parameters
    ----------
    items : list of str or dict
        List of Reddit post IDs or URLs, or dict objects with "id"/"url" keys.
    comment_count : int, optional
        Maximum number of top comments per thread (1-50). Default is 20.

    Returns
    -------
    dict
        Structured response with:
        - results: list of thread data for successful fetches
        - errors: list of error objects for failed fetches
        - metadata: {total, success_count, error_count}
    """
    comment_count = _clamp(comment_count, COMMENT_COUNT_MIN, COMMENT_COUNT_MAX)
    config = load_config()
    reddit = _create_reddit_client(config)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    try:
        for idx, item in enumerate(items):
            try:
                # Extract ID from item (can be string or dict)
                if isinstance(item, dict):
                    url = item.get("url", "") or item.get("permalink", "")
                    if not url:
                        url = item.get("id", "")
                else:
                    url = item

                submission_id = _extract_submission_id(url)
                if not submission_id:
                    errors.append(
                        {
                            "index": idx,
                            "item": str(item)[:100],
                            "error": "Could not extract a submission ID from the URL/ID",
                        }
                    )
                    continue

                # Fetch the submission
                submission = await reddit.submission(id=submission_id)

                # Load top comments
                await submission.comments.replace_more(limit=0)
                actual_comments = [
                    c
                    for c in submission.comments.list()
                    if not isinstance(c, MoreComments)
                ]
                top_comments = sorted(
                    actual_comments,
                    key=lambda c: c.score,
                    reverse=True,
                )[:comment_count]

                # Extract comment data
                comments_data = []
                for comment in top_comments:
                    comment_author = comment.author
                    comments_data.append(
                        {
                            "author": comment_author.name if comment_author else None,
                            "score": comment.score,
                            "body": comment.body,
                            "created_utc": comment.created_utc,
                        }
                    )

                results.append(
                    {
                        "id": submission.id,
                        "subreddit": submission.subreddit.display_name,
                        "title": submission.title,
                        "score": submission.score,
                        "created_utc": submission.created_utc,
                        "permalink": submission.permalink,
                        "selftext": submission.selftext or "",
                        "num_comments": submission.num_comments,
                        "comments": comments_data,
                    }
                )
            except RedditError as e:
                errors.append(
                    {
                        "index": idx,
                        "item": str(item)[:100],
                        "error": str(e),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "index": idx,
                        "item": str(item)[:100],
                        "error": str(exc),
                    }
                )
    finally:
        await reddit.close()

    return {
        "results": results,
        "errors": errors,
        "metadata": {
            "total": len(items),
            "success_count": len(results),
            "error_count": len(errors),
        },
    }


async def fetch_thread(url: str, comment_count: int = 20) -> str:
    """Fetch a Reddit thread and render it as Markdown.

    Parameters
    ----------
    url : str
        URL of the Reddit thread (can be reddit.com or redd.it).
    comment_count : int, optional
        Maximum number of top comments to render (1-50). Default is 20.

    Returns
    -------
    str
        Markdown-formatted thread content.
    """
    comment_count = _clamp(comment_count, COMMENT_COUNT_MIN, COMMENT_COUNT_MAX)
    config = load_config()
    reddit = _create_reddit_client(config)

    submission_id = _extract_submission_id(url)
    if not submission_id:
        raise RedditError(f"Could not extract a submission ID from the URL: {url}")

    try:
        # asyncpraw fetches the submission by default (fetch=True)
        submission = await reddit.submission(id=submission_id)
    except Exception as e:
        raise RedditError(f"Failed to fetch Reddit thread: {e}") from e

    lines: list[str] = [
        f"# {submission.title}",
        f"**Score:** {submission.score}",
    ]

    author = submission.author
    if author is not None:
        lines.append(f"**Author:** /u/{author.name}")

    if submission.selftext:
        lines.extend(["", "## Selftext", "", submission.selftext])

    try:
        # Load top comments
        await submission.comments.replace_more(limit=0)
        # Filter out MoreComments objects (they don't have a score attribute)
        actual_comments = [
            c for c in submission.comments.list() if not isinstance(c, MoreComments)
        ]
        top_comments = sorted(
            actual_comments,
            key=lambda c: c.score,
            reverse=True,
        )[:comment_count]
    except Exception as e:
        raise RedditError(f"Failed to load Reddit comments: {e}") from e
    finally:
        await reddit.close()

    if top_comments:
        lines.append("")
        lines.append("## Top Comments")

        for comment in top_comments:
            comment_author = comment.author
            if comment_author is not None:
                header = (
                    f"### Comment by /u/{comment_author.name} (Score: {comment.score})"
                )
            else:
                header = f"### Comment (Score: {comment.score})"
            lines.extend(["", header, "", comment.body])

    return "\n".join(lines)
