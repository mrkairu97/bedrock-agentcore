import os
import time
import threading
import logging
from collections.abc import Generator
from typing import Optional

import httpx
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

logger = logging.getLogger(__name__)

# Refresh the token this many seconds before it actually expires so a request
# never goes out with a token that expires mid-flight.
TOKEN_REFRESH_MARGIN_SECONDS: int = 60


class CognitoClientCredentialsAuth(httpx.Auth):
    """httpx auth that adds a Cognito OAuth2 (client_credentials) bearer token to every request.

    The AgentCore Gateway uses a Custom JWT authorizer backed by this Cognito pool, so each
    MCP call must carry an access token. The token is cached and re-fetched shortly before
    it expires. Because this runs per request (instead of baking a token into a header once),
    a long-lived MCP connection keeps working after the first token expires.
    """

    def __init__(self, token_endpoint: str, client_id: str, client_secret: str, scope: str) -> None:
        self._token_endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def _get_token(self) -> str:
        with self._lock:
            if self._access_token and time.time() < self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
                return self._access_token
            # client_credentials grant: the client authenticates with HTTP Basic auth.
            response = httpx.post(
                self._token_endpoint,
                data={"grant_type": "client_credentials", "scope": self._scope},
                auth=(self._client_id, self._client_secret),
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            self._access_token = body["access_token"]
            self._expires_at = time.time() + int(body.get("expires_in", 3600))
            logger.info("Fetched new gateway access token (expires in %ss)", body.get("expires_in"))
            return self._access_token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        yield request


def get_streamable_http_mcp_client() -> Optional[MCPClient]:
    """Returns a Strands MCP client for the AgentCore Gateway, or None if it isn't configured."""
    url = os.environ.get("GATEWAY_URL")
    client_id = os.environ.get("GATEWAY_CLIENT_ID")
    client_secret = os.environ.get("GATEWAY_CLIENT_SECRET")
    token_endpoint = os.environ.get("GATEWAY_TOKEN_ENDPOINT")
    scope = os.environ.get("GATEWAY_SCOPE")

    if not all([url, client_id, client_secret, token_endpoint, scope]):
        logger.warning("Gateway env vars are not fully set; running without gateway tools.")
        return None

    auth = CognitoClientCredentialsAuth(token_endpoint, client_id, client_secret, scope)
    # Strands calls this factory to open the Streamable HTTP transport to the gateway's /mcp endpoint.
    return MCPClient(lambda: streamablehttp_client(url, auth=auth))
