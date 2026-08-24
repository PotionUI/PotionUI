from typing import Any, Dict, List, Optional, Set

from src.platform.util.ids import generate_ulid

from src.platform.database import db
from src.features.models.availability_records import ModelAvailability


class ModelAvailabilityRepository:
    """Which backend can load which model, and under what name."""

    def upsert(self, availability: ModelAvailability) -> ModelAvailability:
        """Record (or refresh) a backend's claim about a model.

        UNIQUE(model_id, backend_id) means re-indexing updates the
        ref/size/confidence/digest rather than accumulating duplicates.
        """
        if not availability.id:
            availability.id = generate_ulid()

        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO model_availability
                    (id, model_id, backend_id, ref, size, confidence, digest, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(model_id, backend_id) DO UPDATE SET
                    ref = excluded.ref,
                    size = excluded.size,
                    confidence = excluded.confidence,
                    digest = excluded.digest,
                    indexed_at = CURRENT_TIMESTAMP
                """,
                (
                    availability.id,
                    availability.model_id,
                    availability.backend_id,
                    availability.ref,
                    availability.size,
                    availability.confidence,
                    availability.digest,
                ),
            )
            cursor.execute(
                "SELECT * FROM model_availability WHERE model_id = ? AND backend_id = ?",
                (availability.model_id, availability.backend_id),
            )
            row = cursor.fetchone()
            return ModelAvailability.from_row(row) if row else None

    def get(self, model_id: str, backend_id: str) -> Optional[ModelAvailability]:
        """The full claim (ref, confidence, digest) a backend makes about one model.

        `get_ref` only ever needed the ref; digest-conflict routing checks need the
        whole row, so this is the resolution surface for "what digest does backend X
        have on record for model Y" - the piece a remote-execution package builder
        would use to embed an expected digest per model.
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM model_availability WHERE model_id = ? AND backend_id = ?",
                (model_id, backend_id),
            )
            row = cursor.fetchone()
            return ModelAvailability.from_row(row) if row else None

    def get_for_backend(self, backend_id: str) -> List[ModelAvailability]:
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM model_availability WHERE backend_id = ?", (backend_id,)
            )
            return [ModelAvailability.from_row(r) for r in cursor.fetchall()]

    def stats_for_backend(self, backend_id: str) -> Dict[str, Any]:
        """Aggregate indexing stats for one backend: how many models it reported,
        how much disk space they total, and when it last reported them.

        A single SQL aggregate rather than `get_for_backend` + Python summation -
        this is admin-dashboard-facing and only needs the three numbers, not the
        rows themselves.
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS count, SUM(size) AS total_size, MAX(indexed_at) AS last_indexed_at
                FROM model_availability WHERE backend_id = ?
                """,
                (backend_id,),
            )
            row = cursor.fetchone()
            return {
                "indexed_models": row["count"] or 0,
                "total_size_bytes": row["total_size"] or 0,
                "last_indexed_at": row["last_indexed_at"],
            }

    def any_indexed(self, backend_ids: List[str]) -> bool:
        """Has any of these backends ever been indexed?

        Cheaper than loading rows: this runs on every generation to decide whether
        availability may constrain backend selection at all.
        """
        if not backend_ids:
            return False

        placeholders = ",".join("?" for _ in backend_ids)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT 1 FROM model_availability WHERE backend_id IN ({placeholders}) LIMIT 1",
                tuple(backend_ids),
            )
            return cursor.fetchone() is not None

    def has_any(self) -> bool:
        """Has any backend, anywhere, been indexed?

        Distinguishes "this model is available nowhere" from "nothing has been indexed",
        which look identical from an empty `backend_ids` list.
        """
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM model_availability LIMIT 1")
            return cursor.fetchone() is not None

    def model_ids_for_backends(self, backend_ids: List[str]) -> List[str]:
        """Every model loadable by at least one of these backends.

        Fed to `model_repo.get_all(allowed_model_ids=...)` so availability becomes a
        WHERE clause rather than a post-filter. Filtering after the query would force it
        to be unpaginated - the picker would then load the whole library, with providers
        and tags, on every open.
        """
        if not backend_ids:
            return []

        placeholders = ",".join("?" for _ in backend_ids)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT DISTINCT model_id FROM model_availability "
                f"WHERE backend_id IN ({placeholders})",
                tuple(backend_ids),
            )
            return [r["model_id"] for r in cursor.fetchall()]

    def get_for_model(self, model_id: str) -> List[ModelAvailability]:
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM model_availability WHERE model_id = ?", (model_id,)
            )
            return [ModelAvailability.from_row(r) for r in cursor.fetchall()]

    def conflicts_for(self, model_ids: List[str], backend_ids: List[str]) -> List[ModelAvailability]:
        """Digest-conflicted rows among these models, restricted to these backends.

        Feeds the actionable half of a routing failure: `backends_holding` already
        excludes these rows from "holds", so a caller that needs to explain WHY a
        model isn't available - conflicted vs never indexed - queries this
        separately rather than losing the distinction.
        """
        if not model_ids or not backend_ids:
            return []

        model_placeholders = ",".join("?" for _ in model_ids)
        backend_placeholders = ",".join("?" for _ in backend_ids)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM model_availability
                WHERE model_id IN ({model_placeholders})
                  AND backend_id IN ({backend_placeholders})
                  AND confidence = 'conflict'
                """,
                (*model_ids, *backend_ids),
            )
            return [ModelAvailability.from_row(r) for r in cursor.fetchall()]

    def backends_holding(self, model_ids: List[str]) -> Set[str]:
        """Backends that hold EVERY one of `model_ids`.

        This is the availability half of backend selection: a preset's engine narrows
        the candidates, and this narrows them further to backends that can actually
        load everything the user picked. An empty `model_ids` constrains nothing.

        A row whose `confidence` is `conflict` is excluded from "holds" - the backend
        answered, but with bytes that disagree with the model's canonical digest, which
        is worse than not answering at all. See CONFIDENCE_CONFLICT.
        """
        if not model_ids:
            return set()

        placeholders = ",".join("?" for _ in model_ids)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT backend_id
                FROM model_availability
                WHERE model_id IN ({placeholders}) AND confidence != 'conflict'
                GROUP BY backend_id
                HAVING COUNT(DISTINCT model_id) = ?
                """,
                (*model_ids, len(set(model_ids))),
            )
            return {r["backend_id"] for r in cursor.fetchall()}

    def backend_ids_by_model(self, model_ids: List[str]) -> Dict[str, List[str]]:
        """model_id -> backends holding it. Drives the per-model badges in the picker.

        Same conflict exclusion as `backends_holding` - a badge claiming a backend
        "has" a model it cannot actually be routed to would be misleading.
        """
        if not model_ids:
            return {}

        placeholders = ",".join("?" for _ in model_ids)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT model_id, backend_id FROM model_availability "
                f"WHERE model_id IN ({placeholders}) AND confidence != 'conflict'",
                tuple(model_ids),
            )
            out: Dict[str, List[str]] = {}
            for row in cursor.fetchall():
                out.setdefault(row["model_id"], []).append(row["backend_id"])
            return out

    def delete_for_backend(self, backend_id: str, keep_model_ids: Optional[Set[str]] = None) -> int:
        """Drop stale claims after a re-index.

        A model removed from the remote server must stop being offered, so anything not
        seen in the latest listing is deleted rather than left to fail at generation time.
        """
        with db.get_cursor() as cursor:
            if keep_model_ids:
                placeholders = ",".join("?" for _ in keep_model_ids)
                cursor.execute(
                    f"DELETE FROM model_availability WHERE backend_id = ? "
                    f"AND model_id NOT IN ({placeholders})",
                    (backend_id, *keep_model_ids),
                )
            else:
                cursor.execute(
                    "DELETE FROM model_availability WHERE backend_id = ?", (backend_id,)
                )
            return cursor.rowcount


model_availability_repo = ModelAvailabilityRepository()
