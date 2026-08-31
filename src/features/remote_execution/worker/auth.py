"""Static bearer-token auth for the worker's own routes.

No Auth, no user model, no PotionUI database: a worker's only
authentication is possession of the shared secret its operator set in
``POTIONUI_WORKER_TOKEN``. Every route depends on the dependency this builds.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

_BEARER_PREFIX = "Bearer "


def build_token_dependency(expected_token: str):
    def require_worker_token(authorization: str = Header(default="")) -> None:
        if not authorization.startswith(_BEARER_PREFIX):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing worker token")
        if not secrets.compare_digest(authorization[len(_BEARER_PREFIX):], expected_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid worker token")

    return require_worker_token
