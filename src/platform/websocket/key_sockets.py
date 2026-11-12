import asyncio
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, Optional

from fastapi import WebSocket

from src.platform.database.rows import dt_column, now_utc
from src.platform.security import key_revocation
from src.platform.security.principal import KeyGrant

logger = logging.getLogger(__name__)

KEY_CLOSE_CODE = 4001
REVOKED_REASON = "API key revoked"
EXPIRED_REASON = "API key expired"
HEARTBEAT_SECONDS = 15


@dataclass
class _Tracked:
    websocket: WebSocket
    grant: KeyGrant
    loop: asyncio.AbstractEventLoop


class KeySocketRegistry:
    def __init__(self, heartbeat_seconds: float = HEARTBEAT_SECONDS):
        self._heartbeat_seconds = heartbeat_seconds
        self._tracked: Dict[int, _Tracked] = {}
        self._lock = threading.Lock()
        self._sweeper: Optional[asyncio.Task] = None

    def register(self, websocket: WebSocket, grant: KeyGrant) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._tracked[id(websocket)] = _Tracked(websocket, grant, loop)
        if self._sweeper is None or self._sweeper.done() or self._sweeper.get_loop() is not loop:
            self._sweeper = loop.create_task(self._sweep_forever())

    def attach(self, websocket: WebSocket, grant: Optional[KeyGrant]) -> None:
        if grant is not None:
            self.register(websocket, grant)

    def unregister(self, websocket: WebSocket) -> None:
        with self._lock:
            self._tracked.pop(id(websocket), None)

    @contextmanager
    def track(self, websocket: WebSocket, grant: Optional[KeyGrant]) -> Iterator[None]:
        self.attach(websocket, grant)
        try:
            yield
        finally:
            self.unregister(websocket)

    def count_for_key(self, key_id: str) -> int:
        with self._lock:
            return sum(1 for item in self._tracked.values() if item.grant.key_id == key_id)

    def close_for_key(self, key_id: str) -> None:
        with self._lock:
            targets = [item for item in self._tracked.values() if item.grant.key_id == key_id]
        for item in targets:
            self._dispatch(item, REVOKED_REASON)

    def sweep(self, now=None) -> int:
        moment = now or now_utc()
        with self._lock:
            expired = [item for item in self._tracked.values() if self._is_expired(item.grant, moment)]
        for item in expired:
            self._dispatch(item, EXPIRED_REASON)
        return len(expired)

    @staticmethod
    def _is_expired(grant: KeyGrant, moment) -> bool:
        expires = dt_column(grant.expires_at)
        return expires is not None and expires <= moment

    def _dispatch(self, item: _Tracked, reason: str) -> None:
        with self._lock:
            self._tracked.pop(id(item.websocket), None)
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
                self.sweep()
            except Exception:
                logger.exception("API key socket sweep failed")


key_sockets = KeySocketRegistry()
key_revocation.subscribe(key_sockets.close_for_key)
