#!/usr/bin/env python3
import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Set, Tuple

DESCRIPTION = """List, then on request delete, what generations deleted before deletion removed
everything they owned left behind: files rows no generation lists, their tags and
index queue rows, the notifications about those generations, and the files still
under their folders in storage/generations. Nothing is deleted without --apply.
Stop the application, or run this inside its container, and back up db.sqlite
before --apply."""

OWNER_NOTIFICATION_TYPES = ("generation.completed", "generation.failed", "generation.warning")

ORPHAN_FILES = """
SELECT f.id, f.file_path, f.file_type, f.thumbnail_small, f.thumbnail_medium, f.thumbnail_large,
       (SELECT COUNT(*) FROM media_system_tags t WHERE t.file_id = f.id) AS tags,
       (SELECT COUNT(*) FROM media_index_queue q WHERE q.file_id = f.id) AS queued
FROM files f
WHERE f.file_path LIKE 'generations/%'
  AND NOT EXISTS (SELECT 1 FROM generation_files gf WHERE gf.file_id = f.id)
  AND NOT EXISTS (SELECT 1 FROM model_files mf WHERE mf.file_id = f.id)
  AND NOT EXISTS (SELECT 1 FROM model_preview_media mp WHERE mp.file_id = f.id)
  AND NOT EXISTS (SELECT 1 FROM phrasebook_values pv WHERE pv.preview_file_id = f.id)
  AND NOT EXISTS (
      SELECT 1 FROM models m
      WHERE CASE WHEN json_valid(m.preview_media) THEN json_extract(m.preview_media, '$.file_id') END = f.id
  )
ORDER BY f.created_at, f.id
"""

ORPHAN_NOTIFICATIONS = f"""
SELECT id, type, created_at, generation_id FROM (
    SELECT n.id, n.type, n.created_at,
           CASE WHEN json_valid(n.metadata) THEN json_extract(n.metadata, '$.generation_id') END AS generation_id
    FROM notifications n
    WHERE n.type IN ({', '.join('?' * len(OWNER_NOTIFICATION_TYPES))})
)
WHERE generation_id IS NOT NULL AND generation_id NOT IN (SELECT id FROM generations)
ORDER BY created_at, id
"""


@dataclass
class Leftovers:
    files: List[sqlite3.Row] = field(default_factory=list)
    notifications: List[sqlite3.Row] = field(default_factory=list)
    stored: List[Tuple[str, int]] = field(default_factory=list)
    folders: List[str] = field(default_factory=list)
    stats_kept: int = 0
    ledger_kept: int = 0


def _keys_of(row) -> List[str]:
    base = PurePosixPath(row["file_path"]).parent
    keys = [row["file_path"]]
    for column in ("thumbnail_small", "thumbnail_medium", "thumbnail_large"):
        if row[column]:
            relative = PurePosixPath(row[column])
            keys.append((base / relative).as_posix())
            keys.append((base / relative.parent / f"{relative.stem}_animated.webp").as_posix())
    return keys


def _cited_keys(connection, leaving: Set[str]) -> Set[str]:
    cited = set()
    rows = connection.execute(
        "SELECT id, file_path, thumbnail_small, thumbnail_medium, thumbnail_large FROM files WHERE file_path LIKE 'generations/%'"
    )
    for row in rows:
        if row["id"] not in leaving:
            cited.update(_keys_of(row))
    return cited


def _stored_leftovers(storage: Path, generation_ids: Set[str], cited: Set[str]) -> Tuple[List[Tuple[str, int]], List[str]]:
    stored, folders = [], []
    root = storage / "generations"
    if not root.is_dir():
        return stored, folders
    for day in sorted(path for path in root.iterdir() if path.is_dir()):
        for folder in sorted(path for path in day.iterdir() if path.is_dir()):
            if folder.name in generation_ids:
                continue
            kept = False
            for path in sorted(item for item in folder.rglob("*") if item.is_file()):
                key = path.relative_to(storage).as_posix()
                if key in cited:
                    kept = True
                else:
                    stored.append((key, path.stat().st_size))
            if not kept:
                folders.append(folder.relative_to(storage).as_posix())
    return stored, folders


