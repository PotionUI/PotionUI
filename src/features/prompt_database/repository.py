"""Transactional persistence for normalized prompt aggregates."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.features.segments.dto import RichSegment
from src.platform.database.rows import dt_column
from src.features.prompt_database.records import Prompt
from src.platform.util.ids import generate_ulid


_CHIP_MARKER = re.compile(r"#\[([^\]]+)\]|#([\w][\w.]*)")

_SQLITE_IN_CHUNK_SIZE = 500


def resolve_rich_segment_text(segment: RichSegment) -> str:
    """Resolve chip markers using their complete, persisted editor state."""
    if segment.type == "break":
        return "BREAK"
    if not segment.content or not segment.chips:
        return segment.content

    by_path: Dict[str, List[str]] = {}
    for chip in segment.chips.values():
        if hasattr(chip, "model_dump"):
            chip = chip.model_dump()
        if not isinstance(chip, dict):
            continue
        path = chip.get("categoryPath") or chip.get("category_path")
        if path:
            by_path.setdefault(str(path), []).append(str(chip.get("value", "")))
    offsets: Dict[str, int] = {}

    def replace(match: re.Match) -> str:
        path = match.group(1) or match.group(2)
        values = by_path.get(path)
        index = offsets.get(path, 0)
        if not values or index >= len(values):
            return match.group(0)
        offsets[path] = index + 1
        return values[index]

    return _CHIP_MARKER.sub(replace, segment.content)


def flatten_segments(segments: Sequence[RichSegment]) -> str:
    """Match the generation editor's enabled-content and BREAK joining rules."""
    result = ""
    previous_was_break = False
    for segment in segments:
        if not segment.enabled:
            continue
        if segment.type == "break":
            result += " BREAK" if result else "BREAK"
            previous_was_break = True
            continue
        text = resolve_rich_segment_text(segment).strip()
        if not text:
            continue
        text = (segment.prefix or "") + text + (segment.suffix or "")
        if result:
            result += " " if previous_was_break else ", "
        result += text
        previous_was_break = False
    return result.strip()


