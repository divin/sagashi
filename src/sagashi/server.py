"""FastMCP server assembly."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def build_server() -> FastMCP:
    """Build and return the FastMCP server instance."""
    raise NotImplementedError
