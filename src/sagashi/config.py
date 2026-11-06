"""Configuration and settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


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
        missing = [
            name
            for name, value in (
                ("REDDIT_CLIENT_ID", self.reddit_client_id),
                ("REDDIT_CLIENT_SECRET", self.reddit_client_secret),
                ("REDDIT_USER_AGENT", self.reddit_user_agent),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"Reddit credentials incomplete, missing: {', '.join(missing)}"
            )


def _get_env_var(name: str) -> str | None:
    """Get environment variable, returning None if empty or unset."""
    value = os.getenv(name)
    return value if value else None


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        search_provider="uruky",
        uruky_token=_get_env_var("URUKY_TOKEN"),
        reddit_client_id=_get_env_var("REDDIT_CLIENT_ID"),
        reddit_client_secret=_get_env_var("REDDIT_CLIENT_SECRET"),
        reddit_user_agent=_get_env_var("REDDIT_USER_AGENT"),
        fetch_max_chars=int(os.getenv("FETCH_MAX_CHARS", "40000")),
        mcp_auth_token=_get_env_var("MCP_SEARCH_AUTH_TOKEN"),
    )