class PromptRepository:
    parent_columns = (
        "name", "usage_hint", "source_group_id", "source_provider", "source_id",
        "source_url", "model_id", "model_name", "base_model", "cfg_scale", "steps",
        "sampler", "width", "height", "heart_count", "like_count", "laugh_count",
        "cry_count", "comment_count", "tags", "nsfw", "metadata", "variables",
    )

    def _segments_for(self, cursor, prompt_id: str) -> List[RichSegment]:
        cursor.execute(
            "SELECT * FROM prompt_segments WHERE prompt_id = ? ORDER BY position ASC",
            (prompt_id,),
        )
        return [
            RichSegment(
                id=row["id"],
                type=row["type"],
                content=row["content"] or "",
                chips=json.loads(row["chips"] or "{}"),
                resources=json.loads(row["resources"] or "{}"),
                enabled=bool(row["is_enabled"]),
                name=row["name"],
                color=row["color"],
                description=row["description"],
                prefix=row["prefix"],
                suffix=row["suffix"],
            )
            for row in cursor.fetchall()
        ]

    def _prompt_from_row(self, row, segments: List[RichSegment]) -> Prompt:
        return Prompt(
            id=row["id"], user_id=row["user_id"], name=row["name"],
            flattened_text=row["flattened_text"] or "", usage_hint=row["usage_hint"],
            source_group_id=row["source_group_id"], source_provider=row["source_provider"],
            source_id=row["source_id"], source_url=row["source_url"], model_id=row["model_id"],
            model_name=row["model_name"], base_model=row["base_model"], cfg_scale=row["cfg_scale"],
            steps=row["steps"], sampler=row["sampler"], width=row["width"], height=row["height"],
            heart_count=row["heart_count"] or 0, like_count=row["like_count"] or 0,
            laugh_count=row["laugh_count"] or 0, cry_count=row["cry_count"] or 0,
            comment_count=row["comment_count"] or 0, tags=json.loads(row["tags"] or "[]"),
            nsfw=bool(row["nsfw"]), metadata=json.loads(row["metadata"] or "{}"),
            variables=json.loads(row["variables"]) if row["variables"] else None,
            embedded=bool(row["embedded"]), created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]), segments=segments,
        )

    def _from_row(self, cursor, row) -> Prompt:
        return self._prompt_from_row(row, self._segments_for(cursor, row["id"]))

    def _segments_bulk(self, cursor, prompt_ids: Sequence[str]) -> Dict[str, List[RichSegment]]:
        result: Dict[str, List[RichSegment]] = {prompt_id: [] for prompt_id in prompt_ids}
        for start in range(0, len(prompt_ids), _SQLITE_IN_CHUNK_SIZE):
            chunk = prompt_ids[start:start + _SQLITE_IN_CHUNK_SIZE]
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(
                f"SELECT * FROM prompt_segments WHERE prompt_id IN ({placeholders}) "
                f"ORDER BY prompt_id ASC, position ASC",
                chunk,
            )
            for row in cursor.fetchall():
                result[row["prompt_id"]].append(RichSegment(
                    id=row["id"], type=row["type"], content=row["content"] or "",
                    chips=json.loads(row["chips"] or "{}"), resources=json.loads(row["resources"] or "{}"),
                    enabled=bool(row["is_enabled"]),
                    name=row["name"], color=row["color"], description=row["description"],
                    prefix=row["prefix"], suffix=row["suffix"],
                ))
        return result

    def _insert_segments(self, cursor, prompt_id: str, segments: Sequence[RichSegment]) -> None:
        if not segments:
            raise ValueError("a prompt must contain at least one segment")
        for position, segment in enumerate(segments):
            cursor.execute(
                """INSERT INTO prompt_segments
                   (id, prompt_id, position, type, content, chips, resources, is_enabled, name, color,
                    description, prefix, suffix)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    generate_ulid(), prompt_id, position, segment.type, segment.content,
                    json.dumps(segment.model_dump()["chips"]), json.dumps(segment.model_dump()["resources"]),
                    int(segment.enabled), segment.name,
                    segment.color, segment.description, segment.prefix, segment.suffix,
                ),
            )

    def create(self, prompt: Prompt) -> Prompt:
        if not prompt.segments:
            raise ValueError("a prompt must contain at least one segment")
        prompt.id = prompt.id or generate_ulid()
        prompt.flattened_text = flatten_segments(prompt.segments)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """INSERT INTO prompts (
                    id, user_id, name, flattened_text, usage_hint, source_group_id,
                    source_provider, source_id, source_url, model_id, model_name, base_model,
                    cfg_scale, steps, sampler, width, height, heart_count, like_count,
                    laugh_count, cry_count, comment_count, tags, nsfw, metadata, variables, embedded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    prompt.id, prompt.user_id, prompt.name, prompt.flattened_text, prompt.usage_hint,
                    prompt.source_group_id, prompt.source_provider, prompt.source_id, prompt.source_url,
                    prompt.model_id, prompt.model_name, prompt.base_model, prompt.cfg_scale, prompt.steps,
                    prompt.sampler, prompt.width, prompt.height, prompt.heart_count, prompt.like_count,
                    prompt.laugh_count, prompt.cry_count, prompt.comment_count, json.dumps(prompt.tags),
                    int(prompt.nsfw), json.dumps(prompt.metadata),
                    json.dumps(prompt.variables) if prompt.variables is not None else None,
                    int(prompt.embedded),
                ),
            )
            self._insert_segments(cursor, prompt.id, prompt.segments)
            cursor.execute("SELECT * FROM prompts WHERE id = ?", (prompt.id,))
            return self._from_row(cursor, cursor.fetchone())

    def get_by_id(self, prompt_id: str, user_id: str) -> Optional[Prompt]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM prompts WHERE id = ? AND user_id = ?", (prompt_id, user_id))
            row = cursor.fetchone()
            return self._from_row(cursor, row) if row else None

    def get_by_ids(self, ids: Sequence[str], user_id: str) -> List[Prompt]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM prompts WHERE id IN ({placeholders}) AND user_id = ?",
                (*ids, user_id),
            )
            rows = cursor.fetchall()
            prompt_ids = [row["id"] for row in rows]
            segments_by_prompt = self._segments_bulk(cursor, prompt_ids)
            values = {row["id"]: self._prompt_from_row(row, segments_by_prompt[row["id"]]) for row in rows}
        return [values[prompt_id] for prompt_id in ids if prompt_id in values]

    def update(self, prompt_id: str, user_id: str, prompt: Prompt) -> Optional[Prompt]:
        if not prompt.segments:
            raise ValueError("a prompt must contain at least one segment")
        flattened = flatten_segments(prompt.segments)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """UPDATE prompts SET name=?, flattened_text=?, usage_hint=?, source_group_id=?,
                   source_provider=?, source_id=?, source_url=?, model_id=?, model_name=?, base_model=?,
                   cfg_scale=?, steps=?, sampler=?, width=?, height=?, heart_count=?, like_count=?,
                   laugh_count=?, cry_count=?, comment_count=?, tags=?, nsfw=?, metadata=?, variables=?,
                   embedded=0, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?""",
                (
                    prompt.name, flattened, prompt.usage_hint, prompt.source_group_id,
                    prompt.source_provider, prompt.source_id, prompt.source_url, prompt.model_id,
                    prompt.model_name, prompt.base_model, prompt.cfg_scale, prompt.steps, prompt.sampler,
                    prompt.width, prompt.height, prompt.heart_count, prompt.like_count, prompt.laugh_count,
                    prompt.cry_count, prompt.comment_count, json.dumps(prompt.tags), int(prompt.nsfw),
                    json.dumps(prompt.metadata),
                    json.dumps(prompt.variables) if prompt.variables is not None else None,
                    prompt_id, user_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            cursor.execute("DELETE FROM prompt_segments WHERE prompt_id = ?", (prompt_id,))
            self._insert_segments(cursor, prompt_id, prompt.segments)
            cursor.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,))
            return self._from_row(cursor, cursor.fetchone())

    _USAGE_JOIN_SQL = (
        "LEFT JOIN (SELECT source_prompt_id, COUNT(*) AS usage_count, "
        "MAX(created_at) AS last_used_at FROM generations WHERE user_id = ? "
        "GROUP BY source_prompt_id) usage ON usage.source_prompt_id = prompts.id"
    )

    def _list_filters(
        self, user_id: str, source_provider: Optional[str], base_model: Optional[str],
        model_id: Optional[str], usage_hint: Optional[str], collection_id: Optional[str],
        q: Optional[str], tags: Optional[Sequence[str]], used: str,
        used_after: Optional[str], has_variables: Optional[bool], nsfw: str,
    ) -> Tuple[List[str], List[Any], bool]:
        clauses, params = ["prompts.user_id = ?"], [user_id]
        for column, value in (
            ("source_provider", source_provider), ("base_model", base_model),
            ("model_id", model_id), ("usage_hint", usage_hint),
        ):
            if value is not None:
                clauses.append(f"prompts.{column} = ?")
                params.append(value)
        if collection_id:
            clauses.append(
                "prompts.id IN (SELECT prompt_id FROM collection_prompts WHERE collection_id = ?)"
            )
            params.append(collection_id)
        if q:
            clauses.append("(prompts.name LIKE ? OR prompts.flattened_text LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        if tags:
            placeholders = ",".join("?" for _ in tags)
            clauses.append(
                f"EXISTS (SELECT 1 FROM json_each(prompts.tags) WHERE json_each.value IN ({placeholders}))"
            )
            params.extend(tags)
        if has_variables is True:
            clauses.append("prompts.variables IS NOT NULL")
        elif has_variables is False:
            clauses.append("prompts.variables IS NULL")
        if nsfw != "include":
            clauses.append("prompts.nsfw = 0")
        needs_usage_join = used in ("used", "never") or bool(used_after)
        if used == "used":
            clauses.append("usage.usage_count > 0")
        elif used == "never":
            clauses.append("usage.usage_count IS NULL")
        if used_after:
            clauses.append("datetime(usage.last_used_at) >= datetime(?)")
            params.append(used_after)
        return clauses, params, needs_usage_join

    def get_all(
        self, user_id: str, limit: int = 20, offset: int = 0,
        source_provider: Optional[str] = None, base_model: Optional[str] = None,
        model_id: Optional[str] = None, usage_hint: Optional[str] = None,
        collection_id: Optional[str] = None,
        q: Optional[str] = None, tags: Optional[Sequence[str]] = None,
        used: str = "any", used_after: Optional[str] = None,
        has_variables: Optional[bool] = None, nsfw: str = "exclude",
        sort_by: str = "created_at", sort_order: str = "desc",
    ) -> List[Prompt]:
        clauses, params, needs_usage_join = self._list_filters(
            user_id, source_provider, base_model, model_id, usage_hint, collection_id,
            q, tags, used, used_after, has_variables, nsfw,
        )
        sort_columns = {
            "created_at": "prompts.created_at", "updated_at": "prompts.updated_at",
            "most_hearts": "prompts.heart_count", "heart_count": "prompts.heart_count",
            "like_count": "prompts.like_count", "name": "COALESCE(prompts.name, prompts.flattened_text)",
            "usage_count": "COALESCE(usage.usage_count, 0)", "last_used_at": "usage.last_used_at",
        }
        needs_usage_join = needs_usage_join or sort_by in ("usage_count", "last_used_at")
        order = "DESC" if sort_order.lower() == "desc" else "ASC"
        join_sql = self._USAGE_JOIN_SQL if needs_usage_join else ""
        join_params = [user_id] if needs_usage_join else []
        query = (
            f"SELECT prompts.* FROM prompts {join_sql} WHERE {' AND '.join(clauses)} "
            f"ORDER BY {sort_columns.get(sort_by, 'prompts.created_at')} {order} LIMIT ? OFFSET ?"
        )
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, (*join_params, *params, limit, offset))
            rows = cursor.fetchall()
            prompt_ids = [row["id"] for row in rows]
            segments_by_prompt = self._segments_bulk(cursor, prompt_ids)
            return [self._prompt_from_row(row, segments_by_prompt[row["id"]]) for row in rows]

    def tag_counts(self, user_id: str, nsfw: str = "exclude") -> List[Tuple[str, int]]:
        clauses = ["prompts.user_id = ?"]
        params: List[Any] = [user_id]
        if nsfw != "include":
            clauses.append("prompts.nsfw = 0")
        query = (
            "SELECT je.value AS tag, COUNT(*) AS count FROM prompts, "
            f"json_each(prompts.tags) je WHERE {' AND '.join(clauses)} "
            "GROUP BY je.value ORDER BY count DESC, tag ASC"
        )
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [(row["tag"], row["count"]) for row in cursor.fetchall()]

    def get_ids_and_text(
        self, user_id: str, limit: int = 5000, model_id: Optional[str] = None,
    ) -> List[Tuple[str, str]]:
        clauses, params = ["user_id = ?"], [user_id]
        if model_id is not None:
            clauses.append("model_id = ?")
            params.append(model_id)
        query = f"SELECT id, flattened_text FROM prompts WHERE {' AND '.join(clauses)} LIMIT ?"
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, (*params, limit))
            return [(row["id"], row["flattened_text"] or "") for row in cursor.fetchall()]

    def text_search(
        self, user_id: str, query: str, limit: int = 20,
        base_model: Optional[str] = None, model_id: Optional[str] = None,
        source_provider: Optional[str] = None,
    ) -> List[Prompt]:
        clauses = ["user_id = ?", "(flattened_text LIKE ? OR name LIKE ? OR tags LIKE ?)"]
        like = f"%{query}%"
        params: List[Any] = [user_id, like, like, like]
        for column, value in (("base_model", base_model), ("model_id", model_id), ("source_provider", source_provider)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM prompts WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
                (*params, limit),
            )
            rows = cursor.fetchall()
            prompt_ids = [row["id"] for row in rows]
            segments_by_prompt = self._segments_bulk(cursor, prompt_ids)
            return [self._prompt_from_row(row, segments_by_prompt[row["id"]]) for row in rows]

    def count(
        self, user_id: str, source_provider=None, model_id=None,
        base_model=None, usage_hint=None, collection_id=None,
        q: Optional[str] = None, tags: Optional[Sequence[str]] = None,
        used: str = "any", used_after: Optional[str] = None,
        has_variables: Optional[bool] = None, nsfw: str = "exclude",
    ) -> int:
        clauses, params, needs_usage_join = self._list_filters(
            user_id, source_provider, base_model, model_id, usage_hint, collection_id,
            q, tags, used, used_after, has_variables, nsfw,
        )
        join_sql = self._USAGE_JOIN_SQL if needs_usage_join else ""
        join_params = [user_id] if needs_usage_join else []
        query = f"SELECT COUNT(*) FROM prompts {join_sql} WHERE {' AND '.join(clauses)}"
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, (*join_params, *params))
            return int(cursor.fetchone()[0])

    def get_source_ids(
        self, user_id: str, source_provider: str, model_id: Optional[str] = None,
    ) -> "set[str]":
        clauses = ["user_id = ?", "source_provider = ?", "source_id IS NOT NULL"]
        params: List[Any] = [user_id, source_provider]
        if model_id is not None:
            clauses.append("model_id = ?")
            params.append(model_id)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT source_id FROM prompts WHERE {' AND '.join(clauses)}", params)
            return {row["source_id"] for row in cursor.fetchall()}

    def delete(self, prompt_id: str, user_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM prompts WHERE id = ? AND user_id = ?", (prompt_id, user_id))
            return cursor.rowcount > 0

    def bulk_delete(self, ids: Sequence[str], user_id: str) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"DELETE FROM prompts WHERE id IN ({placeholders}) AND user_id = ?", (*ids, user_id))
            return cursor.rowcount

    def delete_by_model(self, model_id: str, user_id: str) -> Tuple[int, List[str]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT id FROM prompts WHERE model_id = ? AND user_id = ?", (model_id, user_id))
            ids = [row[0] for row in cursor.fetchall()]
            if not ids:
                return 0, []
            placeholders = ",".join("?" for _ in ids)
            cursor.execute(f"DELETE FROM prompts WHERE id IN ({placeholders})", ids)
            return cursor.rowcount, ids

    def mark_embedded(self, prompt_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("UPDATE prompts SET embedded=1 WHERE id=?", (prompt_id,))
            return cursor.rowcount > 0

    def get_unembedded(self, user_id: str, limit: int = 100) -> List[Prompt]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM prompts WHERE user_id=? AND embedded=0 ORDER BY created_at LIMIT ?",
                (user_id, limit),
            )
            rows = cursor.fetchall()
            return [self._from_row(cursor, row) for row in rows]

    def has_embedded(self, user_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM prompts WHERE user_id=? AND embedded=1 LIMIT 1", (user_id,))
            return cursor.fetchone() is not None

    def reset_embedded(self, user_id: str) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("UPDATE prompts SET embedded=0 WHERE user_id=? AND embedded=1", (user_id,))
            return cursor.rowcount


prompt_repo = PromptRepository()
