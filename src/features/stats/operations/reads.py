"""Read-side operations for the admin Stats page.

The SQL lives in `StatsRepository`; these functions add the thing SQL cannot
know -- chiefly that a `preset_id` is an opaque ULID whose human name lives in
the preset's on-disk `preset.yml`, not in the `presets` table.
"""

import logging
from typing import Any, Dict, List, Optional

from src.features.presets.file_repository import FilePresetRepository
from src.features.stats.repository import DIMENSIONS, StatsRepository

logger = logging.getLogger(__name__)

# Dimensions whose keys are ULIDs and need a display name resolved from disk.
_NAMED_DIMENSIONS = {'preset'}


def _preset_names(file_preset_repository: FilePresetRepository) -> Dict[str, str]:
    """preset_id -> display name, read from the on-disk preset.yml files.

    The `presets` table stores only ids. A generation can also reference a preset that has
    since been deleted from disk, so callers must tolerate a missing name.
    """
    try:
        return {
            p['id']: p.get('name') or p['id']
            for p in file_preset_repository.list_all_presets()
            if p.get('id')
        }
    except Exception:
        # A malformed preset on disk must not take down the whole Stats page.
        logger.exception("Failed to load preset names; falling back to raw ids")
        return {}


def _label_items(
    file_preset_repository: FilePresetRepository, dimension: str, items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if dimension not in _NAMED_DIMENSIONS:
        for item in items:
            item['label'] = item['key']
        return items

    names = _preset_names(file_preset_repository)
    for item in items:
        item['label'] = names.get(item['key'], item['key'])
    return items


def dimensions() -> List[str]:
    return list(DIMENSIONS)


def timeseries(
    stats_repository: StatsRepository, metric: str, bucket: str,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
) -> Dict[str, Any]:
    points = stats_repository.timeseries(
        metric=metric, bucket=bucket, date_from=date_from, date_to=date_to
    )
    return {'metric': metric, 'bucket': bucket, 'points': points}


def breakdown(
    stats_repository: StatsRepository,
    file_preset_repository: FilePresetRepository,
    dimension: str,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    result = stats_repository.breakdown(
        dimension=dimension, limit=limit, date_from=date_from, date_to=date_to
    )
    result['items'] = _label_items(file_preset_repository, dimension, result['items'])
    return result


def storage(
    stats_repository: StatsRepository,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    bucket: str = 'day',
    limit: int = 30,
) -> Dict[str, Any]:
    return stats_repository.storage(date_from=date_from, date_to=date_to, bucket=bucket, limit=limit)
