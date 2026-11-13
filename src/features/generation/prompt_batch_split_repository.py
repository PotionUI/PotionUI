import json
import logging
from typing import Any, Dict, List

from src.features.generation.history_revision_repository import bump_history_revision
from src.features.generation.prompt_batch import PromptBatch, prompt_state_for
from src.platform.util.ids import generate_ulid
from src.platform.database import get_cursor

logger = logging.getLogger(__name__)

_COPIED_LINKS = (
    ("generation_models", "model_id"),
    ("generation_sources", "field_name, source_generation_id, source_file_index"),
)
_KEYED_LINKS = ("generation_tags", "collection_generations")


class PromptBatchSplitRepository:
    def split(self, generation_id: str, batch: PromptBatch) -> List[str]:
        later = sorted({index for index in batch.files.values() if 0 < index < batch.count})
        if not later or batch.base_seed is None:
            return []

        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM generations WHERE id = ?", (generation_id,))
            row = cursor.fetchone()
            if row is None:
                return []
            source = dict(row)

            cursor.execute(
                "SELECT f.id, f.file_path FROM files f JOIN generation_files gf ON gf.file_id = f.id "
                "WHERE gf.generation_id = ?",
                (generation_id,),
            )
            file_ids = {entry["file_path"]: entry["id"] for entry in cursor.fetchall()}

            owned = {path: index for path, index in batch.files.items() if path in file_ids}
            later = [index for index in later if any(owner == index for owner in owned.values())]
            if not later:
                return []

            new_ids = {index: generate_ulid() for index in later}
            self._rewrite_origin(cursor, generation_id, source, batch)
            for index, new_id in new_ids.items():
                self._insert_row(cursor, source, new_id, index, batch)
                moved = [file_ids[path] for path, owner in owned.items() if owner == index]
                self._move_files(cursor, generation_id, new_id, moved)
                self._copy_links(cursor, generation_id, new_id)
            self._split_segments(cursor, generation_id, new_ids)
            self._split_parameters(cursor, generation_id, new_ids, batch.per_prompt)
            self._split_applied_prompts(cursor, generation_id, new_ids, batch.per_prompt)
            bump_history_revision(cursor, source["user_id"])
        return list(new_ids.values())

    @staticmethod
    def _form_for(source: Dict[str, Any], index: int, batch: PromptBatch) -> str:
        form = json.loads(source["form_data"]) if source.get("form_data") else {}
        form["quantity"] = batch.per_prompt
        form["seed"] = batch.seed_for(index)
        return json.dumps(form)

    @staticmethod
    def _state_for(source: Dict[str, Any], index: int, batch: PromptBatch):
        state = json.loads(source["prompt_state"]) if source.get("prompt_state") else None
        state = prompt_state_for(state, index, batch.count)
        return json.dumps(state) if state is not None else None

    def _rewrite_origin(self, cursor, generation_id: str, source: Dict[str, Any], batch: PromptBatch) -> None:
        cursor.execute(
            "UPDATE generations SET form_data = ?, prompt_state = ?, batch_id = ? WHERE id = ?",
            (self._form_for(source, 0, batch), self._state_for(source, 0, batch), generation_id, generation_id),
        )

    def _insert_row(self, cursor, source: Dict[str, Any], new_id: str, index: int, batch: PromptBatch) -> None:
        values = dict(source)
        values.update(
            id=new_id,
            batch_id=source["id"],
            form_data=self._form_for(source, index, batch),
            prompt_state=self._state_for(source, index, batch),
            idempotency_key=None,
            idempotency_fingerprint=None,
            rating=0,
            is_favorite=0,
        )
        columns = list(values)
        cursor.execute(
            f"INSERT INTO generations ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            [values[column] for column in columns],
        )

    @staticmethod
    def _move_files(cursor, generation_id: str, new_id: str, file_ids: List[str]) -> None:
        for file_id in file_ids:
            cursor.execute(
                "UPDATE generation_files SET generation_id = ? WHERE generation_id = ? AND file_id = ?",
                (new_id, generation_id, file_id),
            )
            cursor.execute(
                "UPDATE media_system_tags SET generation_id = ? WHERE generation_id = ? AND file_id = ?",
                (new_id, generation_id, file_id),
            )

    @staticmethod
    def _copy_links(cursor, generation_id: str, new_id: str) -> None:
        for table, columns in _COPIED_LINKS:
            cursor.execute(f"SELECT {columns} FROM {table} WHERE generation_id = ?", (generation_id,))
            for link in cursor.fetchall():
                names = list(link.keys())
                cursor.execute(
                    f"INSERT INTO {table} (id, generation_id, {', '.join(names)}) "
                    f"VALUES (?, ?, {', '.join('?' * len(names))})",
                    [generate_ulid(), new_id, *[link[name] for name in names]],
                )
        for table in _KEYED_LINKS:
            other = "tag_id" if table == "generation_tags" else "collection_id"
            cursor.execute(f"SELECT {other} FROM {table} WHERE generation_id = ?", (generation_id,))
            for link in cursor.fetchall():
                cursor.execute(
                    f"INSERT OR IGNORE INTO {table} (generation_id, {other}) VALUES (?, ?)",
                    (new_id, link[other]),
                )

    @staticmethod
    def _split_segments(cursor, generation_id: str, new_ids: Dict[int, str]) -> None:
        for index, new_id in new_ids.items():
            cursor.execute(
                "SELECT id FROM generation_segments WHERE generation_id = ? AND prompt_index = ?",
                (generation_id, index),
            )
            for segment in cursor.fetchall():
                cursor.execute(
                    "UPDATE generation_segments SET generation_id = ?, prompt_index = 0 WHERE id = ?",
                    (new_id, segment["id"]),
                )
                cursor.execute(
                    "UPDATE generation_segment_phrasebook SET generation_id = ? WHERE segment_id = ?",
                    (new_id, segment["id"]),
                )

    @staticmethod
    def _split_applied_prompts(cursor, generation_id: str, new_ids: Dict[int, str], per_prompt: int) -> None:
        cursor.execute(
            "SELECT applied, created_at FROM generation_applied_prompts WHERE generation_id = ?", (generation_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return
        try:
            entries = json.loads(row["applied"])
        except (TypeError, ValueError):
            return
        if not isinstance(entries, list):
            return
        cursor.execute(
            "UPDATE generation_applied_prompts SET applied = ? WHERE generation_id = ?",
            (json.dumps(entries[:per_prompt]), generation_id),
        )
        for index, new_id in new_ids.items():
            cursor.execute(
                "INSERT OR REPLACE INTO generation_applied_prompts (generation_id, applied, created_at) VALUES (?, ?, ?)",
                (new_id, json.dumps(entries[index * per_prompt:(index + 1) * per_prompt]), row["created_at"]),
            )

    @staticmethod
    def _split_parameters(cursor, generation_id: str, new_ids: Dict[int, str], per_prompt: int) -> None:
        cursor.execute(
            "SELECT id, parameter_name, parameter_value, parameter_index, meta, position FROM generation_parameters "
            "WHERE generation_id = ?",
            (generation_id,),
        )
        rows = cursor.fetchall()
        per_name: Dict[str, int] = {}
        for entry in rows:
            per_name[entry["parameter_name"]] = per_name.get(entry["parameter_name"], 0) + 1
        for entry in rows:
            if per_name[entry["parameter_name"]] == 1:
                for new_id in new_ids.values():
                    cursor.execute(
                        "INSERT INTO generation_parameters (id, generation_id, parameter_name, parameter_value, "
                        "parameter_index, meta, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (generate_ulid(), new_id, entry["parameter_name"], entry["parameter_value"],
                         entry["parameter_index"], entry["meta"], entry["position"]),
                    )
                continue
            owner = entry["parameter_index"] // per_prompt
            if owner in new_ids:
                cursor.execute(
                    "UPDATE generation_parameters SET generation_id = ?, parameter_index = ? WHERE id = ?",
                    (new_ids[owner], entry["parameter_index"] % per_prompt, entry["id"]),
                )
