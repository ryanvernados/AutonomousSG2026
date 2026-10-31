import hmac

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.api.errors import PlatformError

bearer = HTTPBearer(auto_error=False, scheme_name="SupabaseBearer")


async def authenticate(request: Request,
                       credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if not credentials:
        raise PlatformError(401, "UNAUTHENTICATED", "Bearer access token required")
    config = request.app.state.settings
    if config.auth_mode == "demo":
        if not config.demo_token or not hmac.compare_digest(credentials.credentials, config.demo_token):
            raise PlatformError(401, "UNAUTHENTICATED", "Invalid demo token")
        return "00000000-0000-0000-0000-000000000001"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(config.supabase_url.rstrip("/") + "/auth/v1/user",
                                        headers={"apikey": config.supabase_publishable_key,
                                                 "Authorization": "Bearer " + credentials.credentials})
    except httpx.HTTPError as exc:
        raise PlatformError(503, "AUTH_UNAVAILABLE", "Supabase Auth unavailable") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise PlatformError(503, "AUTH_UNAVAILABLE", "Supabase Auth unavailable")
    if response.status_code != 200:
        raise PlatformError(401, "UNAUTHENTICATED", "Invalid or expired Supabase access token")
    try:
        identity = response.json()["id"]
        from uuid import UUID
        UUID(identity)
    except (ValueError, KeyError, TypeError) as exc:
        raise PlatformError(503, "AUTH_UNAVAILABLE", "Invalid response from Supabase Auth") from exc
    return identity
