"""Single shared password login.

The password lives only in the server environment (APP_PASSWORD in backend/.env locally, a project
environment variable on Vercel). A successful login sets an HttpOnly, SameSite=Strict session
cookie holding an expiry and an HMAC signature; nothing about the session is stored server-side.
Failed attempts are throttled per client IP and globally, using the database so the limit holds
across serverless instances.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time

from .config import Settings
from .store import JobStore

COOKIE_NAME = "subtrans_session"
SESSION_TTL = 7 * 24 * 3600
FAIL_WINDOW = 15 * 60
MAX_FAILS_PER_CLIENT = 8
MAX_FAILS_GLOBAL = 60
FAIL_DELAY = 0.6  # every failed attempt costs the caller this long


def _secret(settings: Settings) -> bytes:
    if settings.subtrans_session_secret:
        return settings.subtrans_session_secret.encode()
    # Derived from the password so that changing APP_PASSWORD invalidates every existing session.
    return hashlib.sha256(b"subtrans-session-v1\x00" + settings.app_password.encode()).digest()


def _sign(settings: Settings, payload: str) -> str:
    return hmac.new(_secret(settings), payload.encode(), hashlib.sha256).hexdigest()


def make_session(settings: Settings, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + SESSION_TTL)
    payload = f"{expires}.{secrets.token_urlsafe(12)}"
    return f"{payload}.{_sign(settings, payload)}"


def session_valid(settings: Settings, token: str | None, now: float | None = None) -> bool:
    if not token or not settings.app_password:
        return False
    try:
        expires, nonce, signature = token.split(".", 2)
        expires_at = int(expires)
    except ValueError:
        return False
    if not hmac.compare_digest(signature, _sign(settings, f"{expires}.{nonce}")):
        return False
    return expires_at > (time.time() if now is None else now)


def password_matches(settings: Settings, given: str) -> bool:
    if not settings.app_password:
        return False
    # Compare fixed-length digests so neither length nor content leaks through timing.
    a = hashlib.sha256(given.encode()).digest()
    b = hashlib.sha256(settings.app_password.encode()).digest()
    return hmac.compare_digest(a, b)


class LoginThrottled(Exception):
    pass


async def check_login(store: JobStore, settings: Settings, client_key: str, password: str) -> bool:
    """True on success. Raises LoginThrottled when too many recent failures."""
    now = time.time()
    keys = (f"ip:{client_key}", "global")
    if (
        await store.login_failures(keys[0], FAIL_WINDOW, now) >= MAX_FAILS_PER_CLIENT
        or await store.login_failures(keys[1], FAIL_WINDOW, now) >= MAX_FAILS_GLOBAL
    ):
        raise LoginThrottled()
    if password_matches(settings, password):
        await store.clear_login_failures(keys[0])
        return True
    for key in keys:
        await store.record_login_failure(key, FAIL_WINDOW, now)
    await asyncio.sleep(FAIL_DELAY)
    return False
