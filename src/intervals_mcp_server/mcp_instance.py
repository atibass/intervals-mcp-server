"""
Shared MCP instance module.

This module provides a shared FastMCP instance that can be imported by both
the server module and tool modules without creating cyclic imports.
"""

import hmac
import os

from pydantic import AnyHttpUrl
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error

from intervals_mcp_server.api.client import setup_api_client


class StaticBearerTokenVerifier(TokenVerifier):
    """Validate a single pre-shared bearer token stored only in environment variables."""

    def __init__(self, expected_token: str) -> None:
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not hmac.compare_digest(token, self._expected_token):
            return None

        return AccessToken(
            token=token,
            client_id="intervals-mcp-readonly-client",
            scopes=["mcp:read"],
        )


def _auth_config() -> dict:
    """Build FastMCP auth kwargs when MCP_AUTH_TOKEN is configured."""
    auth_token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if not auth_token:
        return {}

    public_domain = os.getenv(
        "RAILWAY_PUBLIC_DOMAIN",
        "intervals-mcp-production-a661.up.railway.app",
    )
    issuer_url = os.getenv("MCP_AUTH_ISSUER_URL", f"https://{public_domain}")
    resource_url = os.getenv("MCP_RESOURCE_SERVER_URL", f"https://{public_domain}/sse")

    return {
        "token_verifier": StaticBearerTokenVerifier(auth_token),
        "auth": AuthSettings(
            issuer_url=AnyHttpUrl(issuer_url),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=["mcp:read"],
        ),
    }


mcp: FastMCP = FastMCP(
    "intervals-icu",
    lifespan=setup_api_client,
    host=os.getenv("FASTMCP_HOST", "0.0.0.0"),
    port=int(os.getenv("FASTMCP_PORT", os.getenv("PORT", "8000"))),
    **_auth_config(),
)  # pylint: disable=invalid-name
