"""In-memory tracking of one model transfer's byte-level progress.

A multi-gigabyte upload or fetch takes long enough that "started"/"done" is
not enough for an admin surface watching it - this is what lets ``GET
/v1/models/transfers/{id}`` show movement mid-transfer. Not persisted: a
worker restart loses transfer history, same as any other in-process state the
journal doesn't own.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

TransferKind = Literal["upload", "fetch"]
TransferState = Literal["running", "completed", "failed"]


@dataclass
class Transfer:
    id: str
    kind: TransferKind
    relative_path: str
    total_bytes: int
    received_bytes: int = 0
    state: TransferState = "running"
    error: Optional[str] = None


class TransferRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._transfers: Dict[str, Transfer] = {}

    def start(self, kind: TransferKind, relative_path: str, total_bytes: int) -> Transfer:
        transfer = Transfer(
            id=uuid.uuid4().hex, kind=kind, relative_path=relative_path, total_bytes=total_bytes,
        )
        with self._lock:
            self._transfers[transfer.id] = transfer
        return transfer

    def progress(self, transfer_id: str, received_bytes: int) -> None:
        with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is not None:
                transfer.received_bytes = received_bytes

    def complete(self, transfer_id: str) -> None:
        with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is not None:
                transfer.state = "completed"

    def fail(self, transfer_id: str, error: str) -> None:
        with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is not None:
                transfer.state = "failed"
                transfer.error = error

    def get(self, transfer_id: str) -> Optional[Transfer]:
        with self._lock:
            return self._transfers.get(transfer_id)

    def list(self) -> List[Transfer]:
        with self._lock:
            return list(self._transfers.values())
