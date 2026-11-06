#!/usr/bin/env bash
# Live smoke test for the Sagashi MCP server.
#
# Exercises every tool endpoint against a running server using the
# `fastmcp` CLI (installed on demand via `uvx`).
#
# Usage:
#   scripts/smoke_test.sh [server-url]
#
# Environment:
#   SAGASHI_URL   Default server URL (overridden by the first argument).
#                 Defaults to http://localhost:8000/mcp
#
# Requires a server started with `just run` (or Docker) and credentials in .env.

set -uo pipefail

SERVER_URL="${1:-${SAGASHI_URL:-http://localhost:8000/mcp}}"
FASTMCP=(uvx --from "fastmcp-slim[server]" fastmcp)

pass=0
fail=0

ok() {
    echo "PASS: $1"
    pass=$((pass + 1))
}

ko() {
    echo "FAIL: $1"
    fail=$((fail + 1))
}

# Run a tool call and capture JSON; sets RESULT_JSON and RESULT_ERR.
call_tool() {
    RESULT_JSON=$("${FASTMCP[@]}" call "$SERVER_URL" "$@" --json 2>/dev/null)
    CALL_STATUS=$?
    RESULT_IS_ERROR=$(echo "$RESULT_JSON" | jq -r '.is_error | tostring')
    RESULT_TEXT=$(echo "$RESULT_JSON" | jq -r '.content[0].text // empty')
}

echo "=== Sagashi live smoke test ==="
echo "Server: $SERVER_URL"
echo

# 0. Server reachability
echo "--- Listing tools ---"
LIST_JSON=$("${FASTMCP[@]}" list "$SERVER_URL" --json 2>/dev/null)
if [[ $? -ne 0 ]]; then
    echo "FAIL: server unreachable at $SERVER_URL (is it running? try: just run)"
    exit 1
fi

TOOL_COUNT=$(echo "$LIST_JSON" | jq '.tools | length')
if [[ "$TOOL_COUNT" -eq 3 ]]; then ok "server exposes 3 tools (found: $TOOL_COUNT)"; else ko "server exposes 3 tools (found: $TOOL_COUNT)"; fi
for tool in web_search search_reddit fetch_page; do
    if echo "$LIST_JSON" | jq -e --arg t "$tool" '.tools[] | select(.name == $t)' >/dev/null; then
        ok "tool '$tool' is registered"
    else
        ko "tool '$tool' is registered"
    fi
done
echo

# 1. web_search
echo "--- web_search ---"
call_tool web_search query=test
if [[ "$RESULT_IS_ERROR" == "false" ]]; then ok "web_search returns no error"; else ko "web_search returns no error ($RESULT_TEXT)"; fi
if echo "$RESULT_TEXT" | grep -q -- '- \*\*'; then ok "web_search returns markdown results"; else ko "web_search returns markdown results"; fi
echo

# 2. search_reddit
echo "--- search_reddit ---"
call_tool search_reddit query=python
if [[ "$RESULT_IS_ERROR" == "false" ]]; then ok "search_reddit returns no error"; else ko "search_reddit returns no error ($RESULT_TEXT)"; fi
if echo "$RESULT_TEXT" | grep -q 'reddit.com'; then ok "search_reddit returns reddit.com links"; else ko "search_reddit returns reddit.com links"; fi
echo

# 3. fetch_page (normal web page)
echo "--- fetch_page (web) ---"
call_tool fetch_page 'url=https://www.python.org'
if [[ "$RESULT_IS_ERROR" == "false" ]]; then ok "fetch_page returns no error"; else ko "fetch_page returns no error ($RESULT_TEXT)"; fi
if [[ ${#RESULT_TEXT} -gt 200 ]]; then ok "fetch_page returns page content (${#RESULT_TEXT} chars)"; else ko "fetch_page returns page content"; fi
echo

# 4. fetch_page Reddit URL blocking (must fail with blocked message)
echo "--- fetch_page (reddit blocking) ---"
call_tool fetch_page 'url=https://www.reddit.com/r/Python/'
if [[ "$RESULT_IS_ERROR" == "true" ]]; then ok "fetch_page blocks reddit.com URLs"; else ko "fetch_page blocks reddit.com URLs"; fi
if echo "$RESULT_TEXT" | grep -q 'blocked'; then ok "blocked message mentions reddit.com"; else ko "blocked message mentions reddit.com"; fi
echo

# 5. fetch_page redd.it routing (routes to Reddit API, returns thread)
echo "--- fetch_page (redd.it routing) ---"
call_tool fetch_page 'url=https://redd.it/abc123'
if [[ "$RESULT_IS_ERROR" == "false" ]]; then ok "fetch_page routes redd.it via official API"; else ko "fetch_page routes redd.it via official API ($RESULT_TEXT)"; fi
if echo "$RESULT_TEXT" | grep -q '^# '; then ok "routed thread returns markdown thread"; else ko "routed thread returns markdown thread"; fi
echo

echo "=== Results: $pass passed, $fail failed ==="
[[ $fail -eq 0 ]]
