from __future__ import annotations

import hmac
import logging
import secrets
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from src.plugin_api import (
    ExternalLoginError,
    register_login_provider,
    sign_in_external,
    unregister_login_provider,
)

from .discovery import DiscoveryError, fetch_discovery
from .flow_cookie import (
    FLOW_COOKIE_NAME,
    FLOW_COOKIE_TTL_SECONDS,
    FlowCookieError,
    build_flow_cookie,
    verify_flow_cookie,
)
from .pkce import code_challenge_s256, generate_code_verifier
from .settings import PLUGIN_ID, load_settings
from .token_exchange import TokenExchangeError, UserinfoMismatchError, enrich_from_userinfo, exchange_code
from .token_verify import TokenVerificationError, verify_id_token

ROUTER_PREFIX = f"/api/plugins/{PLUGIN_ID}"
START_PATH = f"{ROUTER_PREFIX}/start"
CALLBACK_PATH = f"{ROUTER_PREFIX}/callback"
LOGIN_ERROR_URL = "/login?error=external_login"

logger = logging.getLogger(__name__)

router = APIRouter(prefix=ROUTER_PREFIX)


def enable() -> None:
    register_login_provider(PLUGIN_ID, load_settings().label, START_PATH)


def disable() -> None:
    unregister_login_provider(PLUGIN_ID)


def _build_redirect_uri(request: Request, redirect_uri_override: Optional[str]) -> str:
    if redirect_uri_override:
        return redirect_uri_override
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", request.headers.get("host") or request.url.netloc)
    host = host.split(",")[0].strip()
    return f"{scheme}://{host}{CALLBACK_PATH}"


def _fail(reason: str) -> RedirectResponse:
    logger.warning("[OIDC-AUTH] sign-in rejected: %s", reason)
    response = RedirectResponse(url=LOGIN_ERROR_URL, status_code=302)
    response.delete_cookie(FLOW_COOKIE_NAME, path=ROUTER_PREFIX)
    return response


@router.get("/start")
async def start(request: Request):
    settings = load_settings()
    if not settings.is_configured():
        return _fail("issuer_url, client_id or client_secret is not configured")

    try:
        discovery = await fetch_discovery(settings.issuer_url)
    except DiscoveryError as exc:
        return _fail(f"discovery: {exc}")
    except Exception as exc:
        logger.exception("[OIDC-AUTH] discovery request failed")
        return _fail(f"discovery request failed: {exc}")

    authorization_endpoint = discovery.get("authorization_endpoint")
    if not authorization_endpoint:
        return _fail("discovery document has no authorization_endpoint")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    code_verifier = generate_code_verifier()
    redirect_uri = _build_redirect_uri(request, settings.redirect_uri_override)

    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge_s256(code_verifier),
        "code_challenge_method": "S256",
    }
    response = RedirectResponse(url=f"{authorization_endpoint}?{urlencode(params)}", status_code=302)
    response.set_cookie(
        key=FLOW_COOKIE_NAME,
        value=build_flow_cookie(settings.client_secret, state, nonce, code_verifier, redirect_uri),
        max_age=FLOW_COOKIE_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=redirect_uri.startswith("https://"),
        path=ROUTER_PREFIX,
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    settings = load_settings()
    if not settings.is_configured():
        return _fail("issuer_url, client_id or client_secret is not configured")

    if error:
        return _fail(f"identity provider returned error={error[:64]!r}")

    try:
        flow = verify_flow_cookie(settings.client_secret, request.cookies.get(FLOW_COOKIE_NAME))
    except FlowCookieError as exc:
        return _fail(str(exc))

    if not state or not hmac.compare_digest(state, flow.state):
        return _fail("state mismatch")
    if not code:
        return _fail("missing authorization code")

    try:
        discovery = await fetch_discovery(settings.issuer_url)
        token_response = await exchange_code(discovery, settings, code, flow.redirect_uri, flow.code_verifier)
        id_token = token_response.get("id_token")
        if not id_token:
            return _fail("token response has no id_token")

        access_token = token_response.get("access_token")
        claims = await verify_id_token(
            id_token,
            discovery["jwks_uri"],
            settings.issuer_url,
            settings.client_id,
            flow.nonce,
            access_token=access_token,
        )
        claims = await enrich_from_userinfo(discovery, access_token, claims)
    except (DiscoveryError, TokenExchangeError, TokenVerificationError, UserinfoMismatchError) as exc:
        return _fail(str(exc))
    except Exception as exc:
        logger.exception("[OIDC-AUTH] callback failed")
        return _fail(f"unexpected callback failure: {exc}")

    external_claims = {
        "email": claims.get("email"),
        "email_verified": claims.get("email_verified"),
        "preferred_username": claims.get("preferred_username"),
        "name": claims.get("name"),
    }
    try:
        session = sign_in_external(settings.issuer_url, claims["sub"], external_claims)
    except ExternalLoginError as exc:
        return _fail(f"sign-in refused: {exc}")

    response = RedirectResponse(url=f"/login/callback?code={session.handoff_code}", status_code=302)
    response.delete_cookie(FLOW_COOKIE_NAME, path=ROUTER_PREFIX)
    return response
