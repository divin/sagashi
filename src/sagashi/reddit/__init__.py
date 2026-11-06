"""Reddit search module."""

from __future__ import annotations

from praw import Reddit

from sagashi.config import Config, load_config


class RedditError(Exception):
    """Reddit API error with a user-friendly message."""


def _create_reddit_client(config: Config) -> Reddit:
    """Create a PRAW Reddit client from configured credentials."""
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
        submissions = list(
            reddit.subreddit("all").search(
                query,
                sort="relevance",
                time_filter=time_filter,
                limit=10,
            )
        )
    except Exception as e:
        raise RedditError(f"Reddit API error: {e}") from e

    if not submissions:
        return "No Reddit results found."

    lines: list[str] = []
    for submission in submissions:
        lines.append(
            f"- **{submission.title}** (https://reddit.com{submission.permalink})"
        )
        lines.append(f"  Score: {submission.score}")
        if submission.author:
            lines.append(f"  Author: /u/{submission.author.name}")

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
        # Extract submission ID from URL
        submission_id = url.split("/")[-1] if "/" in url else url
        submission = reddit.submission(id=submission_id)
        submission._fetch()
    except Exception as e:
        raise RedditError(f"Failed to fetch Reddit thread: {e}") from e

    lines: list[str] = [
        f"# {submission.title}",
        f"**Score:** {submission.score}",
    ]

    if submission.author:
        lines.append(f"**Author:** /u/{submission.author.name}")

    if submission.selftext:
        lines.extend(["", "## Selftext", "", submission.selftext])

    # Load top comments
    submission.comments.replace_more(limit=0)
    top_comments = sorted(
        submission.comments.list(),
        key=lambda c: c.score,
        reverse=True,
    )[:20]

    if top_comments:
        lines.append("")
        lines.append("## Top Comments")

        for comment in top_comments:
            lines.extend(
                [
                    "",
                    f"### Comment by /u/{comment.author.name} (Score: {comment.score})",
                    "",
                    comment.body,
                ]
            )

    return "\n".join(lines)
