from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Optional

from jose import JWTError, jwt

FLOW_COOKIE_NAME = "oidc_auth_flow"
FLOW_COOKIE_TTL_SECONDS = 600
_COOKIE_KEY_CONTEXT = b"potionui-oidc-auth:flow-cookie:v1"


class FlowCookieError(Exception):
    pass


@dataclass(frozen=True)
class FlowState:
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str


def _signing_key(client_secret: str) -> bytes:
    return hmac.new(client_secret.encode("utf-8"), _COOKIE_KEY_CONTEXT, hashlib.sha256).digest()


def build_flow_cookie(
    client_secret: str,
    state: str,
    nonce: str,
    code_verifier: str,
    redirect_uri: str,
) -> str:
    now = int(time.time())
    claims = {
        "state": state,
        "nonce": nonce,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "iat": now,
        "exp": now + FLOW_COOKIE_TTL_SECONDS,
    }
    return jwt.encode(claims, _signing_key(client_secret), algorithm="HS256")


def verify_flow_cookie(client_secret: str, cookie_value: Optional[str]) -> FlowState:
    if not cookie_value:
        raise FlowCookieError("missing flow cookie")
    try:
        claims = jwt.decode(cookie_value, _signing_key(client_secret), algorithms=["HS256"])
    except JWTError as exc:
        raise FlowCookieError("invalid or expired flow cookie") from exc
    try:
        return FlowState(
            state=claims["state"],
            nonce=claims["nonce"],
            code_verifier=claims["code_verifier"],
            redirect_uri=claims["redirect_uri"],
        )
    except KeyError as exc:
        raise FlowCookieError("flow cookie missing required claim") from exc
