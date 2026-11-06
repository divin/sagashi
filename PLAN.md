# Plan: Sagashi version 1.0

*Sagashi (探し, “search”)* — project name per the sibling naming convention (Tana, Koebako, Koemichi).

## Goal and architecture

A self-hosted MCP server that exposes web-search and page-fetching primitives. The server provides capabilities; the calling agent drives the research loop. Version 1.0 ships exactly three tools — `web_search`, `search_reddit`, and `fetch_page` — served over Streamable HTTP from a single Docker container, deployable like the sibling projects (GHCR image, `docker-compose.yaml`, `example.env`).

Key decisions and their rationale are recorded in the glossary (`GLOSSARY.md`) and ADR 0001 (Reddit content comes only from the official API).

## Shared implementation principles

- Tool descriptions are the interface: keep exactly three tools, with docstrings written as routing contracts for the model. Do not add a fourth tool without removing value elsewhere.
- Bake determinism into server code: Reddit Routing, the crawler blocklist, output caps, and timeouts are never model-facing parameters.
- Keep the `SearchProvider` seam narrow: one active provider, selected by configuration; a provider swap is a new adapter plus env names, not core changes.
- Reddit content only ever comes from the official read-only API (see ADR 0001); `fetch_page` fails loudly rather than falling back to crawling.
- Trim every tool output for a model context window; truncation is marked visibly.
- Credentials live in environment configuration only; never log tokens, secrets, or full fetched page content. Uruky's token travels inside the request URL (`i` parameter), so any logged URL or error message must redact it — pinned by a test.
- Match the sibling-project toolbox: uv, Python 3.12, `just` for format/lint, `src/` layout, tests alongside code.

## Phase 1: project scaffold

**Status: complete.**

- uv project layout under `src/sagashi/` with modules: `config`, `providers/`, `reddit`, `fetcher`, `server`; package, module, and entry point all named `sagashi`.
- Dependencies: `fastmcp`, `httpx`, `praw`, `crawl4ai`. Dev tooling: ruff (format + lint + import sort), pytest, mypy or basedmypy as the sibling repos use.
- `justfile` (sibling-repo style) with recipes per concern:
  - **Formatting/linting (local fix loop)**: `fmt`, `lint`, `isort` (ruff; default recipe runs all three).
  - **CI gate (non-mutating)**: `check` — type check (`ty`) plus `ruff format --check` and `ruff check` without fixes.
  - **Tests**: `test` — `uv run pytest tests -q`.
  - **Local runs**: `run` — start the server locally against `.env`; `crawl-check` — `crawl4ai-doctor` plus the example.com smoke crawl, for the Phase 5 crawler verification.
  - **Environment**: `sync` — `uv sync --dev`.
- CI (Phase 9) invokes only `just check` and `just test`; local convenience recipes stay out of the workflow.

## Phase 2: configuration and the provider seam

**Status: complete.**

- Typed config loaded from the environment: `SEARCH_PROVIDER` (default `uruky`), `URUKY_TOKEN`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, `FETCH_MAX_CHARS` (default 40000), `MCP_SEARCH_AUTH_TOKEN` (optional; unset means no auth, LAN-only deployment).
- Fail fast at startup: missing Uruky or Reddit credentials abort the launch (all three tools must be operational from the first connection); `MCP_SEARCH_AUTH_TOKEN` is the only optional variable.
- `SearchProvider` protocol returning normalized results (`url`, `title`, `description`, `date`); Uruky adapter implements it. No provider fallback logic in 1.0.

## Phase 3: `web_search`

**Status: complete.**

- Uruky adapter calls `GET https://uruky.com/search` with `q`, `f=json`, `i=<token>`, optional `w` mapped from `recency` (`day`/`week`/`month`/`year`/`all`).
- First page only, no pagination parameter; compact Markdown list of title, URL, snippet, date. Explicit error text on HTTP/auth failures.

## Phase 4: Reddit module

**Status: complete.**

- PRAW client with read scope; `search_reddit(query, time_filter)` over `subreddit("all")`, relevance sort, ~10 results as Markdown list.
- Thread-to-Markdown rendering: title, selftext, top ~20 score-sorted comments with author and score; `replace_more(limit=0)`.

