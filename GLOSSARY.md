# Sagashi

*Sagashi (探し, “search”)* is a self-hosted MCP server that exposes web-search and page-fetching primitives so a calling agent drives its own research loop. The server provides capabilities; the client decides when to use them.

## Language

### Tools

**Search**:
A query against a web search provider, returned as a ranked list of titles, URLs, and snippets. The entry point of the research loop; the agent decides afterwards whether any result is worth fetching.
_Avoid_: research (reserved for a possible future server-side loop tool), lookup

**Reddit Search**:
A query against Reddit's own API, bypassing the web search provider entirely. Serves "what do people say about X" queries that search engines index poorly.
_Avoid_: subreddit search, social search

**Fetch**:
Retrieval of one URL as clean Markdown, trimmed for a model context window. The step after Search when snippets are not enough.
_Avoid_: scrape, crawl (those imply the agent drives Crawl4AI's knobs)

### Sources

**Search Provider**:
The generic backend behind Search (Uruky today, swappable with e.g. Kagi via configuration). One provider is active at a time; no fallback chain in v1.
_Avoid_: search engine, backend

**Reddit Source**:
Reddit accessed through its official read-only API. The only source with a purpose-built access path besides the Search Provider.
_Avoid_: forum

**Page Source**:
Any non-Reddit URL, fetched through the embedded crawler.

### Routing

**Reddit Routing**:
The deterministic rule that a reddit.com or redd.it URL inside a Fetch is served by the Reddit Source and is never crawled. Not a model decision; encoded in server code. Search results pointing at Reddit flow through the same rule.
_Avoid_: smart routing, auto-detect
