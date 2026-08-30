"""Small co-hosted OAuth 2.1 provider for the private MCP deployment.

This provider supports dynamic client registration, authorization-code + PKCE,
refresh tokens, and bearer-token validation. Human approval is protected by a
single deployment secret in MCP_OAUTH_PASSWORD.

Access and refresh tokens are stateless HMAC-signed tokens so they remain valid
across Railway deploys and process restarts. Short-lived authorization flow state
remains in memory, which is sufficient for the interactive login exchange.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
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


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class PrivateOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Private OAuth provider with stateless signed bearer/refresh tokens."""

    def __init__(self, issuer_url: str, resource_url: str) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = resource_url
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending: dict[str, PendingAuthorization] = {}
        self.codes: dict[str, AuthorizationCode] = {}

        signing_secret = os.getenv("MCP_OAUTH_TOKEN_SECRET", "").strip()
        if not signing_secret:
            raise RuntimeError("MCP_OAUTH_TOKEN_SECRET must be configured when OAuth is enabled")
        self._signing_secret = signing_secret.encode("utf-8")

    def _encode_token(self, token_type: str, client_id: str, scopes: list[str], expires_at: int) -> str:
        payload = {
            "v": 1,
            "typ": token_type,
            "cid": client_id,
            "scp": scopes,
            "exp": expires_at,
            "res": self.resource_url,
            "sub": "private-user",
            "jti": secrets.token_urlsafe(16),
        }
        body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = hmac.new(self._signing_secret, body.encode("ascii"), hashlib.sha256).digest()
        return f"{body}.{_b64url_encode(signature)}"

    def _decode_token(self, token: str, expected_type: str) -> dict | None:
        try:
            body, supplied_signature = token.split(".", 1)
            expected_signature = hmac.new(
                self._signing_secret,
                body.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(_b64url_decode(supplied_signature), expected_signature):
                return None
            payload = json.loads(_b64url_decode(body).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

        if payload.get("v") != 1 or payload.get("typ") != expected_type:
            return None
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        if payload.get("res") != self.resource_url:
            return None
        if not isinstance(payload.get("cid"), str) or not isinstance(payload.get("scp"), list):
            return None
        return payload

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        known = self.clients.get(client_id)
        if known is not None:
            return known

        # After a process restart the dynamic-registration cache is empty. Token
        # refresh still needs a client object. Treat previously registered clients
        # as public clients; possession of the signed refresh token is the credential.
        return OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=["https://chatgpt.com/connector_platform_oauth_redirect"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="mcp:read",
        )

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
        access_exp = now + 3600
        refresh_exp = now + 30 * 24 * 3600
        access_value = self._encode_token(
            "access", authorization_code.client_id, authorization_code.scopes, access_exp
        )
        refresh_value = self._encode_token(
            "refresh", authorization_code.client_id, authorization_code.scopes, refresh_exp
        )
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
        payload = self._decode_token(refresh_token, "refresh")
        if payload is None or client.client_id != payload["cid"]:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=payload["cid"],
            scopes=list(payload["scp"]),
            expires_at=int(payload["exp"]),
            subject=payload.get("sub"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        requested_scopes = scopes or refresh_token.scopes
        now = int(time.time())
        access_exp = now + 3600
        refresh_exp = now + 30 * 24 * 3600
        access_value = self._encode_token("access", refresh_token.client_id, requested_scopes, access_exp)
        refresh_value = self._encode_token("refresh", refresh_token.client_id, requested_scopes, refresh_exp)
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(requested_scopes),
            refresh_token=refresh_value,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        payload = self._decode_token(token, "access")
        if payload is None:
            return None
        return AccessToken(
            token=token,
            client_id=payload["cid"],
            scopes=list(payload["scp"]),
            expires_at=int(payload["exp"]),
            resource=payload.get("res"),
            subject=payload.get("sub"),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Stateless tokens cannot be individually revoked without server-side state.
        # Rotating MCP_OAUTH_TOKEN_SECRET revokes all currently issued tokens.
        return None
