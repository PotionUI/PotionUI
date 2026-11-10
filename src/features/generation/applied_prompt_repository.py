import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.platform.database import get_cursor


class GenerationAppliedPromptRepository:
    def save(self, generation_id: str, applied: List[Dict[str, str]]) -> None:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT OR REPLACE INTO generation_applied_prompts (generation_id, applied, created_at) "
                "VALUES (?, ?, ?)",
                (generation_id, json.dumps(applied), datetime.now(timezone.utc).isoformat()),
            )

    def get(self, generation_id: str) -> Optional[List[Dict[str, str]]]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT applied FROM generation_applied_prompts WHERE generation_id = ?", (generation_id,)
            )
            row = cursor.fetchone()
        if row is None:
            return None
        try:
            applied = json.loads(row["applied"])
        except (TypeError, ValueError):
            return None
        return [entry for entry in applied if isinstance(entry, dict)] if isinstance(applied, list) else None
