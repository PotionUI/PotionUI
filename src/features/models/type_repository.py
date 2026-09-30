import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_CHUNK = 500

_VERDICT_COLUMNS = (
    "sha256, format, status, model_type, family, variant, components, "
    "transformer_extractable, classifier, registry_fingerprint, reason, classified_at"
)


def _chunks(values: Sequence[Any]) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start:start + _CHUNK]


class ModelTypeRepository:

    def get_verdicts(self, shas: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        unique = sorted({sha for sha in shas if sha})
        if not unique:
            return {}
        from src.platform.database.database import db
        found: Dict[str, Dict[str, Any]] = {}
        with db.get_cursor() as cursor:
            for chunk in _chunks(unique):
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"SELECT {_VERDICT_COLUMNS} FROM model_header_verdicts WHERE sha256 IN ({placeholders})",
                    tuple(chunk),
                )
                for row in cursor.fetchall():
                    found[row["sha256"]] = dict(row)
        return found

    def settled_shas(self, shas: Iterable[str], fingerprint: str) -> Set[str]:
        return {
            sha
            for sha, row in self.get_verdicts(shas).items()
            if row["status"] != "undecided" or row["registry_fingerprint"] == fingerprint
        }

    def upsert_verdict(
        self,
        *,
        sha256: str,
        format: str,
        status: str,
        model_type: Optional[str],
        family: Optional[str],
        variant: Optional[str],
        components: Sequence[str],
        transformer_extractable: bool,
        classifier: Optional[str],
        registry_fingerprint: str,
        reason: Optional[str],
        classified_at: str,
    ) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT OR REPLACE INTO model_header_verdicts ({_VERDICT_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sha256, format, status, model_type, family, variant, json.dumps(list(components)),
                    int(transformer_extractable), classifier, registry_fingerprint, reason, classified_at,
                ),
            )

    def get_assertions(self, shas: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        unique = sorted({sha for sha in shas if sha})
        if not unique:
            return {}
        from src.platform.database.database import db
        found: Dict[str, Dict[str, Any]] = {}
        with db.get_cursor() as cursor:
            for chunk in _chunks(unique):
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    "SELECT sha256, model_type, source, set_by, set_at FROM model_type_assertions "
                    f"WHERE sha256 IN ({placeholders})",
                    tuple(chunk),
                )
                for row in cursor.fetchall():
                    found[row["sha256"]] = dict(row)
        return found

    def put_assertion(self, sha256: str, model_type: str, source: str, set_by: Optional[str], set_at: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT OR REPLACE INTO model_type_assertions (sha256, model_type, source, set_by, set_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sha256, model_type, source, set_by, set_at),
            )

    def delete_assertion(self, sha256: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM model_type_assertions WHERE sha256 = ?", (sha256,))
            return cursor.rowcount > 0

    def models_by_ids(self, model_ids: Iterable[str]) -> List[Dict[str, Any]]:
        unique = sorted({model_id for model_id in model_ids if model_id})
        if not unique:
            return []
        from src.platform.database.database import db
        rows: List[Dict[str, Any]] = []
        with db.get_cursor() as cursor:
            for chunk in _chunks(unique):
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    "SELECT id, filename, sha256, model_type, type_source, is_directory FROM models "
                    f"WHERE id IN ({placeholders})",
                    tuple(chunk),
                )
                rows.extend(dict(row) for row in cursor.fetchall())
        return rows

    def present_copies(self, model_ids: Iterable[str]) -> List[Dict[str, Any]]:
        unique = sorted({model_id for model_id in model_ids if model_id})
        if not unique:
            return []
        from src.platform.database.database import db
        rows: List[Dict[str, Any]] = []
        with db.get_cursor() as cursor:
            for chunk in _chunks(unique):
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"""
                    SELECT ml.model_id AS model_id, ml.root_id AS root_id, ml.model_type AS model_type,
                           ml.rel_path AS rel_path, mrb.scan_headers AS scan_headers
                    FROM model_locations ml
                    JOIN model_root_bindings mrb ON mrb.root_id = ml.root_id AND mrb.model_type = ml.model_type
                    WHERE ml.status = 'present' AND ml.model_id IN ({placeholders})
                    """,
                    tuple(chunk),
                )
                rows.extend(dict(row) for row in cursor.fetchall())
        return rows

    def identities_for_filenames(self, filenames: Iterable[str]) -> Dict[Tuple[str, str], str]:
        unique = sorted({name for name in filenames if name})
        if not unique:
            return {}
        from src.platform.database.database import db
        found: Dict[Tuple[str, str], str] = {}
        with db.get_cursor() as cursor:
            for chunk in _chunks(unique):
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"SELECT id, model_type, filename FROM models WHERE filename IN ({placeholders})",
                    tuple(chunk),
                )
                for row in cursor.fetchall():
                    found[(row["model_type"], row["filename"])] = row["id"]
        return found

    def apply_types(self, updates: Sequence[Tuple[str, str, str]]) -> None:
        if not updates:
            return
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.executemany(
                "UPDATE models SET model_type = ?, type_source = ? WHERE id = ?",
                [(model_type, source, model_id) for model_id, model_type, source in updates],
            )

    def count_needs_type(self) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM models WHERE model_type = 'undefined'")
            return cursor.fetchone()["n"]

    def count_header_classified(self) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM models WHERE type_source = 'header'")
            return cursor.fetchone()["n"]
