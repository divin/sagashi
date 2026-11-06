# Reddit content comes only from the official API

Reddit content (search results and thread pages) is served exclusively through Reddit's official read-only API. Reddit Routing inside Fetch is deterministic server code: any `reddit.com`/`redd.it` URL is handled by the Reddit Source and never reaches the crawler, and reddit.com is on the crawler's blocklist so the path is closed even if routing is bypassed. Reddit Search is a separate tool the model may choose when the user asks about Reddit.

Crawling reddit.com violates Reddit's ToS and risks flagging the host's IP, which would endanger other services on the same server. Official API access is free at agent-scale volumes (100 queries/min) and reaches fresh Reddit content that search engines index poorly, so scraping buys nothing worth the risk.

## Considered options

- Letting the model route (rejected: a model can "helpfully" fall back to scraping; only deterministic code can close the path).
- Scraping with stealth measures (rejected: ToS violation and IP-flagging risk on a shared self-hosted box).

## Consequences

- Missing Reddit credentials abort startup (the project fails fast), so at runtime Reddit-source unavailability means API errors; those surface as loud tool errors with no crawl fallback, by design.
- Historical/deleted threads are unreachable until the Arctic Shift fallback is considered (deferred to a later version, along with other fetch fallbacks).
