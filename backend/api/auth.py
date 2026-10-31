import hmac

import httpx
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.api.errors import PlatformError

bearer = HTTPBearer(auto_error=False)


async def authenticate(request: Request) -> str:
    credentials: HTTPAuthorizationCredentials | None = await bearer(request)
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
    if response.status_code != 200 or not response.json().get("id"):
        raise PlatformError(401, "UNAUTHENTICATED", "Invalid or expired Supabase access token")
    return response.json()["id"]
