from typing import Iterable

from src.platform.database.rows import now_iso


class ModelPresetScopeRepository:
    def get(self, model_id: str) -> list[str]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT preset_id FROM model_preset_scopes WHERE model_id = ? ORDER BY preset_id",
                (model_id,),
            )
            return [row["preset_id"] for row in cursor.fetchall()]

    def replace(self, model_id: str, preset_ids: Iterable[str]) -> list[str]:
        from src.platform.database.database import db

        ordered = sorted(set(preset_ids))
        now = now_iso()
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM model_preset_scopes WHERE model_id = ?", (model_id,))
            cursor.executemany(
                "INSERT INTO model_preset_scopes (model_id, preset_id, created_at) VALUES (?, ?, ?)",
                [(model_id, preset_id, now) for preset_id in ordered],
            )
        return ordered

    def model_ids_scoped_away_from(self, preset_id: str) -> set[str]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT model_id FROM model_preset_scopes WHERE model_id NOT IN "
                "(SELECT model_id FROM model_preset_scopes WHERE preset_id = ?)",
                (preset_id,),
            )
            return {row["model_id"] for row in cursor.fetchall()}
