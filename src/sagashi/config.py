"""Configuration and settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


class ConfigError(Exception):
    """Configuration error with a user-friendly message."""


@dataclass
class Config:
    """Server configuration loaded from environment."""

    search_provider: Literal["uruky"]
    uruky_token: str | None
    reddit_client_id: str | None
    reddit_client_secret: str | None
    reddit_user_agent: str | None
    fetch_max_chars: int
    mcp_auth_token: str | None

    def validate(self) -> None:
        """Require Uruky and Reddit credentials (fail-fast)."""
        if not self.uruky_token:
            raise ConfigError("URUKY_TOKEN is required")
        if (
            not self.reddit_client_id
            or not self.reddit_client_secret
            or not self.reddit_user_agent
        ):
            raise ConfigError(
                "REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, and REDDIT_USER_AGENT are required"
            )


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        search_provider="uruky",
        uruky_token=os.getenv("URUKY_TOKEN"),
        reddit_client_id=os.getenv("REDDIT_CLIENT_ID"),
        reddit_client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        reddit_user_agent=os.getenv("REDDIT_USER_AGENT"),
        fetch_max_chars=int(os.getenv("FETCH_MAX_CHARS", "40000")),
        mcp_auth_token=os.getenv("MCP_SEARCH_AUTH_TOKEN"),
    )
