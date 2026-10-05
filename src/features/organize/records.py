from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.platform.database.rows import dt_column, dt_iso, json_column, row_get

SUBJECT_TRIGGERS = {
    "generation": "generation_completed",
    "upload": "upload_created",
    "model": "model_added",
}

COLLECTION_SCOPES = {
    "generation": "history",
    "upload": "library",
    "model": "models",
}

TAG_TYPES = {
    "generation": "GENERATION",
    "upload": "UPLOAD",
    "model": "MODEL",
}


@dataclass
class Rule:
    id: str
    user_id: str
    name: str
    subject: str
    trigger: str
    match: str = "all"
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    stop_after: bool = False
    position: int = 0
    paused_reason: Optional[str] = None
    paused_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "Rule":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            subject=row["subject"],
            trigger=row["trigger"],
            match=row["match_mode"],
            conditions=json_column(row["conditions_json"], []),
            actions=json_column(row["actions_json"], []),
            enabled=bool(row["enabled"]),
            stop_after=bool(row["stop_after"]),
            position=row["position"],
            paused_reason=row["paused_reason"],
            paused_at=dt_column(row["paused_at"]),
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
            last_run_at=dt_column(row["last_run_at"]),
        )


@dataclass
class Run:
    id: str
    rule_id: str
    rule_name: str
    user_id: str
    subject: str
    kind: str
    status: str
    matched: int = 0
    applied: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    undone_at: Optional[datetime] = None
    rule_deleted: bool = False

    @classmethod
    def from_row(cls, row) -> "Run":
        return cls(
            id=row["id"],
            rule_id=row["rule_id"],
            rule_name=row["rule_name"],
            user_id=row["user_id"],
            subject=row["subject"],
            kind=row["kind"],
            status=row["status"],
            matched=row["matched"],
            applied=row["applied"],
            started_at=dt_column(row["started_at"]),
            finished_at=dt_column(row["finished_at"]),
            undone_at=dt_column(row["undone_at"]),
            rule_deleted=bool(row_get(row, "rule_deleted", 0)),
        )

    def base_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "rule_deleted": self.rule_deleted,
            "subject": self.subject,
            "kind": self.kind,
            "status": self.status,
            "started_at": dt_iso(self.started_at),
            "finished_at": dt_iso(self.finished_at),
            "matched": self.matched,
            "applied": self.applied,
        }


@dataclass
class Application:
    id: str
    run_id: str
    rule_id: str
    user_id: str
    item_type: str
    item_id: str
    action_kind: str
    target_type: str
    target_id: str
    target_name: str = ""
    change: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    undone_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "Application":
        return cls(
            id=row["id"],
            run_id=row["run_id"],
            rule_id=row["rule_id"],
            user_id=row["user_id"],
            item_type=row["item_type"],
            item_id=row["item_id"],
            action_kind=row["action_kind"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            target_name=row["target_name"] or "",
            change=json_column(row["change_json"], {}),
            created_at=dt_column(row["created_at"]),
            undone_at=dt_column(row["undone_at"]),
        )


@dataclass
class Controls:
    paused: bool = False
    rule_cap: Optional[int] = None
    hourly_limit: Optional[int] = None
