"""Write-side orchestration in front of ``GenerationStatsRepository``.

Resolves a preset's *name* at record time so it survives the preset later
being deleted from disk (mirrors ``StatsManager._preset_names()``'s
reasoning). Reads go straight from the controller to
``GenerationStatsRepository`` - there is no mutation involved, so there is
nothing for this manager to add.
"""

import logging
from typing import Optional

from src.features.presets.file_repository import FilePresetRepository
from src.features.stats.generation_stats_repository import GenerationStatsRepository

logger = logging.getLogger(__name__)


class GenerationStatsManager:
    def __init__(
        self,
        generation_stats_repository: GenerationStatsRepository,
        file_preset_repository: FilePresetRepository,
    ):
        self.generation_stats_repository = generation_stats_repository
        self.file_preset_repository = file_preset_repository

    def record_completion(
        self,
        *,
        generation_id: str,
        preset_id: Optional[str],
        engine: Optional[str],
        backend_id: Optional[str],
        duration_ms: Optional[int],
        cold_start: Optional[bool],
        model_load_ms: Optional[float],
        peak_vram_mb: Optional[float],
        peak_ram_mb: Optional[float],
        cpu_percent: Optional[float],
    ) -> None:
        """Resolve the preset's current display name (best-effort -- a
        missing/malformed preset on disk must never block writing the stat
        row) and write one ``generation_stats`` row. Called from
        ``GenerationOrchestrator._finish_generation``, which already wraps
        this in its own best-effort try/except -- this method still logs its
        own failures so a silently-broken write path is diagnosable.
        """
        preset_name = None
        if preset_id:
            try:
                template = self.file_preset_repository.find_preset_by_id(preset_id)
                if template is not None:
                    preset_name = getattr(template, "name", None)
            except Exception:
                logger.exception(
                    "failed resolving preset name for %s; recording stat without it",
                    preset_id,
                )

        try:
            self.generation_stats_repository.record_completion(
                generation_id=generation_id,
                preset_id=preset_id,
                preset_name=preset_name or preset_id,
                engine=engine,
                backend_id=backend_id,
                duration_ms=duration_ms,
                cold_start=cold_start,
                model_load_ms=model_load_ms,
                peak_vram_mb=peak_vram_mb,
                peak_ram_mb=peak_ram_mb,
                cpu_percent=cpu_percent,
            )
        except Exception:
            logger.exception("failed writing generation_stats row for %s", generation_id)
