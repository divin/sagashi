"""Sagashi CLI entry point."""

from sagashi.server import build_server


def main() -> None:
    """Run the Sagashi MCP server."""
    server = build_server()
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
