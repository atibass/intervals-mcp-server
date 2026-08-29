"""
Shared MCP instance module.

This module provides a shared FastMCP instance that can be imported by both
the server module and tool modules without creating cyclic imports.
"""

import html
import os

from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error

from intervals_mcp_server.api.client import setup_api_client
from intervals_mcp_server.oauth_provider import PrivateOAuthProvider


PUBLIC_DOMAIN = os.getenv(
    "RAILWAY_PUBLIC_DOMAIN",
    "intervals-mcp-production-a661.up.railway.app",
)
ISSUER_URL = os.getenv("MCP_AUTH_ISSUER_URL", f"https://{PUBLIC_DOMAIN}").rstrip("/")
RESOURCE_URL = os.getenv("MCP_RESOURCE_SERVER_URL", f"https://{PUBLIC_DOMAIN}/sse")

_oauth_provider = PrivateOAuthProvider(ISSUER_URL, RESOURCE_URL)

mcp: FastMCP = FastMCP(
    "intervals-icu",
    lifespan=setup_api_client,
    host=os.getenv("FASTMCP_HOST", "0.0.0.0"),
    port=int(os.getenv("FASTMCP_PORT", os.getenv("PORT", "8000"))),
    auth_server_provider=_oauth_provider,
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(ISSUER_URL),
        resource_server_url=AnyHttpUrl(RESOURCE_URL),
        required_scopes=["mcp:read"],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mcp:read"],
            default_scopes=["mcp:read"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    ),
)  # pylint: disable=invalid-name


@mcp.custom_route("/login", methods=["GET"])
async def oauth_login_page(request: Request) -> HTMLResponse:
    """Render the private OAuth approval page."""
    state = request.query_params.get("state", "")
    if not state or state not in _oauth_provider.pending:
        return HTMLResponse("Invalid or expired authorization request.", status_code=400)

    safe_state = html.escape(state, quote=True)
    page = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Intervals.icu MCP Authorization</title></head>
<body style="font-family:system-ui,sans-serif;max-width:520px;margin:64px auto;padding:0 20px">
<h2>Authorize Intervals.icu MCP</h2>
<p>This grants read-only access to your private MCP server.</p>
<form method="post" action="/login">
<input type="hidden" name="state" value="{safe_state}">
<label for="password">Authorization password</label><br>
<input id="password" name="password" type="password" required autofocus
 style="width:100%;box-sizing:border-box;padding:12px;margin:8px 0 16px">
<button type="submit" style="padding:10px 18px">Authorize</button>
</form>
</body></html>"""
    return HTMLResponse(page)


@mcp.custom_route("/login", methods=["POST"])
async def oauth_login_submit(request: Request):
    """Validate the private password and finish the OAuth authorization redirect."""
    form = await request.form()
    state = str(form.get("state", ""))
    password = str(form.get("password", ""))
    redirect_url = _oauth_provider.complete_login(state, password)
    if redirect_url is None:
        return HTMLResponse("Invalid password or expired authorization request.", status_code=401)
    return RedirectResponse(redirect_url, status_code=302)
