"""Reddit search module (asyncpraw)."""

from __future__ import annotations

import re

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


async def search_reddit(query: str, time_filter: str = "month", limit: int = 10) -> str:
    """Search Reddit via the official API and render a markdown summary.

    Parameters
    ----------
    query : str
        Search query string.
    time_filter : str, optional
        Time range to search (day, week, month, year, all). Default is "month".
    limit : int, optional
        Maximum number of results (1-25). Default is 10.

    Returns
    -------
    str
        Markdown-formatted search results.
    """
    limit = _clamp(limit, REDDIT_LIMIT_MIN, REDDIT_LIMIT_MAX)
    config = load_config()
    reddit = _create_reddit_client(config)

    try:
        subreddit = await reddit.subreddit("all")
        submissions = [
            sub
            async for sub in subreddit.search(
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
