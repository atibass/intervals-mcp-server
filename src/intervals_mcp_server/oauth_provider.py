"""Small co-hosted OAuth 2.1 provider for the private MCP deployment.

This provider supports dynamic client registration, authorization-code + PKCE,
refresh tokens, and bearer-token validation. Human approval is protected by a
single deployment secret in MCP_OAUTH_PASSWORD.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


@dataclass
class PendingAuthorization:
    client: OAuthClientInformationFull
    params: AuthorizationParams
    expires_at: float


class PrivateOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """In-memory OAuth provider intended for a single-replica private MCP service."""

    def __init__(self, issuer_url: str, resource_url: str) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = resource_url
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending: dict[str, PendingAuthorization] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            return
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        login_state = secrets.token_urlsafe(32)
        self.pending[login_state] = PendingAuthorization(
            client=client,
            params=params,
            expires_at=time.time() + 600,
        )
        return f"{self.issuer_url}/login?{urlencode({'state': login_state})}"

    def complete_login(self, login_state: str, password: str) -> str | None:
        pending = self.pending.pop(login_state, None)
        if pending is None or pending.expires_at < time.time():
            return None

        expected = os.getenv("MCP_OAUTH_PASSWORD", "")
        if not expected or not hmac.compare_digest(password, expected):
            return None

        client_id = pending.client.client_id
        if not client_id:
            return None

        params = pending.params
        code_value = secrets.token_urlsafe(32)
        scopes = params.scopes or ["mcp:read"]
        code = AuthorizationCode(
            code=code_value,
            scopes=scopes,
            expires_at=time.time() + 300,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource or self.resource_url,
            subject="private-user",
        )
        self.codes[code_value] = code
        return construct_redirect_uri(str(params.redirect_uri), code=code_value, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self.codes.get(authorization_code)
        if code is None or code.expires_at < time.time():
            return None
        if client.client_id != code.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)
        now = int(time.time())
        access_value = secrets.token_urlsafe(40)
        refresh_value = secrets.token_urlsafe(40)
        access = AccessToken(
            token=access_value,
            client_id=authorization_code.client_id,
            scopes=authorization_code.scopes,
            expires_at=now + 3600,
            resource=authorization_code.resource or self.resource_url,
            subject=authorization_code.subject,
        )
        refresh = RefreshToken(
            token=refresh_value,
            client_id=authorization_code.client_id,
            scopes=authorization_code.scopes,
            expires_at=now + 30 * 24 * 3600,
            subject=authorization_code.subject,
        )
        self.access_tokens[access_value] = access
        self.refresh_tokens[refresh_value] = refresh
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_value,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token = self.refresh_tokens.get(refresh_token)
        if token is None:
            return None
        if token.expires_at is not None and token.expires_at < int(time.time()):
            self.refresh_tokens.pop(refresh_token, None)
            return None
        if client.client_id != token.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self.refresh_tokens.pop(refresh_token.token, None)
        requested_scopes = scopes or refresh_token.scopes
        now = int(time.time())
        access_value = secrets.token_urlsafe(40)
        refresh_value = secrets.token_urlsafe(40)
        access = AccessToken(
            token=access_value,
            client_id=refresh_token.client_id,
            scopes=requested_scopes,
            expires_at=now + 3600,
            resource=self.resource_url,
            subject=refresh_token.subject,
        )
        new_refresh = RefreshToken(
            token=refresh_value,
            client_id=refresh_token.client_id,
            scopes=requested_scopes,
            expires_at=now + 30 * 24 * 3600,
            subject=refresh_token.subject,
        )
        self.access_tokens[access_value] = access
        self.refresh_tokens[refresh_value] = new_refresh
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(requested_scopes),
            refresh_token=refresh_value,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = self.access_tokens.get(token)
        if access is None:
            return None
        if access.expires_at is not None and access.expires_at < int(time.time()):
            self.access_tokens.pop(token, None)
            return None
        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self.access_tokens.pop(token.token, None)
        else:
            self.refresh_tokens.pop(token.token, None)
