import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, FrozenSet, Optional

from src.platform.security.api_scopes import ApiScope

TICKET_TTL_SECONDS = 30
MAX_TICKETS = 2048
MAX_TICKETS_PER_CALLER = 32


@dataclass(frozen=True)
class TicketEntry:
    path: str
    user_id: str
    key_id: Optional[str]
    scopes: FrozenSet[ApiScope]
    expires_at: float


def _digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


class TicketStoreFull(Exception):
    pass


def caller_of(user_id: str, key_id: Optional[str]) -> str:
    return f"key:{key_id}" if key_id else f"session:{user_id}"


class WsTicketStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._entries: "OrderedDict[str, TicketEntry]" = OrderedDict()
        self._lock = threading.Lock()

    def issue(
        self,
        path: str,
        user_id: str,
        key_id: Optional[str] = None,
        scopes: FrozenSet[ApiScope] = frozenset(),
    ) -> str:
        ticket = secrets.token_urlsafe(32)
        now = self._clock()
        entry = TicketEntry(path, user_id, key_id, frozenset(scopes), now + TICKET_TTL_SECONDS)
        with self._lock:
            self._prune(now)
            caller = caller_of(user_id, key_id)
            owned = [
                digest for digest, held in self._entries.items()
                if caller_of(held.user_id, held.key_id) == caller
            ]
            while len(owned) >= MAX_TICKETS_PER_CALLER:
                del self._entries[owned.pop(0)]
            if len(self._entries) >= MAX_TICKETS:
                raise TicketStoreFull()
            self._entries[_digest(ticket)] = entry
        return ticket

    def redeem(self, ticket: str, path: str) -> Optional[TicketEntry]:
        if not ticket or len(ticket) > 128:
            return None
        with self._lock:
            entry = self._entries.pop(_digest(ticket), None)
        if entry is None or entry.expires_at <= self._clock() or entry.path != path:
            return None
        return entry

    def size(self) -> int:
        with self._lock:
            self._prune(self._clock())
            return len(self._entries)

    def _prune(self, now: float) -> None:
        expired = [digest for digest, entry in self._entries.items() if entry.expires_at <= now]
        for digest in expired:
            del self._entries[digest]


ws_tickets = WsTicketStore()
