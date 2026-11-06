# Sagashi

Self-hosted MCP server for web search and page fetching.

## Features

- **Web search**: via Uruky API
- **Reddit search**: via official Reddit API (PRAW)
- **Page fetching**: via Crawl4AI (headless Chromium)
- **Reddit routing**: automatic detection and routing for Reddit URLs
- **Auth support**: optional bearer-token authentication

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (optional, for containerized deployment)

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `URUKY_TOKEN` | Yes | Uruky API token for web search |
| `REDDIT_CLIENT_ID` | Yes | Reddit app client ID |
| `REDDIT_CLIENT_SECRET` | Yes | Reddit app client secret |
| `REDDIT_USER_AGENT` | Yes | Reddit app user agent string |
| `MCP_SEARCH_AUTH_TOKEN` | No | Bearer token for MCP server auth |
| `FETCH_MAX_CHARS` | No | Max characters for page content (default: 40000) |

### Running with Docker

```bash
# Create .env file
cp example.env .env
# Edit .env with your credentials

# Run with Docker
docker run -p 3000:3000 --env-file .env sagashi:latest
```

### Running Locally

```bash
# Install dependencies
uv sync --dev

# Run
uv run sagashi
```

## Tool Reference

### `web_search(query, recency?)`

Search the web using Uruky.

**Use for**: General web search queries.

**Returns**: Compact Markdown list of search results with title, URL, snippet, and date.

### `search_reddit(query, time_filter?)`

Search Reddit via the official API.

**Use for**: Reddit-specific questions or when the user explicitly asks about Reddit.

**Returns**: Compact Markdown list of search results.

### `fetch_page(url)`

Fetch a web page and return its Markdown content.

**Use for**: When the user provides a specific URL to fetch.

**Returns**: Page content as Markdown. If the URL is from `reddit.com` or `redd.it`, it will be routed to `search_reddit` instead.

**Note**: Content is truncated at `FETCH_MAX_CHARS` (default 40000) with a visible marker.

## Privacy Notes

- **Reddit content**: Only comes from the official API. Crawling `reddit.com` is blocked to comply with Reddit's Terms of Service and avoid IP flagging.
- **No data retention**: Sagashi is a stateless search proxy. No user queries or search results are stored.

## Development

```bash
# Install dependencies
uv sync --dev

# Run checks
just check

# Run tests
just test

# Format code
just fmt
```

## Deployment

### Docker Compose

```yaml
version: '3.8'

services:
  sagashi:
    image: sagashi:latest
    ports:
      - "3000:3000"
    env_file:
      - .env
```

## Credits

- [FastMCP](https://github.com/jlowin/fastmcp) for the MCP server framework
- [Crawl4AI](https://github.com/unclecode/crawl4ai) for web crawling
- [PRAW](https://github.com/praw-dev/praw) for Reddit API access
