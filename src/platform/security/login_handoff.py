from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, Optional, Tuple


DEFAULT_HANDOFF_TTL_SECONDS = 120


class LoginHandoffStore:
    def __init__(self, ttl_seconds: int = DEFAULT_HANDOFF_TTL_SECONDS):
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._codes: Dict[str, Tuple[str, float]] = {}

    def issue(self, access_token: str) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            self._codes[code] = (access_token, time.monotonic() + self._ttl_seconds)
        return code

    def redeem(self, code: Optional[str]) -> Optional[str]:
        if not code:
            return None
        with self._lock:
            self._prune()
            entry = self._codes.pop(code, None)
        if entry is None:
            return None
        access_token, expires_at = entry
        return access_token if time.monotonic() <= expires_at else None

    def clear(self) -> None:
        with self._lock:
            self._codes.clear()

    def _prune(self) -> None:
        now = time.monotonic()
        for code in [code for code, (_, expires_at) in self._codes.items() if expires_at < now]:
            del self._codes[code]
