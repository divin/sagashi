"""Optional bearer-token authentication.

If MCP_SEARCH_AUTH_TOKEN is set, every MCP request must carry
``Authorization: Bearer <token>``. If it is unset, the server is open —
acceptable on a trusted local network, the intended deployment for Sagashi.
"""

from __future__ import annotations

import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier


class StaticTokenVerifier(TokenVerifier):
    """Verify a single configured bearer token; no OAuth machinery."""

    def __init__(self, token: str) -> None:
        super().__init__(required_scopes=None)
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        """Accept only the exact configured token."""
        if not hmac.compare_digest(token, self._token):
            return None
        return AccessToken(
            client_id="sagashi-client", token=token, scopes=[], claims={}
        )
