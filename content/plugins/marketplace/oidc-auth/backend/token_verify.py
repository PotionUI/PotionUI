from __future__ import annotations

import hmac
from typing import Any, Dict, Optional

from jose import JWTError, jwt

from .discovery import get_jwk

ALLOWED_ALGORITHMS = frozenset(
    {
        "RS256", "RS384", "RS512",
        "ES256", "ES384", "ES512",
        "PS256", "PS384", "PS512",
    }
)


class TokenVerificationError(Exception):
    pass


async def verify_id_token(
    id_token: str,
    jwks_uri: str,
    issuer: str,
    client_id: str,
    nonce: str,
    access_token: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError as exc:
        raise TokenVerificationError("malformed id_token") from exc

    algorithm = header.get("alg")
    if algorithm not in ALLOWED_ALGORITHMS:
        raise TokenVerificationError(f"disallowed id_token algorithm: {algorithm!r}")

    kid = header.get("kid")
    if not kid:
        raise TokenVerificationError("id_token header is missing kid")

    key = await get_jwk(jwks_uri, kid, algorithm)
    if key is None:
        raise TokenVerificationError(f"no matching key for kid {kid!r}")

    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[algorithm],
            access_token=access_token,
            options={"verify_aud": False, "verify_iss": False},
        )
    except JWTError as exc:
        raise TokenVerificationError("id_token signature or claims are invalid") from exc

    if str(claims.get("iss", "")).rstrip("/") != issuer:
        raise TokenVerificationError("id_token issuer mismatch")

    audiences = claims.get("aud")
    audiences = audiences if isinstance(audiences, list) else [audiences]
    if client_id not in audiences:
        raise TokenVerificationError("id_token audience mismatch")
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise TokenVerificationError("id_token azp does not match client_id for a multi-audience token")

    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
        raise TokenVerificationError("id_token nonce mismatch")

    return claims