def find_leftovers(connection, storage: Path) -> Leftovers:
    found = Leftovers()
    found.files = connection.execute(ORPHAN_FILES).fetchall()
    found.notifications = connection.execute(ORPHAN_NOTIFICATIONS, OWNER_NOTIFICATION_TYPES).fetchall()
    generation_ids = {row[0] for row in connection.execute("SELECT id FROM generations")}
    cited = _cited_keys(connection, {row["id"] for row in found.files})
    found.stored, found.folders = _stored_leftovers(storage, generation_ids, cited)
    found.stats_kept = connection.execute(
        "SELECT COUNT(*) FROM generation_stats WHERE generation_id NOT IN (SELECT id FROM generations)"
    ).fetchone()[0]
    found.ledger_kept = connection.execute(
        "SELECT COUNT(*) FROM limit_events WHERE ref_id IS NOT NULL AND ref_id NOT IN (SELECT id FROM generations)"
    ).fetchone()[0]
    return found


def _size(count: int) -> str:
    return f"{count / 1_000_000:.1f} MB" if count >= 100_000 else f"{count / 1000:.1f} kB"


def report(found: Leftovers) -> List[str]:
    lines = [f"files rows no generation lists: {len(found.files)}"]
    for row in found.files:
        lines.append(f"  {row['id']}  {row['file_type']}  {row['file_path']}  ({row['tags']} tags, {row['queued']} index queue rows)")
    lines.append(f"notifications about deleted generations: {len(found.notifications)}")
    for row in found.notifications:
        lines.append(f"  {row['id']}  {row['type']}  {row['generation_id']}  {row['created_at']}")
    total = sum(size for _, size in found.stored)
    lines.append(f"files under the folders of deleted generations: {len(found.stored)}, {_size(total)}, in {len(found.folders)} folders")
    for key, size in found.stored:
        lines.append(f"  {key}  {_size(size)}")
    lines.append(
        f"kept on purpose: {found.stats_kept} generation_stats rows (durable timing statistics) "
        f"and {found.ledger_kept} limit_events rows (usage ledger, pruned after 40 days)"
    )
    return lines


def apply(connection, storage: Path, found: Leftovers) -> Dict[str, int]:
    file_ids = [row["id"] for row in found.files]
    notification_ids = [row["id"] for row in found.notifications]
    with connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for start in range(0, len(file_ids), 500):
            batch = file_ids[start:start + 500]
            connection.execute(f"DELETE FROM files WHERE id IN ({', '.join('?' * len(batch))})", batch)
        for start in range(0, len(notification_ids), 500):
            batch = notification_ids[start:start + 500]
            connection.execute(f"DELETE FROM notifications WHERE id IN ({', '.join('?' * len(batch))})", batch)
    removed = 0
    directories = set()
    for key, _ in found.stored:
        try:
            (storage / key).unlink()
            removed += 1
        except FileNotFoundError:
            pass
        parent = PurePosixPath(key).parent
        while len(parent.parts) >= 3:
            directories.add(parent)
            parent = parent.parent
    for folder in found.folders:
        directories.add(PurePosixPath(folder))
        directories.update(PurePosixPath(path.relative_to(storage).as_posix()) for path in (storage / folder).rglob("*") if path.is_dir())
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        try:
            (storage / directory.as_posix()).rmdir()
        except OSError:
            continue
    return {"files_rows": len(file_ids), "notifications": len(notification_ids), "stored_files": removed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--storage", default=os.environ.get("POTIONUI_STORAGE_PATH", "storage"), help="the storage folder (default: $POTIONUI_STORAGE_PATH or ./storage)")
    parser.add_argument("--db", help="the database (default: <storage>/db.sqlite)")
    parser.add_argument("--apply", action="store_true", help="delete what is listed")
    args = parser.parse_args(argv)
    storage = Path(args.storage)
    database = Path(args.db) if args.db else storage / "db.sqlite"
    if not database.is_file():
        print(f"No database at {database}", file=sys.stderr)
        return 2
    uri = database.resolve().as_uri() + ("" if args.apply else "?mode=ro")
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        found = find_leftovers(connection, storage)
        print("\n".join(report(found)))
        if not args.apply:
            print("Dry run: nothing was deleted. Run again with --apply to delete the rows and files listed above.")
            return 0
        done = apply(connection, storage, found)
        print(f"Deleted {done['files_rows']} files rows, {done['notifications']} notifications and {done['stored_files']} stored files.")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
