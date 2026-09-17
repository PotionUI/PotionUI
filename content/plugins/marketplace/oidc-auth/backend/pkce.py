from __future__ import annotations

import base64
import hashlib
import secrets


def generate_code_verifier() -> str:
    return secrets.token_urlsafe(48)


def code_challenge_s256(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
