# Sagashi (探し, "search")

A self-hosted MCP server that exposes web-search and page-fetching primitives for agent-driven research loops.

## Features

- **Three primitives**: `web_search`, `search_reddit`, `fetch_page`
- **Uruky primary**, swappable provider via adapter seam
- **Reddit content only from official API** (deterministic routing + blocklist)
- **Embedded Crawl4AI** (no sidecar container; single-container deployment)
- **Streamable HTTP transport**, optional bearer token auth
- **Docker** ready (`Dockerfile`, `docker-compose.yaml`, `example.env`)

## Quick start

```bash
# Install dependencies
uv sync

# Run the server locally
uv run sagashi
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `URUKY_TOKEN` | Yes | Uruky API token for web search |
| `REDDIT_CLIENT_ID` | Yes | Reddit app client ID |
| `REDDIT_CLIENT_SECRET` | Yes | Reddit app client secret |
| `REDDIT_USER_AGENT` | Yes | Reddit app user agent string |
| `MCP_SEARCH_AUTH_TOKEN` | No | Bearer token for MCP endpoint auth |

See `example.env` for a template.

## License

Apache-2.0 — see [LICENSE](LICENSE).
