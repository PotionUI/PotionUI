import re
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone
from src.features.generation.records import Generation, File, GenerationFile
from .file_repository import file_repo
from src.platform.util.ids import generate_ulid

# The format SQLite's CURRENT_TIMESTAMP uses for created_at/updated_at. Timestamps written
# from Python must match it so that the lexicographic date filters in `_date_cond` (which
# compare against 'YYYY-MM-DD HH:MM:SS') stay correct. This costs sub-second precision on
# duration_ms, which is why durations land on whole seconds.
_TIMESTAMP_FMT = '%Y-%m-%d %H:%M:%S'

# Columns allowed for ORDER BY (whitelist to prevent SQL injection).
# Maps a public sort key to the SQL ORDER BY expression (alias `g`).
_SORT_COLUMNS = {
    'created_at': 'g.created_at',
    'completed_at': 'g.completed_at',
    'rating': 'g.rating',
    'file_size': (
        '(SELECT COALESCE(SUM(f.file_size), 0) FROM generation_files gf '
        'JOIN files f ON gf.file_id = f.id WHERE gf.generation_id = g.id)'
    ),
}


class GenerationRepository:
    def create(self, generation: Generation) -> Generation:
        """Create a new generation"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generations (
                    id, preset_id, preset_version, form_data, user_id, status, progress,
                    mode, prompt_state, backend_id, tab_id, form_name, source_prompt_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                generation.id,
                generation.preset_id,
                generation.preset_version,
                generation.serialize_form_data(),
                generation.user_id,
                generation.status,
                generation.progress,
                generation.mode,
                generation.serialize_prompt_state(),
                generation.backend_id,
                generation.tab_id,
                generation.form_name,
                generation.source_prompt_id
            ))

        return self.get_by_id(generation.id)

    def get_by_id(self, generation_id: str, user_id: Optional[str] = None, include_files: bool = False) -> Optional[Generation]:
        """Get generation by ID, optionally filtered by user"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute("SELECT * FROM generations WHERE id = ? AND user_id = ?", (generation_id, user_id))
            else:
                cursor.execute("SELECT * FROM generations WHERE id = ?", (generation_id,))
            row = cursor.fetchone()

            if not row:
                return None

            generation = Generation.from_row(row)

            if include_files:
                generation.files = file_repo.get_generation_files(generation_id, user_id=user_id)

            return generation

    # --- Shared filter building -------------------------------------------------

    def _build_filters(
        self, alias: str = 'g', *,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        media_type: Optional[str] = None,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
        completed_from: Optional[str] = None,
        completed_to: Optional[str] = None,
        search: Optional[str] = None,
        mode: Optional[str] = None,
        preset_id: Optional[str] = None,
        model_name: Optional[str] = None,
        min_rating: Optional[int] = None,
        favorites_only: bool = False,
        used_phrasebook_value_id: Optional[str] = None,
        system_tag: Optional[str] = None,
        generation_ids: Optional[List[str]] = None,
    ) -> Tuple[List[str], List[Any]]:
        """Build WHERE conditions + params shared by get_all and count_by_status.

        `alias` is the table alias used in the enclosing query (e.g. 'g').
        Returns (conditions, params) in matching order.
        """
        a = f"{alias}." if alias else ""
        conditions: List[str] = []
        params: List[Any] = []

        if media_type:
            conditions.append(f"""EXISTS (
                SELECT 1 FROM generation_files gf
                JOIN files f ON gf.file_id = f.id
                WHERE gf.generation_id = {a}id
                AND f.file_type = ?
            )""")
            params.append(media_type.upper())

        if user_id:
            conditions.append(f"{a}user_id = ?")
            params.append(user_id)

        if status:
            conditions.append(f"{a}status = ?")
            params.append(status)

        def _date_cond(value, column, op, day_suffix):
            if not value:
                return
            bound = value + day_suffix if len(value) == 10 else value
            conditions.append(f"datetime({a}{column}) {op} datetime(?)")
            params.append(bound)

        _date_cond(created_from, 'created_at', '>=', ' 00:00:00')
        _date_cond(created_to, 'created_at', '<=', ' 23:59:59')
        _date_cond(completed_from, 'completed_at', '>=', ' 00:00:00')
        _date_cond(completed_to, 'completed_at', '<=', ' 23:59:59')

        if mode:
            conditions.append(f"{a}mode = ?")
            params.append(mode)

        if preset_id:
            conditions.append(f"{a}preset_id = ?")
            params.append(preset_id)

        if min_rating:
            conditions.append(f"{a}rating >= ?")
            params.append(min_rating)

        if favorites_only:
            conditions.append(f"{a}is_favorite = 1")

        if model_name:
            conditions.append(f"""EXISTS (
                SELECT 1 FROM generation_models gm
                JOIN models m ON gm.model_id = m.id
                WHERE gm.generation_id = {a}id
                AND m.filename LIKE ?
            )""")
            params.append(f"%{model_name}%")

        if used_phrasebook_value_id:
            conditions.append(f"""EXISTS (
                SELECT 1 FROM generation_segment_phrasebook ga
                WHERE ga.generation_id = {a}id
                AND ga.phrasebook_value_id = ?
            )""")
            params.append(used_phrasebook_value_id)

        if system_tag:
            conditions.append(f"""EXISTS (
                SELECT 1 FROM media_system_tags mst
                WHERE mst.generation_id = {a}id
                AND mst.category != 'rating'
                AND LOWER(mst.tag) = LOWER(?)
            )""")
            params.append(system_tag)

        if generation_ids is not None:
            if generation_ids:
                placeholders = ','.join('?' * len(generation_ids))
                conditions.append(f"{a}id IN ({placeholders})")
                params.extend(generation_ids)
            else:
                conditions.append("1 = 0")

        if search:
            search_conditions, search_params = self._build_search(search, alias)
            conditions.extend(search_conditions)
            params.extend(search_params)

        return conditions, params

    def _parse_search_terms(self, search: str) -> List[Tuple[str, bool]]:
        """Parse a search string into (term, negate) tuples.

        Supports quoted phrases ("a b"), NOT terms (!term), and comma/space
        separated AND terms.
        """
        tokens = re.findall(r'!?"[^"]*"|!?[^\s,]+', search)
        terms: List[Tuple[str, bool]] = []
        for tok in tokens:
            negate = tok.startswith('!')
            if negate:
                tok = tok[1:]
            tok = tok.strip('"').strip()
            if tok:
                terms.append((tok, negate))
        return terms

    def _build_search(self, search: str, alias: str = 'g') -> Tuple[List[str], List[Any]]:
        """Build full-text search conditions across prompt (form_data), preset, models."""
        a = f"{alias}." if alias else ""
        conditions: List[str] = []
        params: List[Any] = []
        for term, negate in self._parse_search_terms(search):
            like = f"%{term}%"
            clause = (
                f"({a}form_data LIKE ? OR {a}preset_id LIKE ? OR EXISTS ("
                f"SELECT 1 FROM generation_models gm JOIN models m ON gm.model_id = m.id "
                f"WHERE gm.generation_id = {a}id AND m.filename LIKE ?))"
            )
            if negate:
                clause = f"NOT {clause}"
            conditions.append(clause)
            params.extend([like, like, like])
        return conditions, params

    def _resolve_sort(self, sort_by: Optional[str], sort_dir: Optional[str]) -> str:
        """Return a safe ORDER BY clause from whitelisted inputs."""
        column = _SORT_COLUMNS.get((sort_by or 'created_at'), _SORT_COLUMNS['created_at'])
        direction = 'ASC' if (sort_dir or 'desc').lower() == 'asc' else 'DESC'
        return f" ORDER BY {column} {direction}"

    # --- Listing ----------------------------------------------------------------

    def get_all(self, user_id: Optional[str] = None, limit: Optional[int] = None, offset: int = 0,
                status: Optional[str] = None, include_files: bool = False,
                include_tags: bool = False, tag_ids: Optional[List[str]] = None,
                created_from: Optional[str] = None, created_to: Optional[str] = None,
                completed_from: Optional[str] = None, completed_to: Optional[str] = None,
                media_type: Optional[str] = None,
                search: Optional[str] = None, mode: Optional[str] = None,
                preset_id: Optional[str] = None, model_name: Optional[str] = None,
                min_rating: Optional[int] = None, favorites_only: bool = False,
                collection_id: Optional[str] = None,
                used_phrasebook_value_id: Optional[str] = None,
                system_tag: Optional[str] = None,
                generation_ids: Optional[List[str]] = None,
                sort_by: Optional[str] = None, sort_dir: Optional[str] = None) -> List[Generation]:
        """Get all generations with optional filtering, searching and sorting."""

        joins = ""
        conditions: List[str] = []
        params: List[Any] = []

        if tag_ids:
            joins += " INNER JOIN generation_tags gt ON g.id = gt.generation_id"
            placeholders = ','.join('?' * len(tag_ids))
            conditions.append(f"gt.tag_id IN ({placeholders})")
            params.extend(tag_ids)

        if collection_id:
            joins += " INNER JOIN collection_generations cg ON g.id = cg.generation_id"
            conditions.append("cg.collection_id = ?")
            params.append(collection_id)

        filter_conditions, filter_params = self._build_filters(
            'g', user_id=user_id, status=status, media_type=media_type,
            created_from=created_from, created_to=created_to,
            completed_from=completed_from, completed_to=completed_to,
            search=search, mode=mode, preset_id=preset_id, model_name=model_name,
            min_rating=min_rating, favorites_only=favorites_only,
            used_phrasebook_value_id=used_phrasebook_value_id,
            system_tag=system_tag, generation_ids=generation_ids,
        )
        conditions.extend(filter_conditions)
        params.extend(filter_params)

        query = f"SELECT DISTINCT g.* FROM generations g{joins}"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        if tag_ids:
            query += " GROUP BY g.id HAVING COUNT(DISTINCT gt.tag_id) = ?"
            params.append(len(tag_ids))

        query += self._resolve_sort(sort_by, sort_dir)

        if limit:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            generations = [Generation.from_row(row) for row in cursor.fetchall()]

            if include_files:
                generation_ids = [generation.id for generation in generations]
                files_by_generation = file_repo.get_generation_files_bulk(generation_ids, user_id=user_id)
                for generation in generations:
                    generation.files = files_by_generation[generation.id]

            if include_tags:
                from src.features.tags.repository import tag_repo
                generation_ids = [generation.id for generation in generations]
                tags_by_generation = tag_repo.get_generation_tags_bulk(generation_ids)
                for generation in generations:
                    generation.tags = tags_by_generation[generation.id]

            return generations

    def count_by_status(self, user_id: Optional[str] = None, status: Optional[str] = None,
                        tag_ids: Optional[List[str]] = None,
                        created_from: Optional[str] = None, created_to: Optional[str] = None,
                        completed_from: Optional[str] = None, completed_to: Optional[str] = None,
                        media_type: Optional[str] = None,
                        search: Optional[str] = None, mode: Optional[str] = None,
                        preset_id: Optional[str] = None, model_name: Optional[str] = None,
                        min_rating: Optional[int] = None, favorites_only: bool = False,
                        collection_id: Optional[str] = None,
                        used_phrasebook_value_id: Optional[str] = None,
                        system_tag: Optional[str] = None) -> int:
        """Count generations matching the same filters as get_all (for pagination total)."""

        joins = ""
        conditions: List[str] = []
        params: List[Any] = []
        needs_group = False

        if tag_ids:
            joins += " INNER JOIN generation_tags gt ON g.id = gt.generation_id"
            placeholders = ','.join('?' * len(tag_ids))
            conditions.append(f"gt.tag_id IN ({placeholders})")
            params.extend(tag_ids)
            needs_group = True

        if collection_id:
            joins += " INNER JOIN collection_generations cg ON g.id = cg.generation_id"
            conditions.append("cg.collection_id = ?")
            params.append(collection_id)

        filter_conditions, filter_params = self._build_filters(
            'g', user_id=user_id, status=status, media_type=media_type,
            created_from=created_from, created_to=created_to,
            completed_from=completed_from, completed_to=completed_to,
            search=search, mode=mode, preset_id=preset_id, model_name=model_name,
            min_rating=min_rating, favorites_only=favorites_only,
            used_phrasebook_value_id=used_phrasebook_value_id,
            system_tag=system_tag,
        )
        conditions.extend(filter_conditions)
        params.extend(filter_params)

        inner = f"SELECT g.id FROM generations g{joins}"
        if conditions:
            inner += " WHERE " + " AND ".join(conditions)

        if needs_group:
            inner += " GROUP BY g.id HAVING COUNT(DISTINCT gt.tag_id) = ?"
            params.append(len(tag_ids))
            query = f"SELECT COUNT(*) FROM ({inner})"
        else:
            # DISTINCT guards against row fan-out from a collection join
            query = f"SELECT COUNT(DISTINCT g.id) FROM generations g{joins}"
            if conditions:
                query += " WHERE " + " AND ".join(conditions)

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()[0]

    # --- Prompt Library provenance -----------------------------------------------

    def get_by_source_prompt(
        self, prompt_id: str, user_id: str, limit: int = 20, offset: int = 0,
        include_files: bool = True,
    ) -> List[Generation]:
        """Completed generations submitted from this library prompt, newest first.

        Scoped to `user_id` the same way the history endpoints are - a prompt
        id alone can't leak another user's generations through this side door.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM generations WHERE source_prompt_id = ? AND user_id = ? "
                "AND status = 'completed' ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (prompt_id, user_id, limit, offset),
            )
            generations = [Generation.from_row(row) for row in cursor.fetchall()]

        if include_files:
            generation_ids = [generation.id for generation in generations]
            files_by_generation = file_repo.get_generation_files_bulk(generation_ids, user_id=user_id)
            for generation in generations:
                generation.files = files_by_generation[generation.id]

        return generations

    def count_by_source_prompt(self, prompt_id: str, user_id: str) -> int:
        """Total completed generations for `get_by_source_prompt`'s pagination."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM generations WHERE source_prompt_id = ? AND user_id = ? "
                "AND status = 'completed'",
                (prompt_id, user_id),
            )
            return cursor.fetchone()[0]

    def usage_stats_by_source_prompt(
        self, prompt_ids: List[str], user_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """Per-prompt usage aggregate for the prompt list: `{prompt_id: {usage_count,
        last_used_at}}`. Counts generations of any status (a failed attempt still
        "used" the prompt); a prompt id missing from the result has zero usage.
        One grouped query for the whole page, not one query per prompt.
        """
        if not prompt_ids:
            return {}
        placeholders = ','.join('?' * len(prompt_ids))
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT source_prompt_id, COUNT(*) AS usage_count, MAX(created_at) AS last_used_at "
                f"FROM generations WHERE source_prompt_id IN ({placeholders}) AND user_id = ? "
                f"GROUP BY source_prompt_id",
                (*prompt_ids, user_id),
            )
            return {
                row['source_prompt_id']: {
                    'usage_count': row['usage_count'],
                    'last_used_at': row['last_used_at'],
                }
                for row in cursor.fetchall()
            }

    # --- Ratings & favorites ----------------------------------------------------

    def update_rating(self, generation_id: str, rating: int, user_id: Optional[str] = None) -> bool:
        """Set the star rating (0-5) for a generation."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute(
                    "UPDATE generations SET rating = ? WHERE id = ? AND user_id = ?",
                    (rating, generation_id, user_id)
                )
            else:
                cursor.execute(
                    "UPDATE generations SET rating = ? WHERE id = ?",
                    (rating, generation_id)
                )
            return cursor.rowcount > 0

    def set_favorite(self, generation_id: str, is_favorite: bool, user_id: Optional[str] = None) -> bool:
        """Toggle the favorite flag for a generation."""
        value = 1 if is_favorite else 0
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute(
                    "UPDATE generations SET is_favorite = ? WHERE id = ? AND user_id = ?",
                    (value, generation_id, user_id)
                )
            else:
                cursor.execute(
                    "UPDATE generations SET is_favorite = ? WHERE id = ?",
                    (value, generation_id)
                )
            return cursor.rowcount > 0

    # --- Facets -----------------------------------------------------------------

    def get_facets(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Return distinct modes, presets and models (with counts) for filter UIs."""
        user_filter = " WHERE user_id = ?" if user_id else ""
        user_params: List[Any] = [user_id] if user_id else []

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            # Modes
            cursor.execute(
                f"SELECT mode, COUNT(*) AS c FROM generations{user_filter} "
                f"GROUP BY mode ORDER BY c DESC",
                user_params
            )
            modes = [{'value': row['mode'], 'count': row['c']} for row in cursor.fetchall() if row['mode']]

            # Presets
            preset_filter = " WHERE preset_id IS NOT NULL"
            preset_params: List[Any] = []
            if user_id:
                preset_filter += " AND user_id = ?"
                preset_params.append(user_id)
            cursor.execute(
                f"SELECT preset_id, COUNT(*) AS c FROM generations{preset_filter} "
                f"GROUP BY preset_id ORDER BY c DESC",
                preset_params
            )
            # Ids only: display names live in preset YAML, not in this table.
            presets = [
                {'id': row['preset_id'], 'count': row['c']}
                for row in cursor.fetchall()
            ]

            # Models (by filename)
            model_join = ""
            model_params: List[Any] = []
            if user_id:
                model_join = " JOIN generations g ON gm.generation_id = g.id"
                where = " WHERE g.user_id = ?"
                model_params.append(user_id)
            else:
                where = ""
            cursor.execute(
                f"SELECT m.filename AS name, COUNT(DISTINCT gm.generation_id) AS c "
                f"FROM models m JOIN generation_models gm ON gm.model_id = m.id{model_join}{where} "
                f"GROUP BY m.id ORDER BY c DESC",
                model_params
            )
            models = [{'name': row['name'], 'count': row['c']} for row in cursor.fetchall() if row['name']]

        return {'modes': modes, 'presets': presets, 'models': models}

    # --- Status / progress / lifecycle -----------------------------------------

    def update_status(self, generation_id: str, status: str, error_message: Optional[str] = None) -> bool:
        """Update generation status.

        Timestamps are written in UTC to match `created_at`/`updated_at`, which SQLite fills
        from CURRENT_TIMESTAMP. Writing naive local time here made `completed_at - created_at`
        off by the host's UTC offset (see migration 075).

        `duration_ms` is stored rather than derived: `updated_at` is bumped by any later write
        (rating, favouriting), so it cannot be trusted as a completion time after the fact.
        Duration runs from `started_at` when known, else from `created_at`. The queue's
        dispatcher transitions a generation to `running` when it claims a backend slot, so
        `started_at` marks execution start and the recorded duration excludes queue wait.
        A generation cancelled while still queued never ran, so it keeps a NULL `started_at`
        and its duration falls back to `created_at`.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            now = datetime.now(timezone.utc).strftime(_TIMESTAMP_FMT)

            if status == 'running':
                cursor.execute(
                    "UPDATE generations SET status = ?, started_at = COALESCE(started_at, ?) WHERE id = ?",
                    (status, now, generation_id)
                )
            elif status in ['completed', 'failed', 'cancelled']:
                cursor.execute(
                    """
                    UPDATE generations
                    SET status = ?,
                        completed_at = ?,
                        duration_ms = CAST(ROUND(
                            (julianday(?) - julianday(COALESCE(started_at, created_at))) * 86400000.0
                        ) AS INTEGER),
                        error_message = ?
                    WHERE id = ?
                    """,
                    (status, now, now, error_message, generation_id)
                )
            else:
                cursor.execute(
                    "UPDATE generations SET status = ? WHERE id = ?",
                    (status, generation_id)
                )

            return cursor.rowcount > 0

    def update_preset_version(self, generation_id: str, preset_version: str) -> bool:
        """Record which preset version actually rendered this generation's pipeline."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE generations SET preset_version = ? WHERE id = ?",
                (preset_version, generation_id)
            )
            return cursor.rowcount > 0

    def update_progress(self, generation_id: str, progress: float) -> bool:
        """Update generation progress"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE generations
                SET progress = ?
                WHERE id = ?
            """, (progress, generation_id))

            return cursor.rowcount > 0

    def delete(self, generation_id: str) -> bool:
        """Delete generation and its files"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generations WHERE id = ?", (generation_id,))
            return cursor.rowcount > 0

    def add_file(self, generation_id: str, file: File) -> File:
        """Add a file to a generation (creates file and associates it)"""
        # Create the file first
        created_file = file_repo.create(file)

        # Associate it with the generation
        file_repo.associate_with_generation(generation_id, created_file.id)

        return created_file

    def get_files(self, generation_id: str, user_id: Optional[str] = None, file_type: Optional[str] = None, is_final: Optional[bool] = None) -> List[File]:
        """Get files for a generation (delegates to file_repo)"""
        return file_repo.get_generation_files(generation_id, user_id=user_id, file_type=file_type, is_final=is_final)

    def get_active_generations(self, user_id: Optional[str] = None) -> List[Generation]:
        """Get all active (pending/running) generations"""
        return self.get_all(user_id=user_id, status='pending') + self.get_all(user_id=user_id, status='running')

    def reconcile_interrupted_generations(self) -> int:
        """Fail every generation still `pending`/`running` at boot.

        Generation state lives only in-process (`GenerationStatusTracker`); a non-terminal
        row surviving a restart means the process died mid-generation (OOM/kill/crash), not
        that it is somehow still running. Called once from the startup lifespan
        (`src.bootstrap.app`), alongside the download-worker resume.

        `duration_ms` is left NULL rather than synthesized -- a row stranded for months has
        no meaningful duration -- and `progress` is left at its last observed value. The
        `update_generations_updated_at` trigger bumps `updated_at` for touched rows only.
        """
        from src.features.generation.status_tracker import GenerationState
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            now = datetime.now(timezone.utc).strftime(_TIMESTAMP_FMT)
            cursor.execute(
                "UPDATE generations SET status = ?, completed_at = ? WHERE status IN (?, ?)",
                (
                    GenerationState.FAILED.value,
                    now,
                    GenerationState.PENDING.value,
                    GenerationState.RUNNING.value,
                ),
            )
            return cursor.rowcount


# Global repository instance
generation_repo = GenerationRepository()
