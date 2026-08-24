"""Usage statistics for the admin Stats page.

Thin orchestration over `StatsRepository`: the SQL lives in the repository, and this manager
adds the things SQL cannot know -- chiefly that a `preset_id` is an opaque ULID whose human
name lives in the preset's on-disk `preset.yml`, not in the `presets` table.
"""

import logging
from typing import Any, Dict, List, Optional

from src.features.presets.file_repository import FilePresetRepository
from src.features.stats.repository import DIMENSIONS, StatsRepository

logger = logging.getLogger(__name__)

# Dimensions whose keys are ULIDs and need a display name resolved from disk.
_NAMED_DIMENSIONS = {'preset'}


class StatsManager:
    def __init__(self, stats_repository: StatsRepository, file_preset_repository: FilePresetRepository):
        self.stats_repository = stats_repository
        self.file_preset_repository = file_preset_repository

    # --- helpers ------------------------------------------------------------------

    def _preset_names(self) -> Dict[str, str]:
        """preset_id -> display name, read from the on-disk preset.yml files.

        The `presets` table stores only ids. A generation can also reference a preset that has
        since been deleted from disk, so callers must tolerate a missing name.
        """
        try:
            return {
                p['id']: p.get('name') or p['id']
                for p in self.file_preset_repository.list_all_presets()
                if p.get('id')
            }
        except Exception:
            # A malformed preset on disk must not take down the whole Stats page.
            logger.exception("Failed to load preset names; falling back to raw ids")
            return {}

    def _label_items(self, dimension: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if dimension not in _NAMED_DIMENSIONS:
            for item in items:
                item['label'] = item['key']
            return items

        names = self._preset_names()
        for item in items:
            item['label'] = names.get(item['key'], item['key'])
        return items

    # --- api ----------------------------------------------------------------------

    @staticmethod
    def dimensions() -> List[str]:
        return list(DIMENSIONS)

    def timeseries(self, metric: str, bucket: str,
                   date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        points = self.stats_repository.timeseries(
            metric=metric, bucket=bucket, date_from=date_from, date_to=date_to
        )
        return {'metric': metric, 'bucket': bucket, 'points': points}

    def breakdown(self, dimension: str, limit: int = 10,
                  date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        result = self.stats_repository.breakdown(
            dimension=dimension, limit=limit, date_from=date_from, date_to=date_to
        )
        result['items'] = self._label_items(dimension, result['items'])
        return result

    def storage(self, date_from: Optional[str] = None, date_to: Optional[str] = None,
                bucket: str = 'day', limit: int = 30) -> Dict[str, Any]:
        return self.stats_repository.storage(date_from=date_from, date_to=date_to, bucket=bucket, limit=limit)
