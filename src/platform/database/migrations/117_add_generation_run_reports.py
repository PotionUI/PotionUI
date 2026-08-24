"""
Migration 117: durable per-generation run reports.

The admin history drawer shows live status-history/pipe-timer/artifact/plugin-
output data that today only ever exists as WebSocket messages
(`GenerationOutputSerializer`, `src/features/generation/websocket_handler.py`)
- nothing survives past the connection. `generation_run_reports` gives that
same data a home: one row per generation, written once by
`RunReportRecorder.flush()` when the generation reaches a terminal state
(`src/features/generation/run_report_recorder.py`).

Unlike `generation_stats` (091), which deliberately drops its FK so a stat
survives generation deletion, a run report has no meaning once its generation
is gone - `generation_id` is the primary key (inherently unique) and
`ON DELETE CASCADE` removes the report automatically alongside
`generation_files`/`generation_parameters` and everything else scoped to a
single generation.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS generation_run_reports (
                generation_id TEXT PRIMARY KEY,
                report TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE
            )
            """
        )


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS generation_run_reports")
