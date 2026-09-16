from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from src.plugin_api import (
    ExternalLoginError,
    register_login_provider,
    sign_in_external,
    unregister_login_provider,
)

PLUGIN_ID = "stub-login"
LABEL = "Stub Identity"
ISSUER = "https://stub-idp.example"
START_PATH = f"/api/plugins/{PLUGIN_ID}/start"
CALLBACK_PATH = f"/api/plugins/{PLUGIN_ID}/callback"

router = APIRouter(prefix=f"/api/plugins/{PLUGIN_ID}")


def enable() -> None:
    register_login_provider(PLUGIN_ID, LABEL, START_PATH)


def disable() -> None:
    unregister_login_provider(PLUGIN_ID)


@router.get("/start")
async def start(
    sub: str = Query(...),
    email: Optional[str] = Query(None),
    email_verified: bool = Query(False),
    preferred_username: Optional[str] = Query(None),
):
    query = f"sub={sub}&email_verified={str(email_verified).lower()}"
    if email:
        query += f"&email={email}"
    if preferred_username:
        query += f"&preferred_username={preferred_username}"
    return RedirectResponse(url=f"{CALLBACK_PATH}?{query}", status_code=302)


@router.get("/callback")
async def callback(
    sub: str = Query(...),
    email: Optional[str] = Query(None),
    email_verified: bool = Query(False),
    preferred_username: Optional[str] = Query(None),
):
    claims = {
        "email": email,
        "email_verified": email_verified,
        "preferred_username": preferred_username,
    }
    try:
        session = sign_in_external(ISSUER, sub, claims)
    except ExternalLoginError:
        return RedirectResponse(url="/login?error=external_login", status_code=302)
    return RedirectResponse(
        url=f"/login/callback?code={session.handoff_code}", status_code=302
    )
