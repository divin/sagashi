# Sagashi 🔍

**Sagashi is a privacy-focused, self-hostable MCP (Model Context Protocol) server for web search, Reddit search, and page fetching.** It gives MCP-compatible agents (llama.cpp, Claude, Cursor, and others) three tools: web search via the [Uruky](https://uruky.com) API, Reddit search via the official Reddit API, and clean Markdown page fetching via [Crawl4AI](https://github.com/unclecode/crawl4ai). Everything runs on your own host with your own API credentials; no queries or results are stored. The name comes from *sagashi* (捜し, “search”).

AI tools were used during development.

## 🧭 How it works

```mermaid
flowchart LR
    A[MCP client] -->|Streamable HTTP| B[Sagashi MCP server]
    B -->|web_search| C[Uruky API]
    B -->|search_reddit| D[Reddit official API]
    B -->|fetch_page| E[Crawl4AI / headless Chromium]
    E -. redd.it URLs .-> D
```

A single long-lived process serves three tools over Streamable HTTP. A lexical reranker boosts results whose titles and snippets actually match the query before they reach the agent. Reddit URLs are routed deterministically: `reddit.com` links are refused (ToS compliance), `redd.it` links are served through the official API instead — the crawler never touches Reddit.

## 🛠️ Tools

| Tool | Use for | Parameters |
|------|---------|------------|
| `web_search` | General web search queries | `query` (required), `recency` (day/week/month/year) |
| `search_reddit` | Reddit-specific questions or explicit Reddit requests | `query` (required), `time_filter` (day/week/month/year/all, default `month`), `limit` (1–25, default 10) |
| `fetch_page` | Fetching a specific URL | `url` (required), `max_chars` (≥ 1000, default `FETCH_MAX_CHARS`) |

All tools return compact Markdown sized for agent context windows; large pages are truncated with a visible marker at the configured limit. Parameters are clamped server-side, so agents can freely adjust them.

## 🚀 Quick start

1. Create a Reddit app at [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) (script type) to get `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`. Copy `example.env` to `.env` and fill in the Uruky and Reddit credentials.
2. Start the server:

   ```sh
   uv sync --locked
   uv run sagashi
   ```

3. Point your MCP client at `http://localhost:8000/mcp`. Verify everything works end-to-end with:

   ```sh
   just smoke   # exercises every tool against the running server
   ```

### Docker

```sh
docker run -p 3000:3000 --env-file .env sagashi:latest
```

Published images are available at `ghcr.io/divin/sagashi` for tagged releases. The container runs as a non-root user and includes headless Chromium for page fetching.

## ⚙️ Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `URUKY_TOKEN` | Yes | Uruky API token for web search |
| `REDDIT_CLIENT_ID` | Yes | Reddit app client ID |
| `REDDIT_CLIENT_SECRET` | Yes | Reddit app client secret |
| `REDDIT_USER_AGENT` | Yes | Reddit app user agent string |
| `MCP_SEARCH_AUTH_TOKEN` | No | Reserved for external bearer-token auth setups |
| `FETCH_MAX_CHARS` | No | Truncation ceiling for page content (default: 40000) |

Missing required variables abort startup with a clear error; `.env` files are loaded automatically.

## 🔒 Privacy

- **Reddit content only from the official API**: crawling `reddit.com` is hard-blocked to comply with Reddit's Terms of Service and to avoid IP flagging; even if an agent insists, the server refuses. See [ADR 0001](docs/adr/0001-reddit-content-comes-only-from-the-official-api.md).
- **Stateless**: Sagashi is a search proxy. Queries, results, and fetched pages are not stored.
- **Your credentials, your host**: outbound traffic goes only to Uruky, Reddit's API, and the pages you fetch.

## 🧪 Development

Use Python 3.12, [`uv`](https://github.com/astral-sh/uv), and [`just`](https://just.systems/man/en/):

```sh
uv sync --dev
just       # Format, lint, and sort imports
just check # Type, format, and lint checks
just test  # Test suite
just smoke # Live smoke test against a running server
```

Unit tests mock all network access; the smoke test exercises real endpoints.

## 📦 Releases

Tags trigger the CI pipeline: the `test` job runs checks and tests on every push and pull request, and version tags additionally publish a Docker image to GHCR and create a GitHub Release with generated notes. Check out [Koebako](https://github.com/divin/koebako) and [Koemichi](https://github.com/divin/koemichi) for companion self-hosted projects.

## 🙏 Credits

- [FastMCP](https://github.com/jlowin/fastmcp) for the MCP server framework
- [Crawl4AI](https://github.com/unclecode/crawl4ai) for web crawling
- [asyncpraw](https://github.com/praw-dev/asyncpraw) for Reddit API access
- [Uruky](https://uruky.com) for web search

## 📄 License

Apache License 2.0 — see [LICENSE](LICENSE).
