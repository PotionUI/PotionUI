"""
Migration 094: model_preview_media -- multiple admin-set previews per model.

Admin model previews need to support pasting/loading multiple preview media
items, not only one. Migration 085 gave a model
exactly one admin-set preview, stored as a JSON blob on `models.preview_media`
({file_id, url, type, name?}). This migration adds an *ordered list* of
previews alongside it, additive: nothing about `models.preview_media` or the
existing `PUT /api/models/{id}/preview` (single-set/clear) endpoint changes.

Design:

* `models.preview_media` keeps its existing job — it is now a denormalized
  mirror of `model_preview_media` position 0 ("the primary preview"), so every
  render site that already reads `model.preview_media` (ModelCard,
  LoraPickerField, ModelCollectionBrowser, ModelField, `modelPreview.ts`)
  keeps working with zero changes. `ModelIndexManager` is the only writer of
  both and is responsible for keeping them in sync.
* Rows carry their own `file_id`/`url`/`type`/`name` rather than joining
  through `files` at read time — matches how the single-preview column already
  denormalizes those fields, and keeps the previews list servable without a
  join.
* `position` is a plain 0-based integer, compacted (0..n-1, no gaps) on every
  delete/reorder by the manager — there's no reordering UI need for sparse
  positions, and compacted integers make "position 0 is primary" trivial to
  reason about.
* `ON DELETE CASCADE` on model_id: preview rows are the model's own data, same
  call as `session_versions` (migration 092) — deleting a model should not
  orphan its preview rows.
* Models that already had a single preview set through the pre-existing
  column (before this migration, or via the untouched legacy endpoint after
  it) have no row here yet. `ModelIndexManager` lazily backfills one row from
  `models.preview_media` the first time the new list endpoints touch a model,
  rather than this migration walking every row — additive and avoids a data
  migration that duplicates file-existence validation the manager already
  does at read time.
"""

from src.platform.database.database import db


def up():
    """Add the model_preview_media table."""
    with db.get_cursor() as cursor:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_preview_media (
                id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                file_id TEXT,
                url TEXT NOT NULL,
                type TEXT NOT NULL,  -- 'image' | 'video' | 'audio'
                name TEXT,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models (id) ON DELETE CASCADE
            )
        ''')
        # The only read pattern is "list a model's previews in order".
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_model_preview_media_model
            ON model_preview_media (model_id, position)
        ''')


def down():
    """Rollback the migration."""
    with db.get_cursor() as cursor:
        cursor.execute('DROP TABLE IF EXISTS model_preview_media')
