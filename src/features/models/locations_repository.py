from typing import Any, Dict, Tuple


class ModelLocationsRepository:

    def aggregate_by_root_and_type(self) -> Dict[Tuple[str, str], Dict[str, int]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT root_id, model_type, COUNT(*) AS indexed_files, COALESCE(SUM(size), 0) AS size_bytes
                FROM model_locations
                WHERE status = 'present'
                GROUP BY root_id, model_type
                """
            )
            return {
                (row["root_id"], row["model_type"]): {
                    "indexed_files": row["indexed_files"],
                    "size_bytes": row["size_bytes"],
                }
                for row in cursor.fetchall()
            }

    def count_for_root(self, root_id: str) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM model_locations WHERE root_id = ?", (root_id,))
            row = cursor.fetchone()
            return row["n"] if row else 0
