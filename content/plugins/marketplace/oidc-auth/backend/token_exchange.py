from __future__ import annotations

from typing import Any, Dict, Optional

from .http_client import new_client
from .settings import OIDCSettings

USERINFO_FILL_CLAIMS = ("email", "preferred_username", "name")


class TokenExchangeError(Exception):
    pass


class UserinfoMismatchError(Exception):
    pass


def _token_endpoint_auth_method(discovery: Dict[str, Any]) -> str:
    supported = discovery.get("token_endpoint_auth_methods_supported")
    if supported and "client_secret_post" not in supported and "client_secret_basic" in supported:
        return "client_secret_basic"
    return "client_secret_post"


async def exchange_code(
    discovery: Dict[str, Any],
    settings: OIDCSettings,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> Dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": settings.client_id,
    }
    auth: Optional[tuple] = None
    if _token_endpoint_auth_method(discovery) == "client_secret_basic":
        auth = (settings.client_id, settings.client_secret)
    else:
        data["client_secret"] = settings.client_secret

    async with new_client() as client:
        response = await client.post(discovery["token_endpoint"], data=data, auth=auth)
    if response.status_code >= 400:
        raise TokenExchangeError(f"token endpoint returned {response.status_code}: {response.text}")
    return response.json()


async def enrich_from_userinfo(
    discovery: Dict[str, Any],
    access_token: Optional[str],
    claims: Dict[str, Any],
) -> Dict[str, Any]:
    userinfo_endpoint = discovery.get("userinfo_endpoint")
    if not userinfo_endpoint or not access_token:
        return claims
    missing = [name for name in USERINFO_FILL_CLAIMS if name not in claims]
    if not missing:
        return claims

    async with new_client() as client:
        response = await client.get(
            userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"}
        )
    if response.status_code >= 400:
        return claims
    userinfo = response.json()

    if userinfo.get("sub") != claims.get("sub"):
        raise UserinfoMismatchError("userinfo sub does not match id_token sub")

    merged = dict(claims)
    if "email" in missing and "email" in userinfo:
        merged["email"] = userinfo["email"]
        merged["email_verified"] = bool(userinfo.get("email_verified", False))
    if claims.get("email_verified") is False:
        merged["email_verified"] = False
    for name in ("preferred_username", "name"):
        if name in missing and name in userinfo:
            merged[name] = userinfo[name]
    return merged
