"""
Cache of (path, size, mtime_ns) -> sha256 for the native model scan.

Hashing a multi-GB checkpoint is unavoidable the first time nothing on this host has
ever read its bytes, but paying that cost on every scan would make indexing a large
depot unusable. A cache hit means the file's size and mtime haven't moved since it was
last hashed, so `scan_native_models` can skip straight to the digest without touching
the file again. A changed mtime (or size) is a cache miss, which is exactly the signal
that matters here: a file replaced in place - accidentally or via a partial resync -
gets rehashed rather than silently reporting a stale digest.

Distinct from `models.sha256`: this cache is keyed by path and is populated purely by
scanning, independent of whether a `models` row exists yet for that path. See migration
110_model_availability_digest.py.
"""

from dataclasses import dataclass
from typing import Optional

from src.platform.database import db


@dataclass
class CachedHash:
    path: str
    size: int
    mtime_ns: int
    sha256: str


class ModelHashCacheRepository:
    def get(self, path: str) -> Optional[CachedHash]:
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT path, size, mtime_ns, sha256 FROM model_hash_cache WHERE path = ?",
                (path,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return CachedHash(
                path=row["path"], size=row["size"], mtime_ns=row["mtime_ns"], sha256=row["sha256"]
            )

    def put(self, path: str, size: int, mtime_ns: int, sha256: str) -> None:
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO model_hash_cache (path, size, mtime_ns, sha256, hashed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    size = excluded.size,
                    mtime_ns = excluded.mtime_ns,
                    sha256 = excluded.sha256,
                    hashed_at = CURRENT_TIMESTAMP
                """,
                (path, size, mtime_ns, sha256),
            )


model_hash_cache_repo = ModelHashCacheRepository()
