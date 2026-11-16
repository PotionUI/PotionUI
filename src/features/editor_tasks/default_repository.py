from dataclasses import dataclass
from typing import List, Optional

from src.platform.database import get_cursor

@dataclass(frozen=True)
class TaskDefault:
    user_id: str
    task: str
    preset_id: str
    mode: str
    variant: Optional[str]
    setup_kind: str
    setup_id: Optional[str]
    setup_name: Optional[str]
    preset_name: Optional[str]
    mode_label: Optional[str]
    set_at: str

_COLUMNS = (
    "user_id, task, preset_id, mode, variant, setup_kind, setup_id, setup_name, preset_name, mode_label, set_at"
)

def _to_default(row) -> TaskDefault:
    return TaskDefault(
        user_id=row["user_id"],
        task=row["task"],
        preset_id=row["preset_id"],
        mode=row["mode"],
        variant=row["variant"],
        setup_kind=row["setup_kind"],
        setup_id=row["setup_id"],
        setup_name=row["setup_name"],
        preset_name=row["preset_name"],
        mode_label=row["mode_label"],
        set_at=row["set_at"],
    )

class TaskDefaultRepository:

    def list_for_user(self, user_id: str) -> List[TaskDefault]:
        with get_cursor() as cursor:
            cursor.execute(f"SELECT {_COLUMNS} FROM editor_task_defaults WHERE user_id = ?", (user_id,))
            return [_to_default(row) for row in cursor.fetchall()]

    def upsert(self, record: TaskDefault) -> None:
        with get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO editor_task_defaults ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (user_id, task) DO UPDATE SET preset_id = excluded.preset_id, mode = excluded.mode, "
                "variant = excluded.variant, setup_kind = excluded.setup_kind, setup_id = excluded.setup_id, "
                "setup_name = excluded.setup_name, preset_name = excluded.preset_name, "
                "mode_label = excluded.mode_label, set_at = excluded.set_at",
                (
                    record.user_id, record.task, record.preset_id, record.mode, record.variant, record.setup_kind,
                    record.setup_id, record.setup_name, record.preset_name, record.mode_label, record.set_at,
                ),
            )

    def delete(self, user_id: str, task: str) -> bool:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM editor_task_defaults WHERE user_id = ? AND task = ?", (user_id, task))
            return cursor.rowcount > 0
