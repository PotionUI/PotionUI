import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.features.generation.grids.dto import Axis
from src.features.generation.records import Generation
from src.platform.database.rows import dt_column, dt_iso, json_column, now_iso
from src.platform.util.ids import generate_ulid


@dataclass
class GridRecord:
    id: str
    user_id: str
    preset_id: Optional[str]
    tab_id: Optional[str]
    x_axis: Axis
    y_axis: Optional[Axis]
    lock_seed: bool
    base_request: Dict[str, Any]
    seeds: Dict[str, int] = field(default_factory=dict)
    created_at: Optional[datetime] = None

    @property
    def cols(self) -> int:
        return len(self.x_axis.values)

    @property
    def rows(self) -> int:
        return len(self.y_axis.values) if self.y_axis is not None else 1

    @property
    def cell_count(self) -> int:
        return self.cols * self.rows

    def summary(self) -> Dict[str, Any]:
        return {"id": self.id, "cols": self.cols, "rows": self.rows, "cell_count": self.cell_count}

    @classmethod
    def from_row(cls, row) -> "GridRecord":
        y_axis = json_column(row["y_axis"])
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            preset_id=row["preset_id"],
            tab_id=row["tab_id"],
            x_axis=Axis.model_validate(json.loads(row["x_axis"])),
            y_axis=Axis.model_validate(y_axis) if y_axis else None,
            lock_seed=bool(row["lock_seed"]),
            base_request=json.loads(row["base_request"]),
            seeds={key: int(value) for key, value in (json_column(row["seeds"], {}) or {}).items()},
            created_at=dt_column(row["created_at"]),
        )

    def created_iso(self) -> Optional[str]:
        return dt_iso(self.created_at)


class GridRepository:
    def create(
        self,
        user_id: str,
        preset_id: Optional[str],
        tab_id: Optional[str],
        x_axis: Axis,
        y_axis: Optional[Axis],
        lock_seed: bool,
        base_request: Dict[str, Any],
        seeds: Dict[str, int],
    ) -> GridRecord:
        from src.platform.database.database import db
        grid_id = generate_ulid()
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generation_grids (id, user_id, preset_id, tab_id, x_axis, y_axis, lock_seed, "
                "base_request, seeds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    grid_id,
                    user_id,
                    preset_id,
                    tab_id,
                    x_axis.model_dump_json(),
                    y_axis.model_dump_json() if y_axis is not None else None,
                    1 if lock_seed else 0,
                    json.dumps(base_request, default=str),
                    json.dumps(seeds),
                    now_iso(),
                ),
            )
        return self.get(grid_id)

    def get(self, grid_id: str) -> Optional[GridRecord]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM generation_grids WHERE id = ?", (grid_id,))
            row = cursor.fetchone()
        return GridRecord.from_row(row) if row else None

    def get_many(self, grid_ids: List[str]) -> Dict[str, GridRecord]:
        if not grid_ids:
            return {}
        from src.platform.database.database import db
        placeholders = ",".join("?" * len(grid_ids))
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT * FROM generation_grids WHERE id IN ({placeholders})", list(grid_ids))
            rows = cursor.fetchall()
        return {row["id"]: GridRecord.from_row(row) for row in rows}

    def update_seeds(self, grid_id: str, seeds: Dict[str, int]) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("UPDATE generation_grids SET seeds = ? WHERE id = ?", (json.dumps(seeds), grid_id))

    def delete(self, grid_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generation_grids WHERE id = ?", (grid_id,))
            return cursor.rowcount > 0

    def cells(self, grid_id: str) -> List[Generation]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM generations WHERE grid_id = ? ORDER BY grid_y, grid_x, created_at DESC, id DESC",
                (grid_id,),
            )
            return [Generation.from_row(row) for row in cursor.fetchall()]

    def representative_ids(self, grid_ids: List[str]) -> Dict[str, str]:
        if not grid_ids:
            return {}
        from src.platform.database.database import db
        placeholders = ",".join("?" * len(grid_ids))
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT g.grid_id AS grid_id, g.id AS id FROM generations g WHERE g.grid_id IN ({placeholders}) "
                "AND g.id = (SELECT r0.id FROM generations r0 WHERE r0.grid_id = g.grid_id "
                "ORDER BY r0.grid_y, r0.grid_x LIMIT 1)",
                list(grid_ids),
            )
            return {row["grid_id"]: row["id"] for row in cursor.fetchall()}


grid_repo = GridRepository()
