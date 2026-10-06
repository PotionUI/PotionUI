import hashlib
import time
from pathlib import Path
from typing import Optional

from fastapi import Request, Response
from jose import JWTError, jwt

MEDIA_COOKIE = "potionui_media"
MEDIA_COOKIE_PATH = "/api"


def media_cookie_name() -> str:
    from src.platform.security.current_user import get_auth

    try:
        secret = get_auth().tokens._config.secret_key
    except Exception:
        return MEDIA_COOKIE
    if not isinstance(secret, str) or not secret:
        return MEDIA_COOKIE
    identity = f"{secret}\0{_instance_location()}"
    return f"{MEDIA_COOKIE}_{hashlib.sha256(identity.encode()).hexdigest()[:12]}"


def _instance_location() -> str:
    from src.platform.database.database import db

    try:
        return str(Path(db.db_path).resolve())
    except Exception:
        return ""


def _is_secure(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def token_lifetime_seconds(token: str, now: Optional[float] = None) -> int:
    try:
        expires = jwt.get_unverified_claims(token).get("exp")
    except JWTError:
        return 0
    if not isinstance(expires, (int, float)):
        return 0
    return max(0, int(expires - (time.time() if now is None else now)))


def set_media_cookie(response: Response, request: Request, token: Optional[str]) -> None:
    lifetime = token_lifetime_seconds(token) if token else 0
    if lifetime <= 0:
        clear_media_cookie(response, request)
        return
    response.set_cookie(
        media_cookie_name(),
        token,
        max_age=lifetime,
        path=MEDIA_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
    )


def clear_media_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        media_cookie_name(),
        path=MEDIA_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
    )


def media_cookie_token(request: Request) -> Optional[str]:
    return request.cookies.get(media_cookie_name()) or None
