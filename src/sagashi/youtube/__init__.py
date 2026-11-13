"""YouTube transcript fetching via yt-dlp.

Fetches a video's title and full transcript by pulling captions through
yt-dlp. Prefers manual English subtitles, falls back to auto-generated
English. YouTube bot-checks the server's IP, so a cookies.txt file
(Netscape format) is used when configured; export it once with a browser
extension while signed in to YouTube.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any


class YoutubeTranscriptError(Exception):
    """YouTube transcript fetch error with a user-friendly, trace-free message."""


# Seconds per yt-dlp invocation before giving up.
_YTDLP_TIMEOUT = 120


def _yt_dlp() -> str:
    """Resolve the yt-dlp binary, raising a friendly error if missing."""
    binary = shutil.which("yt-dlp")
    if binary is None:
        raise YoutubeTranscriptError(
            "yt-dlp is not installed on the server. Install it with 'uv add yt-dlp'."
        )
    return binary


def _friendly_ytdlp_error(stderr: str) -> str:
    """Map a yt-dlp error to a short, actionable message.

    Never includes stack traces or library internals: those leak server
    details to the calling model and waste context tokens.
    """
    lowered = stderr.lower()
    if "read-only file system" in lowered:
        return (
            "The cookies file is on a read-only filesystem, but yt-dlp needs "
            "to rewrite it after every run (YouTube rotates session cookies). "
            "Mount the cookies file read-write."
        )
    if "sign in to confirm" in lowered:
        return (
            "YouTube bot-check blocked the request. Provide a cookies.txt "
            "(Netscape format) at YOUTUBE_COOKIES_FILE, exported while "
            "signed in to YouTube."
        )
    if "no subtitles" in lowered or "no captions" in lowered:
        return "No English captions available for this video."
    if "private video" in lowered:
        return "This video is private."
    if "unavailable" in lowered:
        return "This video is unavailable."
    if "age" in lowered and "confirm" in lowered:
        return "This video is age-restricted; cookies required."
    return "yt-dlp failed: " + stderr.strip()[:200]


def _js_runtime_args() -> list[str]:
    """Pick an available JavaScript runtime for yt-dlp, if any.

    yt-dlp's YouTube extraction expects a JS runtime; only deno is enabled
    by default, so point it at node (bundled in the Docker image) when
    present. Without any runtime extraction is deprecated and some formats
    may be missing.
    """
    if shutil.which("node"):
        return ["--js-runtimes", "node"]
    return []


def _run_yt_dlp(url: str, extra_args: list[str], cookies_path: str | None) -> str:
    """Run yt-dlp and return stdout; raise a friendly error on failure."""
    cmd = [_yt_dlp(), "--no-warnings", *_js_runtime_args()]
    if cookies_path and os.path.exists(cookies_path):
        cmd += ["--cookies", cookies_path]
    cmd += extra_args + [url]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=_YTDLP_TIMEOUT,
        )
    except subprocess.CalledProcessError as e:
        raise YoutubeTranscriptError(_friendly_ytdlp_error(e.stderr or "")) from e
    except subprocess.TimeoutExpired as e:
        raise YoutubeTranscriptError(
            "yt-dlp timed out while fetching the video."
        ) from e
    except FileNotFoundError as e:
        raise YoutubeTranscriptError("yt-dlp is not installed on the server.") from e
    return result.stdout


def pick_english_lang(info: dict[str, Any]) -> tuple[str, bool] | None:
    """Return (lang_code, is_auto) for the best English caption track, or None."""
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    preferred = ["en", "en-US", "en-GB"]

    # 1. Manual, preferred order
    for lang in preferred:
        if lang in subs:
            return lang, False
    # 2. Any manual en*
    for lang in subs:
        if lang.startswith("en"):
            return lang, False
    # 3. Auto, preferred order
    for lang in preferred:
        if lang in auto:
            return lang, True
    # 4. Any auto en*
    for lang in auto:
        if lang.startswith("en"):
            return lang, True
    return None


def _get_metadata(url: str, cookies_path: str | None) -> dict[str, Any]:
    """Fetch video metadata (title, caption listings) via yt-dlp."""
    stdout = _run_yt_dlp(url, ["--dump-json", "--skip-download"], cookies_path)
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise YoutubeTranscriptError(
            "yt-dlp returned unexpected metadata output."
        ) from e


def _download_subtitle(
    url: str,
    lang: str,
    is_auto: bool,
    outdir: str,
    cookies_path: str | None,
) -> str:
    """Use yt-dlp to download the json3 subtitle file. Returns the file path."""
    args = ["--skip-download", "--sub-format", "json3", "--sub-langs", lang]
    args += ["--write-auto-subs"] if is_auto else ["--write-subs"]
    args += ["-o", os.path.join(outdir, "%(id)s.%(ext)s")]
    _run_yt_dlp(url, args, cookies_path)

    matches = glob.glob(os.path.join(outdir, "*.json3"))
    if not matches:
        raise YoutubeTranscriptError("yt-dlp did not produce a caption file.")
    return matches[0]


def extract_text_from_json3(path: str) -> str:
    """Strip timing/formatting from a json3 caption file, returning plain text."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    text_parts: list[str] = []
    for event in data.get("events", []) or []:
        for seg in event.get("segs", []) or []:
            text = seg.get("utf8", "")
            if text.strip() and text != "\n":
                text_parts.append(text.strip())
    return " ".join(text_parts)


def fetch_youtube_transcript(
    url: str, cookies_path: str | None = None
) -> dict[str, str]:
    """Fetch a YouTube video's title and transcript.

    Returns ``{"title": ..., "transcript": ...}`` where the transcript is
    plain text with timing/formatting stripped. Raises
    ``YoutubeTranscriptError`` when no English captions are available or
    yt-dlp fails. Synchronous (yt-dlp subprocess) — callers should wrap it
    in ``asyncio.to_thread`` to avoid blocking the event loop.
    """
    _yt_dlp()  # fail fast if the binary is missing
    info = _get_metadata(url, cookies_path)
    title = info.get("title", "Unknown_Title")

    pick = pick_english_lang(info)
    if pick is None:
        raise YoutubeTranscriptError(f"No English subtitles found for video: {title}")
    lang, is_auto = pick

    tmpdir = tempfile.mkdtemp(prefix="sagashi-yt-transcript-")
    try:
        sub_path = _download_subtitle(url, lang, is_auto, tmpdir, cookies_path)
        transcript = extract_text_from_json3(sub_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return {"title": title, "transcript": transcript}


__all__ = [
    "YoutubeTranscriptError",
    "extract_text_from_json3",
    "fetch_youtube_transcript",
    "pick_english_lang",
]
