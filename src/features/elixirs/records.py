import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict

ELIXIRS_TABLE = "elixirs"
RUNS_TABLE = "elixir_runs"
INVOCATIONS_TABLE = "elixir_invocations"

RUN_STATUSES = (
    "pending",
    "running",
    "awaiting_selection",
    "needs_attention",
    "cancelling",
    "completed",
    "partial",
    "cancelled",
    "failed",
    "interrupted",
)
ACTIVE_RUN_STATUSES = ("pending", "running", "awaiting_selection", "needs_attention", "cancelling")
INVOCATION_STATUSES = (
    "pending",
    "claimed",
    "submitted",
    "running",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "superseded",
)
ACTIVE_INVOCATION_STATUSES = ("pending", "claimed", "submitted", "running")
TERMINAL_INVOCATION_STATUSES = ("completed", "failed", "cancelled", "interrupted", "superseded")


class RevisionConflict(Exception):
    def __init__(self, current_revision: int):
        super().__init__(f"The elixir is at revision {current_revision}")
        self.current_revision = current_revision


class ElixirHasActiveRuns(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ElixirRecord:
    id: str
    user_id: str
    name: str
    run_mode: str
    revision: int
    definition: Dict[str, Any]
    results: Dict[str, Any]
    result_revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row) -> "ElixirRecord":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            run_mode=row["run_mode"],
            revision=row["revision"],
            definition=json.loads(row["definition"]),
            results=json.loads(row["results"]),
            result_revision=row["result_revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_summary(self) -> Dict[str, Any]:
        nodes = self.definition.get("nodes") or []
        return {
            "id": self.id,
            "name": self.name,
            "run_mode": self.run_mode,
            "revision": self.revision,
            "node_count": len(nodes),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_full(self) -> Dict[str, Any]:
        return {
            **self.to_summary(),
            "definition": self.definition,
            "results": self.results,
            "result_revision": self.result_revision,
        }
