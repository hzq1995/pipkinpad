"""Password hashing and persistent signed login cookies."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time

from .config import AISettings

COOKIE_NAME = "pipkinpad_auth"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60
PBKDF2_ITERATIONS = 600_000


def configure_password(settings: AISettings, password: str) -> None:
    if not password:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    settings.password_salt = base64.urlsafe_b64encode(salt).decode()
    settings.password_hash = base64.urlsafe_b64encode(digest).decode()
    # Rotating this secret invalidates every existing login.
    settings.auth_secret = secrets.token_urlsafe(32)


def clear_password(settings: AISettings) -> None:
    settings.password_salt = settings.password_hash = settings.auth_secret = ""


def password_configured(settings: AISettings) -> bool:
    return bool(settings.password_salt and settings.password_hash and settings.auth_secret)


def verify_password(settings: AISettings, password: str) -> bool:
    if not password_configured(settings):
        return True
    try:
        salt = base64.urlsafe_b64decode(settings.password_salt)
        expected = base64.urlsafe_b64decode(settings.password_hash)
    except (ValueError, binascii.Error):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(actual, expected)


def issue_cookie(settings: AISettings, now: int | None = None) -> str:
    expires = (int(time.time()) if now is None else now) + COOKIE_MAX_AGE
    payload = str(expires)
    signature = hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_cookie(settings: AISettings, value: str | None, now: int | None = None) -> bool:
    if not password_configured(settings):
        return True
    try:
        expires_text, signature = (value or "").split(".", 1)
        expires = int(expires_text)
    except (ValueError, AttributeError):
        return False
    expected = hmac.new(settings.auth_secret.encode(), expires_text.encode(), hashlib.sha256).hexdigest()
    current = int(time.time()) if now is None else now
    return expires >= current and hmac.compare_digest(signature, expected)
