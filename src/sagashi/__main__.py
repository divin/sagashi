"""Sagashi CLI entry point."""

import os

from sagashi.server import build_server


def main() -> None:
    """Run the Sagashi MCP server."""
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    server = build_server()
    server.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
