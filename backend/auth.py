from fastapi import HTTPException

from . import config


def verify_bearer_token(authorization: str | None):
    """
    Enforce Authorization: Bearer <token> for HTTP endpoints
    when SMALLPIE_ACCESS_TOKEN is set. Otherwise, it's a no-op.
    """
    if not config.AUTH_ENABLED:
        return

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")

    token = parts[1].strip()
    if token != config.ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid access token")


def verify_ws_token(token: str | None) -> bool:
    """
    Verify the ?token=... query parameter for WebSocket connections.
    Returns True if accepted, False if rejected.
    """
    if not config.AUTH_ENABLED:
        return True

    if not token:
        print("[auth] WebSocket missing token")
        return False

    if token != config.ACCESS_TOKEN:
        print("[auth] WebSocket invalid token")
        return False

    return True
