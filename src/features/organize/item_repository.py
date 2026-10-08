from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.form_refs import collect_model_ids
from src.platform.database.rows import dt_iso, json_column
from src.platform.filesystem.model_types import VIRTUAL_MODEL_TYPES

SUBJECT_TABLES = {
    "generation": ("generations", "g"),
    "upload": ("uploads", "u"),
    "model": ("models", "m"),
}

_CHUNK = 500


def _db():
    from src.platform.database.database import db
    return db


def _chunks(values: Sequence[str]):
    for start in range(0, len(values), _CHUNK):
        yield values[start:start + _CHUNK]


def model_stem(filename: Optional[str], model_type: Optional[str]) -> str:
    if not filename:
        return ""
    if model_type in VIRTUAL_MODEL_TYPES:
        return filename
    stem, _, _ = filename.rpartition(".")
    return stem or filename


def model_sources(model_type: Optional[str], providers: Iterable[str]) -> List[str]:
    found = sorted({str(p).lower() for p in providers if p})
    if model_type in VIRTUAL_MODEL_TYPES:
        return ["cloud", *[p for p in found if p != "cloud"]]
    return found or ["local"]


def effective_attributes(definitions: Sequence[ModelAttributeDefinition], model_type: Optional[str],
                         shared: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for definition in definitions:
        if not definition.applies_to(model_type or ""):
            continue
        value = overlay.get(definition.key) if definition.per_user else None
        if value is None:
            value = shared.get(definition.key)
        if value is None:
            value = definition.default_value
        if value is not None:
            values[definition.key] = value
    return values


def _visible_models_clause(user_id: str, is_admin: bool, restricted: bool) -> Tuple[str, List[Any]]:
    clauses = ["m.is_directory = 0"]
    params: List[Any] = []
    if not is_admin:
        clauses.append(
            "m.id IN (SELECT model_id FROM user_models WHERE user_id = ? UNION "
            "SELECT ugm2.model_id FROM user_group_models ugm2 JOIN user_group_members ugm "
            "ON ugm2.group_id = ugm.group_id WHERE ugm.user_id = ?)"
        )
        params.extend([user_id, user_id])
    if restricted:
        clauses.append("NOT EXISTS (SELECT 1 FROM providers p WHERE p.model_id = m.id AND p.nsfw = 1)")
    return " AND ".join(clauses), params


class OrganizeItemRepository:

    def base_clause(self, subject: str, user_id: str, is_admin: bool = False,
                    restricted: bool = False) -> Tuple[str, List[Any]]:
        if subject == "generation":
            return "g.user_id = ? AND g.status = 'completed'", [user_id]
        if subject == "upload":
            return "u.user_id = ? AND u.purpose = 'user_upload'", [user_id]
        return _visible_models_clause(user_id, is_admin, restricted)

    def candidate_ids(self, subject: str, user_id: str, where: str, params: Sequence[Any],
                      is_admin: bool = False, restricted: bool = False, limit: Optional[int] = None) -> List[str]:
        table, alias = SUBJECT_TABLES[subject]
        base, base_params = self.base_clause(subject, user_id, is_admin, restricted)
        order = f"{alias}.created_at DESC, {alias}.id DESC"
        sql_where = f"({base})" + (f" AND ({where})" if where else "")
        limit_sql = " LIMIT ?" if limit else ""
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"SELECT {alias}.id AS id FROM {table} {alias} CROSS JOIN (SELECT ? AS user_id) viewer "
                f"WHERE {sql_where} ORDER BY {order}{limit_sql}",
                (user_id, *base_params, *params, *([limit] if limit else [])),
            )
            return [row["id"] for row in cursor.fetchall()]

    def generations(self, user_id: str, generation_ids: Sequence[str]) -> List[Dict[str, Any]]:
        ids = list(dict.fromkeys(generation_ids))
        rows: Dict[str, Dict[str, Any]] = {}
        with _db().get_cursor() as cursor:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT id, user_id, preset_id, mode, backend_id, form_data, prompt_state, status, created_at, completed_at "
                    f"FROM generations WHERE user_id = ? AND id IN ({placeholders})",
                    (user_id, *chunk),
                )
                for row in cursor.fetchall():
                    form_data = json_column(row["form_data"], {})
                    rows[row["id"]] = {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "preset_id": row["preset_id"],
                        "mode": row["mode"],
                        "backend_id": row["backend_id"],
                        "status": row["status"],
                        "form_data": form_data,
                        "prompt_state": json_column(row["prompt_state"], {}),
                        "model_ids": list(collect_model_ids(form_data)),
                        "files": [],
                        "tags": [],
                        "created_at": dt_iso(row["created_at"]),
                        "completed_at": dt_iso(row["completed_at"]),
                    }
                found = [gid for gid in chunk if gid in rows]
                if not found:
                    continue
                placeholders = ",".join("?" * len(found))
                cursor.execute(
                    f"SELECT generation_id, model_id FROM generation_models WHERE generation_id IN ({placeholders})",
                    found,
                )
                for row in cursor.fetchall():
                    model_ids = rows[row["generation_id"]]["model_ids"]
                    if row["model_id"] not in model_ids:
                        model_ids.append(row["model_id"])
                cursor.execute(
                    f"SELECT gf.generation_id, f.file_type, f.width, f.height, f.duration_seconds, f.mime_type, "
                    f"f.is_final FROM generation_files gf JOIN files f ON f.id = gf.file_id "
                    f"WHERE gf.generation_id IN ({placeholders}) AND f.is_derived = 0 ORDER BY f.created_at, f.id",
                    found,
                )
                for row in cursor.fetchall():
                    rows[row["generation_id"]]["files"].append({
                        "file_type": (row["file_type"] or "").lower(),
                        "width": row["width"],
                        "height": row["height"],
                        "duration_seconds": row["duration_seconds"],
                        "mime_type": row["mime_type"],
                        "is_final": bool(row["is_final"]),
                    })
                cursor.execute(
                    f"SELECT gt.generation_id, t.name FROM generation_tags gt JOIN tags t ON t.id = gt.tag_id "
                    f"WHERE gt.generation_id IN ({placeholders}) ORDER BY t.name",
                    found,
                )
                for row in cursor.fetchall():
                    rows[row["generation_id"]]["tags"].append(row["name"])
        return [rows[gid] for gid in ids if gid in rows]

    def uploads(self, user_id: str, upload_ids: Sequence[str]) -> List[Dict[str, Any]]:
        ids = list(dict.fromkeys(upload_ids))
        rows: Dict[str, Dict[str, Any]] = {}
        with _db().get_cursor() as cursor:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT id, user_id, media_type, mime_type, width, height, duration_seconds, original_filename, "
                    f"purpose, created_at FROM uploads WHERE user_id = ? AND purpose = 'user_upload' AND id IN ({placeholders})",
                    (user_id, *chunk),
                )
                for row in cursor.fetchall():
                    rows[row["id"]] = {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "media_type": (row["media_type"] or "").lower(),
                        "mime_type": row["mime_type"],
                        "width": row["width"],
                        "height": row["height"],
                        "duration_seconds": row["duration_seconds"],
                        "original_filename": row["original_filename"] or "",
                        "tags": [],
                        "created_at": dt_iso(row["created_at"]),
                    }
                found = [uid for uid in chunk if uid in rows]
                if not found:
                    continue
                placeholders = ",".join("?" * len(found))
                cursor.execute(
                    f"SELECT ut.upload_id, t.name FROM upload_tags ut JOIN tags t ON t.id = ut.tag_id "
                    f"WHERE ut.upload_id IN ({placeholders}) ORDER BY t.name",
                    found,
                )
                for row in cursor.fetchall():
                    rows[row["upload_id"]]["tags"].append(row["name"])
        return [rows[uid] for uid in ids if uid in rows]

    def models(self, user_id: str, model_ids: Sequence[str], is_admin: bool, restricted: bool) -> List[Dict[str, Any]]:
        ids = list(dict.fromkeys(model_ids))
        rows: Dict[str, Dict[str, Any]] = {}
        visible, visible_params = _visible_models_clause(user_id, is_admin, restricted)
        definitions = self.attribute_definitions()
        per_user_keys = {d.key for d in definitions if d.per_user}
        with _db().get_cursor() as cursor:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT m.id, m.filename, m.model_type, m.sha256, m.file_size, m.description, m.model_metadata, "
                    f"m.created_at, v.family, umm.custom_name "
                    f"FROM models m LEFT JOIN model_header_verdicts v ON v.sha256 = m.sha256 "
                    f"LEFT JOIN user_model_meta umm ON umm.model_id = m.id AND umm.user_id = ? "
                    f"WHERE {visible} AND m.id IN ({placeholders})",
                    (user_id, *visible_params, *chunk),
                )
                for row in cursor.fetchall():
                    rows[row["id"]] = {
                        "id": row["id"],
                        "user_id": user_id,
                        "filename": row["filename"],
                        "model_type": row["model_type"],
                        "family": (row["family"] or "").lower() or None,
                        "sha256": row["sha256"],
                        "file_size": row["file_size"],
                        "custom_name": row["custom_name"],
                        "stem": model_stem(row["filename"], row["model_type"]),
                        "description": row["description"],
                        "model_metadata": json_column(row["model_metadata"], {}),
                        "provider_names": [],
                        "provider_descriptions": [],
                        "providers": [],
                        "tags": [],
                        "created_at": dt_iso(row["created_at"]),
                    }
                found = [mid for mid in chunk if mid in rows]
                if not found:
                    continue
                placeholders = ",".join("?" * len(found))
                cursor.execute(
                    f"SELECT mt.model_id, t.name FROM model_tags mt JOIN tags t ON t.id = mt.tag_id "
                    f"WHERE mt.model_id IN ({placeholders}) ORDER BY t.name",
                    found,
                )
                for row in cursor.fetchall():
                    rows[row["model_id"]]["tags"].append(row["name"])
                cursor.execute(
                    f"SELECT model_id, provider, name, description FROM providers WHERE model_id IN ({placeholders}) "
                    f"ORDER BY created_at, id",
                    found,
                )
                for row in cursor.fetchall():
                    entry = rows[row["model_id"]]
                    entry["providers"].append(row["provider"])
                    if row["name"]:
                        entry["provider_names"].append(row["name"])
                    if row["description"]:
                        entry["provider_descriptions"].append(row["description"])
                overlays: Dict[str, Dict[str, Any]] = {}
                if per_user_keys:
                    cursor.execute(
                        f"SELECT model_id, key, value FROM user_model_attributes "
                        f"WHERE user_id = ? AND model_id IN ({placeholders})",
                        (user_id, *found),
                    )
                    for row in cursor.fetchall():
                        overlays.setdefault(row["model_id"], {})[row["key"]] = json_column(row["value"], None)
                for model_id in found:
                    entry = rows[model_id]
                    entry["sources"] = model_sources(entry["model_type"], entry["providers"])
                    shared = entry["model_metadata"] if isinstance(entry["model_metadata"], dict) else {}
                    entry["attributes"] = effective_attributes(
                        definitions, entry["model_type"], shared, overlays.get(model_id, {})
                    )
        return [rows[mid] for mid in ids if mid in rows]

    def attribute_definitions(self) -> List[ModelAttributeDefinition]:
        return AttributeDefinitionRepository().list_all()

    def attribute_definition(self, key: str) -> Optional[ModelAttributeDefinition]:
        return AttributeDefinitionRepository().get_by_key(key)

    def provider_ids(self) -> List[str]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT LOWER(provider) AS provider FROM providers WHERE provider IS NOT NULL AND provider != '' "
                "ORDER BY provider"
            )
            return [row["provider"] for row in cursor.fetchall()]

    def is_admin(self, user_id: str) -> bool:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT account_type FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
        return bool(row) and str(row["account_type"] or "").upper() == "ADMIN"

    def generation_owner(self, generation_id: str) -> Optional[str]:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT user_id FROM generations WHERE id = ?", (generation_id,))
            row = cursor.fetchone()
        return row["user_id"] if row else None

    def upload_owner(self, upload_id: str) -> Optional[str]:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT user_id FROM uploads WHERE id = ?", (upload_id,))
            row = cursor.fetchone()
        return row["user_id"] if row else None

    def model_created_at(self, model_id: str) -> Optional[str]:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT created_at FROM models WHERE id = ?", (model_id,))
            row = cursor.fetchone()
        return dt_iso(row["created_at"]) if row else None

    def models_created_since(self, since_iso: str) -> List[str]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT id FROM models WHERE is_directory = 0 AND datetime(created_at) >= datetime(?) ORDER BY created_at, id",
                (since_iso,),
            )
            return [row["id"] for row in cursor.fetchall()]

    def existing_model_ids(self, model_ids: Iterable[str]) -> Set[str]:
        ids = list(dict.fromkeys(model_ids))
        found: Set[str] = set()
        with _db().get_cursor() as cursor:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(f"SELECT id FROM models WHERE id IN ({placeholders})", chunk)
                found.update(row["id"] for row in cursor.fetchall())
        return found

    def model_names(self, model_ids: Iterable[str]) -> Dict[str, str]:
        ids = list(dict.fromkeys(model_ids))
        names: Dict[str, str] = {}
        with _db().get_cursor() as cursor:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(f"SELECT id, filename FROM models WHERE id IN ({placeholders})", chunk)
                names.update({row["id"]: row["filename"] for row in cursor.fetchall()})
        return names

    def visible_model_ids(self, user_id: str, model_ids: Iterable[str], is_admin: bool, restricted: bool) -> Set[str]:
        ids = list(dict.fromkeys(model_ids))
        visible, visible_params = _visible_models_clause(user_id, is_admin, restricted)
        found: Set[str] = set()
        with _db().get_cursor() as cursor:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT m.id FROM models m WHERE {visible} AND m.id IN ({placeholders})",
                    (*visible_params, *chunk),
                )
                found.update(row["id"] for row in cursor.fetchall())
        return found

    def nsfw_model_ids(self, model_ids: Iterable[str]) -> Set[str]:
        ids = list(dict.fromkeys(model_ids))
        found: Set[str] = set()
        with _db().get_cursor() as cursor:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT DISTINCT model_id FROM providers WHERE nsfw = 1 AND model_id IN ({placeholders})", chunk
                )
                found.update(row["model_id"] for row in cursor.fetchall())
        return found

    def used_modes(self, user_id: str) -> List[str]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT mode FROM generations WHERE user_id = ? AND mode IS NOT NULL ORDER BY mode", (user_id,)
            )
            return [row["mode"] for row in cursor.fetchall()]

    def model_types(self) -> List[str]:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT DISTINCT model_type FROM models WHERE is_directory = 0 ORDER BY model_type")
            return [row["model_type"] for row in cursor.fetchall()]

    def model_families(self) -> List[str]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT LOWER(v.family) AS family FROM model_header_verdicts v JOIN models m ON m.sha256 = v.sha256 "
                "WHERE v.family IS NOT NULL AND v.family != '' ORDER BY family"
            )
            return [row["family"] for row in cursor.fetchall()]

    def tag_names(self, tag_type: str, user_id: Optional[str], query: str, limit: int) -> List[str]:
        with _db().get_cursor() as cursor:
            if user_id is None:
                cursor.execute(
                    "SELECT name FROM tags WHERE type = ? AND instr(LOWER(name), LOWER(?)) > 0 ORDER BY name LIMIT ?",
                    (tag_type, query, limit),
                )
            else:
                cursor.execute(
                    "SELECT name FROM tags WHERE type = ? AND user_id = ? AND instr(LOWER(name), LOWER(?)) > 0 "
                    "ORDER BY name LIMIT ?",
                    (tag_type, user_id, query, limit),
                )
            return [row["name"] for row in cursor.fetchall()]

    def members_of(self, scope: str, collection_id: str, item_ids: Sequence[str]) -> Set[str]:
        ids = list(dict.fromkeys(item_ids))
        found: Set[str] = set()
        with _db().get_cursor() as cursor:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                if scope == "history":
                    cursor.execute(
                        f"SELECT generation_id AS item_id FROM collection_generations "
                        f"WHERE collection_id = ? AND generation_id IN ({placeholders})",
                        (collection_id, *chunk),
                    )
                elif scope == "library":
                    cursor.execute(
                        f"SELECT upload_id AS item_id FROM collection_uploads "
                        f"WHERE collection_id = ? AND upload_id IN ({placeholders})",
                        (collection_id, *chunk),
                    )
                else:
                    cursor.execute(
                        f"SELECT model_id AS item_id FROM model_collection_members "
                        f"WHERE collection_id = ? AND model_id IN ({placeholders})",
                        (collection_id, *chunk),
                    )
                found.update(row["item_id"] for row in cursor.fetchall())
        return found

    def items_with_all_tags(self, subject: str, item_ids: Sequence[str], names: Sequence[str]) -> Set[str]:
        ids = list(dict.fromkeys(item_ids))
        lowered = sorted({name.lower() for name in names})
        if not lowered:
            return set(ids)
        found: Set[str] = set()
        link, column = ("generation_tags", "generation_id") if subject == "generation" else ("upload_tags", "upload_id")
        name_placeholders = ",".join("?" * len(lowered))
        with _db().get_cursor() as cursor:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT l.{column} AS item_id FROM {link} l JOIN tags t ON t.id = l.tag_id "
                    f"WHERE l.{column} IN ({placeholders}) AND LOWER(t.name) IN ({name_placeholders}) "
                    f"GROUP BY l.{column} HAVING COUNT(DISTINCT LOWER(t.name)) = ?",
                    (*chunk, *lowered, len(lowered)),
                )
                found.update(row["item_id"] for row in cursor.fetchall())
        return found
