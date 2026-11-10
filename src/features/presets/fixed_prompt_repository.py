from typing import Optional

from src.features.presets.fixed_prompt import FixedPrompt
from src.platform.database import get_cursor


class PresetFixedPromptRepository:
    def get(self, preset_id: str) -> Optional[FixedPrompt]:
        with get_cursor() as cursor:
            cursor.execute("SELECT fixed_prompt FROM presets WHERE preset_id = ?", (preset_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return FixedPrompt.from_json(row["fixed_prompt"])

    def set(self, preset_id: str, fixed: FixedPrompt) -> bool:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE presets SET fixed_prompt = ? WHERE preset_id = ?",
                (fixed.to_json(), preset_id),
            )
            return cursor.rowcount > 0
