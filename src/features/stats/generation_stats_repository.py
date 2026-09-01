"""Write and aggregate-read path for ``generation_stats``.

Companion table to ``StatsRepository`` (``repository.py``), but deliberately
separate: that repository aggregates the ``generations`` table itself
(counts, durations, breakdowns computed from rows that still exist);
this one is the durable, generation-independent store -- one row written
ONCE at generation completion, carrying data
(cold/warm start, peak VRAM/RAM, model-load time) that is only ever known in
memory at that moment and is never persisted anywhere else.

Every read here is a SQL aggregate over ``generation_stats`` alone -- never
a join back to ``generations`` (that would reintroduce the "scan the whole
generations table" cost this table exists to avoid, and would also silently
hide stats for since-deleted generations, defeating the durability this
table exists to provide).
"""

import logging
from typing import Any, Dict, List, Optional

from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

MAX_LIMIT = 200
DEFAULT_LIMIT = 10


def clamp_limit(limit: Optional[int], default: int = DEFAULT_LIMIT) -> int:
    """Every row-limit query param funnels through this -- keeps the admin
    page's "how many rows do you want to see" control from ever turning into
    an unbounded scan."""
    if limit is None:
        return default
    return max(1, min(int(limit), MAX_LIMIT))


class GenerationStatsRepository:
    def record_completion(
        self,
        *,
        generation_id: str,
        preset_id: Optional[str],
        preset_name: Optional[str],
        engine: Optional[str],
        backend_id: Optional[str],
        duration_ms: Optional[int],
        cold_start: Optional[bool],
        model_load_ms: Optional[float],
        peak_vram_mb: Optional[float],
        peak_ram_mb: Optional[float],
        cpu_percent: Optional[float],
    ) -> None:
        """Write one row. Called exactly once, from the generation.after_complete
        seam (``GenerationOrchestrator._finish_generation``). Never raises --
        the caller wraps this in a best-effort try/except, but the INSERT
        itself is a single row write with no side effects, so failure here
        should never be silent; callers should still log it."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO generation_stats (
                    id, generation_id, preset_id, preset_name, engine, backend_id,
                    duration_ms, cold_start, model_load_ms, peak_vram_mb, peak_ram_mb,
                    cpu_percent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generate_ulid(),
                    generation_id,
                    preset_id,
                    preset_name,
                    engine,
                    backend_id,
                    duration_ms,
                    None if cold_start is None else int(cold_start),
                    model_load_ms,
                    peak_vram_mb,
                    peak_ram_mb,
                    cpu_percent,
                ),
            )

    # --- reads ----------------------------------------------------------------

    def preset_timing(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Cold vs. warm start counts/averages per preset, ranked by total
        run count. ``cold_start IS NULL`` rows (captured before a lease ever
        ran, e.g. a comfyui-engine generation) are counted in ``total_runs``
        but excluded from the cold/warm split -- an unknown start kind is
        never guessed as warm.
        """
        limit = clamp_limit(limit)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    preset_id,
                    COALESCE(MAX(preset_name), preset_id) AS preset_name,
                    COUNT(*) AS total_runs,
                    SUM(CASE WHEN cold_start = 1 THEN 1 ELSE 0 END) AS cold_runs,
                    SUM(CASE WHEN cold_start = 0 THEN 1 ELSE 0 END) AS warm_runs,
                    AVG(CASE WHEN cold_start = 1 THEN duration_ms END) AS avg_cold_duration_ms,
                    AVG(CASE WHEN cold_start = 0 THEN duration_ms END) AS avg_warm_duration_ms,
                    AVG(CASE WHEN cold_start = 1 THEN model_load_ms END) AS avg_model_load_ms
                FROM generation_stats
                WHERE preset_id IS NOT NULL
                GROUP BY preset_id
                ORDER BY total_runs DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in cursor.fetchall()]

    def preset_resources(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Peak/average VRAM, RAM and CPU per preset, ranked by run count.
        Every aggregate here silently skips NULL source rows (SQLite's
        default AVG/MAX behaviour) -- a preset that never ran with resource
        capture available (e.g. no CUDA device) reports NULL, not 0.
        """
        limit = clamp_limit(limit)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    preset_id,
                    COALESCE(MAX(preset_name), preset_id) AS preset_name,
                    COUNT(*) AS total_runs,
                    MAX(peak_vram_mb) AS peak_vram_mb,
                    AVG(peak_vram_mb) AS avg_vram_mb,
                    MAX(peak_ram_mb) AS peak_ram_mb,
                    AVG(peak_ram_mb) AS avg_ram_mb,
                    AVG(cpu_percent) AS avg_cpu_percent
                FROM generation_stats
                WHERE preset_id IS NOT NULL
                GROUP BY preset_id
                ORDER BY total_runs DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in cursor.fetchall()]

    def count(self) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM generation_stats")
            return cursor.fetchone()[0] or 0


generation_stats_repo = GenerationStatsRepository()
