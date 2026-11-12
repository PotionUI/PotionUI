import asyncio
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterator, Optional

from fastapi import WebSocket

from src.platform.database.rows import dt_column, now_utc
from src.platform.security import key_revocation
from src.platform.security.api_scopes import ApiScope, expand_scopes, required_scopes
from src.platform.security.principal import KeyGrant, KeyResolver
from src.platform.security.user import AccountType

logger = logging.getLogger(__name__)

KEY_CLOSE_CODE = 4001
REVOKED_REASON = "API key revoked"
EXPIRED_REASON = "API key expired"
NARROWED_REASON = "API key no longer permits this socket"
HEARTBEAT_SECONDS = 15


@dataclass
class _Tracked:
    websocket: WebSocket
    grant: KeyGrant
    loop: asyncio.AbstractEventLoop
    required: Optional[FrozenSet[ApiScope]]
    was_admin: bool


class KeySocketRegistry:
    def __init__(self, heartbeat_seconds: float = HEARTBEAT_SECONDS):
        self._heartbeat_seconds = heartbeat_seconds
        self._tracked: Dict[int, _Tracked] = {}
        self._lock = threading.Lock()
        self._sweeper: Optional[asyncio.Task] = None
        self._resolver: Optional[KeyResolver] = None

    def set_resolver(self, resolver: Optional[KeyResolver]) -> None:
        self._resolver = resolver

    @contextmanager
    def track(self, websocket: WebSocket, user) -> Iterator[None]:
        grant = getattr(user, "key", None)
        if grant is None:
            yield
            return
        required = required_scopes(getattr(websocket, "scope", {}).get("route"), "GET")
        self._register(websocket, grant, required, user.account_type == AccountType.ADMIN)
        try:
            yield
        finally:
            self._unregister(websocket)

    def _register(
        self,
        websocket: WebSocket,
        grant: KeyGrant,
        required: Optional[FrozenSet[ApiScope]] = None,
        was_admin: bool = False,
    ) -> None:
        loop = asyncio.get_running_loop()
        item = _Tracked(websocket, grant, loop, required, was_admin)
        with self._lock:
            self._tracked[id(websocket)] = item
        if self._sweeper is None or self._sweeper.done() or self._sweeper.get_loop() is not loop:
            self._sweeper = loop.create_task(self._sweep_forever())
        if self._resolver is not None:
            loop.create_task(asyncio.to_thread(self._check, [item]))

    def _unregister(self, websocket: WebSocket) -> None:
        with self._lock:
            self._tracked.pop(id(websocket), None)

    def count_for_key(self, key_id: str) -> int:
        with self._lock:
            return sum(1 for item in self._tracked.values() if item.grant.key_id == key_id)

    def close_for_key(self, key_id: str) -> None:
        with self._lock:
            targets = [item for item in self._tracked.values() if item.grant.key_id == key_id]
        for item in targets:
            self._dispatch(item, REVOKED_REASON)

    def check_key(self, key_id: str) -> int:
        with self._lock:
            targets = [item for item in self._tracked.values() if item.grant.key_id == key_id]
        return self._check(targets)

    def sweep(self, now=None) -> int:
        with self._lock:
            targets = list(self._tracked.values())
        return self._check(targets, now or now_utc())

    def _check(self, items, moment=None) -> int:
        moment = moment or now_utc()
        live: Dict[str, object] = {}
        closed = 0
        for item in items:
            key_id = item.grant.key_id
            if key_id not in live:
                live[key_id] = self._resolver.resolve_id(key_id) if self._resolver is not None else item
            reason = self._verdict(item, live[key_id], moment)
            if reason:
                self._dispatch(item, reason)
                closed += 1
        return closed

    def _verdict(self, item: _Tracked, resolved, moment) -> Optional[str]:
        if self._is_expired(item.grant, moment):
            return EXPIRED_REASON
        if self._resolver is None:
            return None
        if resolved is None:
            return REVOKED_REASON
        if self._is_expired(resolved.grant, moment):
            return EXPIRED_REASON
        if item.required is not None and not (expand_scopes(resolved.grant.scopes) & item.required):
            return NARROWED_REASON
        if item.was_admin and resolved.user.account_type != AccountType.ADMIN:
            return NARROWED_REASON
        return None

    @staticmethod
    def _is_expired(grant: KeyGrant, moment) -> bool:
        expires = dt_column(grant.expires_at)
        return expires is not None and expires <= moment

    def _dispatch(self, item: _Tracked, reason: str) -> None:
        with self._lock:
            if self._tracked.pop(id(item.websocket), None) is None:
                return
        try:
            asyncio.run_coroutine_threadsafe(self._close(item.websocket, reason), item.loop)
        except RuntimeError:
            logger.debug("Event loop closed before API key socket %s could be closed", item.grant.key_id)

    @staticmethod
    async def _close(websocket: WebSocket, reason: str) -> None:
        try:
            await websocket.close(code=KEY_CLOSE_CODE, reason=reason)
        except Exception as exc:
            logger.debug("Closing API key socket failed: %s", exc)

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                await asyncio.to_thread(self.sweep)
            except Exception:
                logger.exception("API key socket sweep failed")


key_sockets = KeySocketRegistry()
key_revocation.subscribe(key_sockets.close_for_key)
key_revocation.subscribe_changes(key_sockets.check_key)