## Phase 5: `fetch_page` and Reddit Routing

**Status: complete. crawl4ai verified locally.**

### Local crawler verification (done on the dev machine)

- Core package only, no `[torch]`/`[transformer]`/`[all]` extras — they pull large model dependencies the Markdown pipeline does not need.
- `crawl4ai-setup` run once after install.
- `crawl4ai-doctor` passes (verified with crawl4ai 0.9.5: crawling test passed).
- Smoke crawl of `https://www.example.com` returns Markdown.

- One warm `AsyncWebCrawler` (headless Chromium) held for the process lifetime; stealth and timeout settings baked in.
- Reddit Routing: `reddit.com`/`redd.it` URLs go to the Reddit module; reddit.com sits on the crawler blocklist (ADR 0001). Failure without Reddit credentials is a loud, explanatory error.
- Non-Reddit pages return clean Markdown truncated at `FETCH_MAX_CHARS` with a visible truncation marker.

## Phase 6: FastMCP server assembly

**Status: complete.**

- FastMCP server on Streamable HTTP, bound for local/trusted-network use; optional bearer-token auth enabled when `MCP_SEARCH_AUTH_TOKEN` is set.
- Tool docstrings carry the routing contract (search → decide → fetch; Reddit questions → `search_reddit`).
- Sync PRAW calls wrapped for the async tool surface without blocking the event loop.

## Phase 7: tests

**Status: not started.**

- Unit tests with mocked Uruky HTTP (success, auth failure, malformed JSON), mocked PRAW (search, thread rendering, deleted threads), Reddit Routing (reddit.com, redd.it, blocklist), truncation marker, and config validation.
- No live network calls in the test suite.

## Phase 8: Docker packaging

**Status: in progress.**

- `Dockerfile`: uv-based Python 3.12 image with Chromium installed for Crawl4AI; non-root runtime user; `CRAWL4AI_NO_CLOUD_NOTICE=1` set in the image so container logs stay free of the Crawl4AI Cloud upsell banner.
- `docker-compose.yaml` with default ports (remapping left to the deployer), `example.env` documenting every variable including Reddit app setup steps.

### Crawler-in-Docker verification checklist (must all pass before 1.0)

- `crawl4ai-setup` (or the equivalent Playwright browser + system-library install) runs during the image build, after the Python dependencies are installed.
- Chromium works under the non-root runtime user: headless launch needs `--no-sandbox` (or equivalent browser args) — verify a crawl succeeds as the container user, not just as root.
- `/dev/shm` is sufficient for Chromium: set `shm_size` in compose or pass `--disable-dev-shm-usage` in browser args; a crash on the first crawl means this was missed.
- Container smoke crawl: running `crawl4ai-doctor` (or an equivalent example.com crawl) inside the built image passes.
- Warm-browser proof: the second `fetch_page` call in a running container is fast (no new Chromium spawn), confirming the single long-lived `AsyncWebCrawler` behaves as designed.

## Phase 9: CI and release

**Status: complete.**

- GitHub Actions: lint/test workflow on push and PR, running `just check` and `just test`; image publish to GHCR on version tags, plain version tags only, `:latest` pointing at the newest release (sibling-project convention). Images build for amd64 only. The publish workflow builds the image and runs the Phase 8 crawler-in-Docker verification before pushing.

## Phase 10: docs and live verification

**Status: in progress.**

- README in the sibling-repo shape: what it is, how it works, quick start, tool reference, privacy notes, development section; Reddit app creation documented as a setup step even though credentials already exist.
- Live verification against real Uruky and Reddit credentials, and end-to-end registration in both sign-off clients: pi and the llama.cpp web UI (which supports remote HTTP MCP servers — verified working as of the Oct 2026 llama.cpp releases, though still undocumented).

## Version 1.0 boundary

Version 1.0 is done when all phases above are implemented, tested, documented, published to GHCR, and verified live. Explicitly deferred to later versions: `search_images` (Uruky `/image-search`), the fetch fallback chain (stealth retry, Jina/Kagi Extract), Arctic Shift for historical Reddit threads, and a one-shot server-side `research()` tool.
