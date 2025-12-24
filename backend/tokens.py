import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, Optional

try:
    from . import config  # type: ignore
except ImportError:
    import config  # type: ignore

from fastapi import HTTPException, status


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class RateLimiter:
    """Simple in-memory sliding-window rate limiter keyed by client identifier."""

    def __init__(self, max_calls: int, window_seconds: int):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.lock = threading.Lock()
        self.buckets: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        with self.lock:
            dq = self.buckets.get(key)
            if dq is None:
                dq = deque()
                self.buckets[key] = dq

            while dq and dq[0] < cutoff:
                dq.popleft()

            if len(dq) >= self.max_calls:
                return False

            dq.append(now)
            return True


class TokenRegistry:
    """Tracks active tokens for revocation and replay prevention."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active: dict[str, dict[str, Any]] = {}

    def add(self, jti: str, payload: Dict[str, Any]):
        with self.lock:
            self.active[jti] = payload

    def is_active(self, jti: str) -> bool:
        with self.lock:
            payload = self.active.get(jti)
            if not payload:
                return False
            if payload.get("exp", 0) < int(time.time()):
                self.active.pop(jti, None)
                return False
            return True

    def revoke_session(self, session_id: str):
        with self.lock:
            to_delete = [jti for jti, payload in self.active.items() if payload.get("session_id") == session_id]
            for jti in to_delete:
                self.active.pop(jti, None)

    def revoke_jti(self, jti: str):
        with self.lock:
            self.active.pop(jti, None)


token_registry = TokenRegistry()
issue_limiter = RateLimiter(config.TOKEN_ISSUE_LIMIT, config.TOKEN_ISSUE_WINDOW_SECONDS)
verify_limiter = RateLimiter(config.TOKEN_VERIFY_LIMIT, config.TOKEN_VERIFY_WINDOW_SECONDS)


def _sign(payload: Dict[str, Any]) -> str:
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url(payload_bytes)
    sig = hmac.new(config.SIGNING_KEY.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return payload_b64 + "." + _b64url(sig)


def _verify(token: str) -> Dict[str, Any]:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format")

    expected_sig = hmac.new(config.SIGNING_KEY.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    provided_sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature")

    payload_json = _b64url_decode(payload_b64)
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    return payload


def issue_token(scope: str, session_id: Optional[str], client_id: str) -> Dict[str, Any]:
    if not issue_limiter.allow(client_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded for token issue")

    now = int(time.time())
    exp = now + config.TOKEN_TTL_SECONDS
    jti = uuid.uuid4().hex
    session = session_id or uuid.uuid4().hex
    payload = {
        "jti": jti,
        "session_id": session,
        "scope": scope,
        "aud": "smallpie",
        "iat": now,
        "exp": exp,
    }

    token = _sign(payload)
    token_registry.add(jti, payload)
    return {"token": token, "expires_at": exp, "session_id": session}


def validate_token(token: str, expected_scope: str, client_id: str) -> Dict[str, Any]:
    if not verify_limiter.allow(client_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    payload = _verify(token)
    now = int(time.time())

    if payload.get("aud") != "smallpie":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid audience")

    if payload.get("scope") != expected_scope:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid scope")

    if payload.get("exp", 0) < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    jti = payload.get("jti")
    if not jti or not token_registry.is_active(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inactive")

    return payload


def revoke_session(session_id: str):
    token_registry.revoke_session(session_id)


def revoke_token_by_jti(jti: str):
    token_registry.revoke_jti(jti)
