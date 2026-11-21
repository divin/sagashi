"""YouTube transcript fetching via the Supadata API (https://supadata.ai).

Fetches a video's title and full transcript through the Supadata API using
the official Python SDK. Prefers manual English captions and falls back to
auto-generated or generated captions (``mode="auto"``). Requires an API key
from supadata.ai, provided via the ``SUPADATA_API_KEY`` environment variable.
"""

from __future__ import annotations

import time
from typing import Any

from supadata import Supadata, SupadataError
from supadata.types import BatchJob


class YoutubeTranscriptError(Exception):
    """YouTube transcript fetch error with a user-friendly, trace-free message."""


# Seconds between polls of an asynchronous transcript job.
_JOB_POLL_INTERVAL_SECONDS = 2.0

# Polls before giving up on an asynchronous transcript job (2 min at 2s).
_JOB_POLL_MAX_ATTEMPTS = 60


def _friendly_supadata_error(error: SupadataError) -> str:
    """Map a Supadata API error to a short, actionable message.

    Never includes stack traces or library internals: those leak server
    details to the calling model and waste context tokens.
    """
    code = (error.error or "").lower()
    combined = f"{error.message or ''} {error.details or ''}".lower()
    if "transcript" in code or "caption" in code:
        return "No captions are available for this video."
    if "auth" in code or "key" in code or "unauthorized" in combined:
        return (
            "Supadata rejected the API key. Check SUPADATA_API_KEY "
            "(get one at https://supadata.ai)."
        )
    if "quota" in code or "rate" in code:
        return "Supadata rate limit or quota exceeded. Try again later."
    if "not-found" in code or "not found" in combined:
        return "This video is unavailable (not found or private)."
    return "Supadata request failed: " + str(error)[:200]


def _transcript_text(content: Any) -> str:
    """Reduce a Transcript's content (plain text or chunk list) to plain text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [getattr(chunk, "text", "") or "" for chunk in content]
        return " ".join(part.strip() for part in parts if part.strip())
    return ""


def _poll_transcript_job(client: Supadata, job_id: str) -> Any:
    """Poll an asynchronous transcript job until it completes or fails."""
    for _ in range(_JOB_POLL_MAX_ATTEMPTS):
        job = client.transcript.get_job_status(job_id)
        if job.status == "completed" and job.result is not None:
            return job.result
        if job.status == "failed":
            detail = ""
            if job.error:
                detail = str(job.error.get("message", ""))[:200]
            raise YoutubeTranscriptError(
                "Supadata transcript job failed." + (f" {detail}" if detail else "")
            )
        time.sleep(_JOB_POLL_INTERVAL_SECONDS)
    raise YoutubeTranscriptError("Supadata transcript job timed out.")


def fetch_youtube_transcript(url: str, api_key: str | None = None) -> dict[str, str]:
    """Fetch a YouTube video's title and transcript.

    Returns ``{"title": ..., "transcript": ...}`` where the transcript is
    plain text with timing/formatting stripped. Raises
    ``YoutubeTranscriptError`` when no captions are available or the
    Supadata API fails. Synchronous (HTTP via the Supadata SDK) — callers
    should wrap it in ``asyncio.to_thread`` to avoid blocking the event loop.
    """
    if not api_key:
        raise YoutubeTranscriptError(
            "SUPADATA_API_KEY is not set. Get a free key at https://supadata.ai."
        )

    client = Supadata(api_key=api_key)

    # Title is cosmetic: a metadata failure must not sink the transcript.
    title = "Unknown_Title"
    try:
        title = client.metadata(url).title or title
    except SupadataError:
        pass

    try:
        transcript = client.transcript(url=url, lang="en", text=True, mode="auto")
    except SupadataError as error:
        raise YoutubeTranscriptError(_friendly_supadata_error(error)) from error

    if isinstance(transcript, BatchJob):
        transcript = _poll_transcript_job(client, transcript.job_id)

    text = _transcript_text(transcript.content)
    if not text:
        raise YoutubeTranscriptError("No captions are available for this video.")

    return {"title": title, "transcript": text}


__all__ = [
    "YoutubeTranscriptError",
    "fetch_youtube_transcript",
]
