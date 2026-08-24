"""
Migration 091: durable per-generation resource/timing stats.

The admin Stats page (``src/features/stats/``) computes everything by
aggregating the ``generations`` table live. That works for counts/durations
that live on the row itself, but two requirements don't:

* Cold vs. warm start and peak VRAM/RAM/CPU come from the per-generation
  resource capture in ``GenerationManager.generate()``
  (``src/features/generation/generation.py``) and the model-lifecycle lease
  hit/miss counters (``src/platform/runtime/model_lifecycle/manager.py``).
  Both are only known IN MEMORY at generation-completion time -- they are
  never persisted on the ``generations`` row itself, so there is nothing on
  disk to aggregate later.
* "Removing a generation should not remove the stat, but the generation
  should still fully delete." ``generations`` is deleted with a plain
  ``DELETE FROM generations WHERE id = ?``
  (``GenerationRepository.delete``), and every child table that must die with
  it (``generation_files``, ``generation_parameters``, ...) does so via an
  ``ON DELETE CASCADE`` foreign key. A stats table must NOT be one of those
  child tables.

``generation_stats`` is therefore a one-row-per-completed-generation table,
written ONCE at the generation.after_complete seam
(``GenerationOrchestrator._finish_generation``,
``src/features/generation/orchestrator.py``), that:

* carries ``generation_id`` as a PLAIN TEXT column -- deliberately no
  ``FOREIGN KEY`` / ``ON DELETE`` clause of any kind, so deleting the
  generation (or its files) can never cascade into this table. This is the
  entire mechanism behind "removing generation should not remove the stat".
* carries both ``preset_id`` (opaque ULID/path, may reference a preset that
  is later deleted from disk) AND ``preset_name`` (resolved once, at write
  time, from the on-disk ``preset.yml`` -- same source
  ``StatsManager._preset_names()`` already uses) so the admin page can still
  show a human name after the preset itself is gone.
* leaves every resource column NULL when the corresponding capture wasn't
  available for that run (e.g. a non-native backend with no model-lifecycle
  lease never produces a cold/warm signal) -- NULL means "not captured",
  never a guessed 0/false.

Not backfilled: only generations completed AFTER this migration lands get a
row. Older generations simply have no stats row, by design ("do NOT
backfill old generations").
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS generation_stats (
                id TEXT PRIMARY KEY,
                generation_id TEXT NOT NULL,
                preset_id TEXT,
                preset_name TEXT,
                engine TEXT,
                backend_id TEXT,
                duration_ms INTEGER,
                cold_start INTEGER,
                model_load_ms REAL,
                peak_vram_mb REAL,
                peak_ram_mb REAL,
                cpu_percent REAL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # The read path (GenerationStatsRepository) never scans this table
        # without filtering by preset_id, and cold/warm breakdowns group by
        # (preset_id, cold_start) -- both covered by one composite index.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_generation_stats_preset "
            "ON generation_stats (preset_id, cold_start)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_generation_stats_created_at "
            "ON generation_stats (created_at)"
        )
        # Deliberately NO index/FK on generation_id -- this table is never
        # joined back to `generations` (that would defeat the "don't scan
        # generations to read stats" design), it only needs to exist so a
        # stat row can be traced back to a still-live generation when one
        # happens to still exist.


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_generation_stats_created_at")
        cursor.execute("DROP INDEX IF EXISTS idx_generation_stats_preset")
        cursor.execute("DROP TABLE IF EXISTS generation_stats")
