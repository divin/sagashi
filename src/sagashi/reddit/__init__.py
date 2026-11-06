"""Reddit search module (asyncpraw)."""

from __future__ import annotations

from asyncpraw import Reddit
from asyncpraw.models import MoreComments

from sagashi.config import Config, load_config


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


async def search_reddit(query: str, time_filter: str = "month") -> str:
    """Search Reddit via the official API and render a markdown summary.

    Parameters
    ----------
    query : str
        Search query string.
    time_filter : str, optional
        Time range to search (day, week, month, year, all). Default is "month".

    Returns
    -------
    str
        Markdown-formatted search results.
    """
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
                limit=10,
            )
        ]
    except Exception as e:
        raise RedditError(f"Reddit API error: {e}") from e
    finally:
        await reddit.close()

    if not submissions:
        return "No Reddit results found."

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


async def fetch_thread(url: str) -> str:
    """Fetch a Reddit thread and render it as Markdown.

    Parameters
    ----------
    url : str
        URL of the Reddit thread (can be reddit.com or redd.it).

    Returns
    -------
    str
        Markdown-formatted thread content.
    """
    config = load_config()
    reddit = _create_reddit_client(config)

    try:
        # Extract submission ID from URL (last path segment, strip query/fragment)
        submission_id = url.split("/")[-1].split("?")[0].split("#")[0]
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
        )[:20]
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
