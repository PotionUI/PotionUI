from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.platform.database.rows import dt_iso, json_column, row_get

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL = frozenset({DONE, FAILED, CANCELLED})
ID_PREFIX = "er_"


@dataclass
class EditorRun:
    id: str
    user_id: str
    task: str
    status: str = QUEUED
    edit_id: Optional[str] = None
    step_id: Optional[str] = None
    generation_id: Optional[str] = None
    rect: Optional[Dict[str, float]] = None
    crop: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    @classmethod
    def from_row(cls, row) -> "EditorRun":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            task=row["task"],
            status=row["status"],
            edit_id=row_get(row, "edit_id"),
            step_id=row_get(row, "step_id"),
            generation_id=row_get(row, "generation_id"),
            rect=json_column(row_get(row, "rect")),
            crop=json_column(row_get(row, "crop")),
            candidates=json_column(row_get(row, "candidates"), []),
            error=row_get(row, "error"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self, progress: Optional[float] = None) -> Dict[str, Any]:
        crop = self.crop or {}
        return {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "edit_id": self.edit_id,
            "step_id": self.step_id,
            "generation_id": self.generation_id,
            "progress": progress if progress is not None else (1.0 if self.status == DONE else 0.0),
            "error": self.error,
            "rect": self.rect,
            "soft": bool(crop.get("soft", False)),
            "candidates": list(self.candidates or []),
            "created_at": dt_iso(self.created_at),
            "updated_at": dt_iso(self.updated_at),
        }
