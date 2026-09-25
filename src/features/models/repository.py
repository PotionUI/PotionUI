from typing import List, Optional, Dict, Any
from src.features.models.records import Model, ModelInfo, ModelFile, UserModel
from src.platform.util.ids import generate_ulid
from src.features.models.search_filter import (
    ModelSearchFilter,
    USAGE_JOIN,
    USAGE_SELECT,
    USAGE_SORT_FIELDS,
    search_filter_clauses,
)
import json
import logging

logger = logging.getLogger(__name__)

_SQLITE_IN_CHUNK_SIZE = 500

class ModelRepository:
    def create(self, model: Model) -> Model:
        """Create a new model entry"""
        if not model.id:
            model.id = generate_ulid()

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO models (
                    id, filename, file_path, file_size, sha256, model_type, user_notes, description, is_directory
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                model.id,
                model.filename,
                model.file_path,
                model.file_size,
                model.sha256,
                model.model_type,
                getattr(model, 'description', None),  # Use description for user_notes column
                getattr(model, 'description', None),
                getattr(model, 'is_directory', False)
            ))

        return self.get_by_id(model.id)

    def get_by_id(
        self,
        model_id: str,
        include_providers: bool = True,
        include_tags: bool = True,
        library_user_id: Optional[str] = None
    ) -> Optional[Model]:
        """Get model by ID.

        `library_user_id` mirrors `get_all`'s overlay: without it, `custom_name`/
        `is_favorite` are absent from the row and `Model.from_row` defaults
        `is_favorite` to False regardless of the actual per-user state.
        """
        library_select = ""
        library_join = ""
        params: List = [model_id]
        if library_user_id:
            library_select = ", umm.custom_name AS custom_name, umm.is_favorite AS is_favorite"
            library_join = " LEFT JOIN user_model_meta umm ON umm.model_id = m.id AND umm.user_id = ?"
            params = [library_user_id, model_id]

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT m.*{library_select} FROM models m{library_join} WHERE m.id = ?", params)
            row = cursor.fetchone()

            if not row:
                return None

            model = Model.from_row(row)

            if include_providers:
                model.providers = self.get_providers(model_id)

            if include_tags:
                from src.features.tags.repository import tag_repo
                model.tags = tag_repo.get_model_tags(model_id)

            # Load associated files and convert to API URLs
            model.files = self._get_model_files_with_urls(model_id)

            return model

    def get_by_sha256(self, sha256: str, include_providers: bool = True) -> Optional[Model]:
        """Get model by SHA256 hash"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM models WHERE sha256 = ?", (sha256,))
            row = cursor.fetchone()

            if not row:
                return None

            model = Model.from_row(row)

            if include_providers:
                model.providers = self.get_providers(model.id)

            # Load associated files and convert to API URLs
            model.files = self._get_model_files_with_urls(model.id)

            return model

    def get_by_file_path(self, file_path: str, include_providers: bool = True) -> Optional[Model]:
        """Get model by file path"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM models WHERE file_path = ?", (file_path,))
            row = cursor.fetchone()

            if not row:
                return None

            model = Model.from_row(row)

            if include_providers:
                model.providers = self.get_providers(model.id)

            # Load associated files and convert to API URLs
            model.files = self._get_model_files_with_urls(model.id)

            return model

    def get_by_identity(
        self, model_type: str, filename: str, include_providers: bool = True
    ) -> Optional[Model]:
        """Get a model by its identity: `(model_type, filename)`.

        This is how a model is matched across backends — native's local path and a
        ComfyUI server's bare name reduce to the same identity. See docs/models.md.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM models WHERE model_type = ? AND filename = ?",
                (model_type, filename),
            )
            row = cursor.fetchone()

            if not row:
                return None

            model = Model.from_row(row)

            if include_providers:
                model.providers = self.get_providers(model.id)

            model.files = self._get_model_files_with_urls(model.id)

            return model

    def get_by_filename(self, filename: str) -> List[Model]:
        """All models with this filename, across model types (uses idx_models_filename).

        `UNIQUE(model_type, filename)` means at most one per type, so more than one row
        here is a genuine cross-type ambiguity for the caller to resolve.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM models WHERE filename = ?", (filename,))
            return [Model.from_row(row) for row in cursor.fetchall()]

    def get_all(self,
                limit: Optional[int] = None,
                offset: int = 0,
                model_type: Optional[str] = None,
                tag_ids: Optional[List[str]] = None,
                any_tag_ids: Optional[List[str]] = None,
                search: Optional[str] = None,
                sort_by: str = "indexed_at",
                sort_order: str = "desc",
                include_providers: bool = True,
                include_tags: bool = True,
                include_files: bool = True,
                allowed_model_ids: Optional[List[str]] = None,
                assignment_filter: Optional[str] = None,
                assigned_user_id: Optional[str] = None,
                assigned_group_id: Optional[str] = None,
                library_user_id: Optional[str] = None,
                favorites_only: bool = False,
                collection_id: Optional[str] = None,
                in_any_collection: bool = False,
                search_filter: Optional[ModelSearchFilter] = None,
                include_usage: bool = False) -> List[Model]:
        """Get all models with optional filtering.

        `tag_ids` requires ALL listed tags (AND, used by the library's multi-tag
        browsing filter). `any_tag_ids` requires AT LEAST ONE (OR semantics) - used
        by a preset's `model` field `filter_tags:` (src/features/fields/model.py), where
        a model tagged with either of two alternative tags should still match.
        The two are independent and may be combined.
        """
        # "Member of any of the user's collections" filter - used by the picker's
        # Collections view search so a query spans the whole curated set. Requires
        # library_user_id (per-user concept); a no-op otherwise.
        in_any_collection_sql = (
            "m.id IN (SELECT mcm.model_id FROM model_collection_members mcm "
            "JOIN model_collections mc ON mc.id = mcm.collection_id WHERE mc.user_id = ?)"
        )
        use_in_any_collection = in_any_collection and bool(library_user_id)
        # Library overlay: LEFT JOIN user_model_meta so callers get custom_name/
        # is_favorite alongside each model, without affecting rows when
        # library_user_id is None (existing callers keep identical behavior).
        library_select = ""
        library_join = ""
        library_params: List = []
        if library_user_id:
            library_select = ", umm.custom_name AS custom_name, umm.is_favorite AS is_favorite"
            library_join = " LEFT JOIN user_model_meta umm ON umm.model_id = m.id AND umm.user_id = ?"
            library_params = [library_user_id]

        collection_join = ""
        collection_params: List = []
        if collection_id:
            collection_join = " INNER JOIN model_collection_members mcm ON mcm.model_id = m.id AND mcm.collection_id = ?"
            collection_params = [collection_id]

        # Only usable once umm is actually joined (library_user_id set) - otherwise
        # umm.is_favorite would reference a non-existent alias.
        library_where_clauses = []
        if favorites_only and library_user_id:
            library_where_clauses.append("COALESCE(umm.is_favorite, 0) = 1")

        advanced_clauses, advanced_params = search_filter_clauses(search_filter, bool(library_user_id))
        library_where_clauses.extend(advanced_clauses)
        use_usage = (
            include_usage
            or sort_by in USAGE_SORT_FIELDS
            or bool(search_filter and search_filter.needs_usage)
        )
        if use_usage:
            library_select += USAGE_SELECT
            collection_join += USAGE_JOIN

        query = f"SELECT DISTINCT m.*{library_select} FROM models m"
        params = []
        where_clauses = []

        # Add tag filtering via JOIN if specified
        if tag_ids and len(tag_ids) > 0:
            # Join with model_tags and filter by ALL specified tags
            query = f"""
                SELECT DISTINCT m.*{library_select} FROM models m{library_join}{collection_join}
                WHERE m.id IN (
                    SELECT model_id FROM model_tags
                    WHERE tag_id IN ({','.join('?' * len(tag_ids))})
                    GROUP BY model_id
                    HAVING COUNT(DISTINCT tag_id) = ?
                )
            """
            params.extend(library_params)
            params.extend(collection_params)
            params.extend(tag_ids)
            params.append(len(tag_ids))

            # Add additional filters
            additional_clauses = list(library_where_clauses)
            params.extend(advanced_params)
            if model_type:
                additional_clauses.append("m.model_type = ?")
                params.append(model_type)
            if search:
                additional_clauses.append("LOWER(m.filename) LIKE LOWER(?)")
                params.append(f"%{search}%")
            if use_in_any_collection:
                additional_clauses.append(in_any_collection_sql)
                params.append(library_user_id)
            if allowed_model_ids is not None:
                if len(allowed_model_ids) == 0:
                    return []
                placeholders = ','.join('?' * len(allowed_model_ids))
                additional_clauses.append(f"m.id IN ({placeholders})")
                params.extend(allowed_model_ids)
            if assignment_filter and assigned_user_id:
                subquery = "SELECT model_id FROM user_models WHERE user_id = ?"
                if assignment_filter == 'assigned':
                    additional_clauses.append(f"m.id IN ({subquery})")
                elif assignment_filter == 'unassigned':
                    additional_clauses.append(f"m.id NOT IN ({subquery})")
                params.append(assigned_user_id)
            if assignment_filter and assigned_group_id:
                subquery = "SELECT model_id FROM user_group_models WHERE group_id = ?"
                if assignment_filter == 'assigned':
                    additional_clauses.append(f"m.id IN ({subquery})")
                elif assignment_filter == 'unassigned':
                    additional_clauses.append(f"m.id NOT IN ({subquery})")
                params.append(assigned_group_id)
            if any_tag_ids:
                placeholders = ','.join('?' * len(any_tag_ids))
                additional_clauses.append(
                    f"m.id IN (SELECT DISTINCT model_id FROM model_tags WHERE tag_id IN ({placeholders}))"
                )
                params.extend(any_tag_ids)

            if additional_clauses:
                query += " AND " + " AND ".join(additional_clauses)
        else:
            # Simple query without tag filtering
            query += library_join + collection_join
            params.extend(library_params)
            params.extend(collection_params)

            where_clauses.extend(library_where_clauses)
            params.extend(advanced_params)
            if model_type:
                where_clauses.append("m.model_type = ?")
                params.append(model_type)
            if search:
                where_clauses.append("LOWER(m.filename) LIKE LOWER(?)")
                params.append(f"%{search}%")
            if use_in_any_collection:
                where_clauses.append(in_any_collection_sql)
                params.append(library_user_id)
            if allowed_model_ids is not None:
                if len(allowed_model_ids) == 0:
                    return []
                placeholders = ','.join('?' * len(allowed_model_ids))
                where_clauses.append(f"m.id IN ({placeholders})")
                params.extend(allowed_model_ids)
            if assignment_filter and assigned_user_id:
                subquery = "SELECT model_id FROM user_models WHERE user_id = ?"
                if assignment_filter == 'assigned':
                    where_clauses.append(f"m.id IN ({subquery})")
                elif assignment_filter == 'unassigned':
                    where_clauses.append(f"m.id NOT IN ({subquery})")
                params.append(assigned_user_id)
            if assignment_filter and assigned_group_id:
                subquery = "SELECT model_id FROM user_group_models WHERE group_id = ?"
                if assignment_filter == 'assigned':
                    where_clauses.append(f"m.id IN ({subquery})")
                elif assignment_filter == 'unassigned':
                    where_clauses.append(f"m.id NOT IN ({subquery})")
                params.append(assigned_group_id)
            if any_tag_ids:
                placeholders = ','.join('?' * len(any_tag_ids))
                where_clauses.append(
                    f"m.id IN (SELECT DISTINCT model_id FROM model_tags WHERE tag_id IN ({placeholders}))"
                )
                params.extend(any_tag_ids)

            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)

        # Add sorting
        valid_sort_fields = {
            'indexed_at': 'm.indexed_at',
            'modified_at': 'm.updated_at',
            'filename': 'm.filename',
            'file_size': 'm.file_size',
            'model_type': 'm.model_type',
            **USAGE_SORT_FIELDS,
        }

        # Default to indexed_at if sort_by is invalid
        sort_field = valid_sort_fields.get(sort_by, 'm.indexed_at')
        sort_direction = 'DESC' if sort_order.lower() == 'desc' else 'ASC'

        query += f" ORDER BY {sort_field} {sort_direction}, m.id {sort_direction}"

        if limit:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            models = [Model.from_row(row) for row in cursor.fetchall()]
            model_ids = [model.id for model in models]

            if include_providers:
                providers_by_model = self._providers_bulk(cursor, model_ids)
                for model in models:
                    model.providers = providers_by_model[model.id]

            if include_files:
                files_by_model = self._files_with_urls_bulk(cursor, model_ids)
                for model in models:
                    model.files = files_by_model[model.id]

        if include_tags:
            from src.features.tags.repository import tag_repo
            tags_by_model = tag_repo.get_model_tags_bulk(model_ids)
            for model in models:
                model.tags = tags_by_model[model.id]

        return models

    def update(self, model: Model) -> bool:
        """Update existing model.

        `is_available`/`unavailable_at` are written here (not just via
        `mark_unavailable`) because `index_single_model` is the only caller of
        this method: a model reached here because the indexer found its file on
        disk, so every update through this path is implicitly a revival for a
        row that had been marked unavailable.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE models
                SET filename = ?, file_path = ?, file_size = ?, sha256 = ?,
                    model_type = ?, user_notes = ?, description = ?, is_directory = ?,
                    is_available = ?, unavailable_at = ?,
                    indexed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                model.filename,
                model.file_path,
                model.file_size,
                model.sha256,
                model.model_type,
                getattr(model, 'description', None),  # Use description for user_notes column
                getattr(model, 'description', None),
                getattr(model, 'is_directory', False),
                getattr(model, 'is_available', True),
                getattr(model, 'unavailable_at', None),
                model.id
            ))

            return cursor.rowcount > 0

    def mark_unavailable(self, model_id: str) -> bool:
        """Soft-mark a model whose file the indexer could not find on disk.

        Keeps the row (tags/ratings/assignments survive); `update()` clears
        this the next time a scan finds the file again.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE models
                SET is_available = 0, unavailable_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (model_id,))

            return cursor.rowcount > 0

    def update_description(self, model_id: str, description: str) -> bool:
        """Update the markdown description for a model"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE models
                SET description = ?
                WHERE id = ?
            """, (description, model_id))

            return cursor.rowcount > 0

    def update_digest(self, model_id: str, *, sha256: str, file_size: int) -> bool:
        """Persist a freshly computed content digest without touching any other column."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE models
                SET sha256 = ?, file_size = ?
                WHERE id = ?
            """, (sha256, file_size, model_id))

            return cursor.rowcount > 0

    def update_prompting_guidance(self, model_id: str, prompting_guidance: str) -> bool:
        """Update the admin-authored prompting guidance text for a model"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE models
                SET prompting_guidance = ?
                WHERE id = ?
            """, (prompting_guidance, model_id))

            return cursor.rowcount > 0

    def update_preview_media(self, model_id: str, preview_media: Optional[str]) -> bool:
        """Set (or clear, with None) a model's admin-set preview media JSON."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE models
                SET preview_media = ?
                WHERE id = ?
            """, (preview_media, model_id))

            return cursor.rowcount > 0

    # --- Preview media list (multiple admin-set previews) ---

    def list_preview_media(self, model_id: str) -> List[Dict[str, Any]]:
        """List a model's preview rows ordered by position (0 = primary)."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT id, model_id, file_id, url, type, name, position, created_at
                FROM model_preview_media
                WHERE model_id = ?
                ORDER BY position ASC
            """, (model_id,))
            rows = cursor.fetchall()
            return [
                {
                    'id': row['id'],
                    'file_id': row['file_id'],
                    'url': row['url'],
                    'type': row['type'],
                    'name': row['name'],
                    'position': row['position'],
                }
                for row in rows
            ]

    def insert_preview_media_row(
        self,
        model_id: str,
        file_id: Optional[str],
        url: str,
        media_type: str,
        name: Optional[str],
        position: int,
    ) -> str:
        """Insert one preview row at `position`, returning its new id."""
        row_id = generate_ulid()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO model_preview_media (id, model_id, file_id, url, type, name, position)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (row_id, model_id, file_id, url, media_type, name, position))
        return row_id

    def get_preview_media_row(self, preview_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single preview row by id, or None."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT id, model_id, file_id, url, type, name, position
                FROM model_preview_media
                WHERE id = ?
            """, (preview_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'id': row['id'],
                'model_id': row['model_id'],
                'file_id': row['file_id'],
                'url': row['url'],
                'type': row['type'],
                'name': row['name'],
                'position': row['position'],
            }

    def delete_preview_media_row(self, preview_id: str) -> bool:
        """Delete one preview row. Caller is responsible for its `files` row."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM model_preview_media WHERE id = ?", (preview_id,))
            return cursor.rowcount > 0

    def set_preview_media_positions(self, model_id: str, ordered_ids: List[str]) -> None:
        """Rewrite positions 0..n-1 for `ordered_ids`, in the order given."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            for position, preview_id in enumerate(ordered_ids):
                cursor.execute("""
                    UPDATE model_preview_media
                    SET position = ?
                    WHERE id = ? AND model_id = ?
                """, (position, preview_id, model_id))

    def update_model_metadata(self, model_id: str, metadata: Dict[str, Any]) -> bool:
        """Replace a model's per-model-type metadata fields dict"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE models
                SET model_metadata = ?
                WHERE id = ?
            """, (json.dumps(metadata), model_id))

            return cursor.rowcount > 0

    def delete(self, model_id: str) -> bool:
        """Delete model and its Civitai info"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM models WHERE id = ?", (model_id,))
            return cursor.rowcount > 0

    def delete_unclaimed_orphans(self, model_ids: List[str]) -> int:
        """Hard-delete rows in `model_ids` that are unreachable and safe to drop.

        Only a backend-reported row - created by `BackendModelIndexer._create_model`,
        `file_path IS NULL` - can qualify; a depot scan's row always has a local
        `file_path` and is never touched here. The remaining guards keep the
        `ON DELETE CASCADE` on `providers`/`generation_models`/`model_availability`
        from silently erasing something worth keeping: a model must currently hold
        no backend claim, no provider info, and no generation-history link.
        """
        if not model_ids:
            return 0

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            placeholders = ",".join("?" for _ in model_ids)
            cursor.execute(
                f"""
                DELETE FROM models
                WHERE id IN ({placeholders})
                  AND file_path IS NULL
                  AND NOT EXISTS (SELECT 1 FROM model_availability WHERE model_availability.model_id = models.id)
                  AND NOT EXISTS (SELECT 1 FROM providers WHERE providers.model_id = models.id)
                  AND NOT EXISTS (SELECT 1 FROM generation_models WHERE generation_models.model_id = models.id)
                """,
                tuple(model_ids),
            )
            return cursor.rowcount

    def upsert(self, model: Model) -> Model:
        """Insert or update model based on file path"""
        existing = self.get_by_file_path(model.file_path, include_providers=False)

        if existing:
            # Update existing model
            model.id = existing.id
            self.update(model)
            return self.get_by_id(model.id)
        else:
            # Create new model
            return self.create(model)

    def count_by_type(self, tag_ids: Optional[List[str]] = None,
                     allowed_model_ids: Optional[List[str]] = None) -> Dict[str, int]:
        """Count models by type with optional tag and access filtering"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if allowed_model_ids is not None and len(allowed_model_ids) == 0:
                return {}

            if tag_ids and len(tag_ids) > 0:
                # Count with tag filtering
                placeholders = ','.join('?' * len(tag_ids))
                query = f"""
                    SELECT m.model_type, COUNT(DISTINCT m.id) as count
                    FROM models m
                    WHERE m.id IN (
                        SELECT model_id FROM model_tags
                        WHERE tag_id IN ({placeholders})
                        GROUP BY model_id
                        HAVING COUNT(DISTINCT tag_id) = ?
                    )
                """
                params = list(tag_ids) + [len(tag_ids)]

                if allowed_model_ids is not None:
                    id_placeholders = ','.join('?' * len(allowed_model_ids))
                    query += f" AND m.id IN ({id_placeholders})"
                    params.extend(allowed_model_ids)

                query += " GROUP BY m.model_type ORDER BY count DESC"
                cursor.execute(query, params)
            else:
                query = "SELECT model_type, COUNT(*) as count FROM models"
                params = []

                if allowed_model_ids is not None:
                    id_placeholders = ','.join('?' * len(allowed_model_ids))
                    query += f" WHERE id IN ({id_placeholders})"
                    params.extend(allowed_model_ids)

                query += " GROUP BY model_type ORDER BY count DESC"
                cursor.execute(query, params)
            return {row['model_type']: row['count'] for row in cursor.fetchall()}

    def get_total_size_by_type(self, tag_ids: Optional[List[str]] = None) -> Dict[str, int]:
        """Get total size by model type with optional tag filtering"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if tag_ids and len(tag_ids) > 0:
                # Get sizes with tag filtering
                placeholders = ','.join('?' * len(tag_ids))
                cursor.execute(f"""
                    SELECT m.model_type, SUM(m.file_size) as total_size 
                    FROM models m
                    WHERE m.file_size IS NOT NULL
                    AND m.id IN (
                        SELECT model_id FROM model_tags
                        WHERE tag_id IN ({placeholders})
                        GROUP BY model_id
                        HAVING COUNT(DISTINCT tag_id) = ?
                    )
                    GROUP BY m.model_type
                    ORDER BY total_size DESC
                """, (*tag_ids, len(tag_ids)))
            else:
                cursor.execute("""
                    SELECT model_type, SUM(file_size) as total_size 
                    FROM models 
                    WHERE file_size IS NOT NULL
                    GROUP BY model_type
                    ORDER BY total_size DESC
                """)
            return {row['model_type']: row['total_size'] or 0 for row in cursor.fetchall()}

    def get_models_missing_hashes(self) -> List[Model]:
        """Get models that don't have SHA256 hashes yet"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM models WHERE sha256 IS NULL ORDER BY filename")
            return [Model.from_row(row) for row in cursor.fetchall()]

    # Provider Methods
    def create_provider(self, provider_info: ModelInfo) -> ModelInfo:
        """Create provider info for a model"""
        if not provider_info.id:
            provider_info.id = generate_ulid()

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO providers (
                    id, model_id, provider, provider_model_id, provider_version_id,
                    name, description, tags, nsfw, download_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                provider_info.id,
                provider_info.model_id,
                provider_info.provider,
                provider_info.provider_model_id,
                provider_info.provider_version_id,
                provider_info.name,
                provider_info.description,
                json.dumps(provider_info.tags) if provider_info.tags else None,
                provider_info.nsfw,
                provider_info.download_url
            ))

        return self.get_provider_by_id(provider_info.id)

    def get_providers(self, model_id: str, provider: Optional[str] = None) -> List[ModelInfo]:
        """Get all provider info for a model, optionally filtered by provider"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if provider:
                cursor.execute("SELECT * FROM providers WHERE model_id = ? AND provider = ?", (model_id, provider))
            else:
                cursor.execute("SELECT * FROM providers WHERE model_id = ?", (model_id,))

            return [ModelInfo.from_row(row) for row in cursor.fetchall()]

    def get_provider_by_id(self, info_id: str) -> Optional[ModelInfo]:
        """Get provider info by its ID"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM providers WHERE id = ?", (info_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return ModelInfo.from_row(row)

    def update_provider(self, provider_info: ModelInfo) -> bool:
        """Update provider info"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE providers
                SET provider_model_id = ?, provider_version_id = ?, name = ?, description = ?,
                    tags = ?, nsfw = ?, download_url = ?
                WHERE id = ?
            """, (
                provider_info.provider_model_id,
                provider_info.provider_version_id,
                provider_info.name,
                provider_info.description,
                json.dumps(provider_info.tags) if provider_info.tags else None,
                provider_info.nsfw,
                provider_info.download_url,
                provider_info.id
            ))

            return cursor.rowcount > 0

    def upsert_provider(self, model_id: str, provider_info: ModelInfo) -> ModelInfo:
        """Insert or update provider info"""
        provider_info.model_id = model_id
        existing = self.get_providers(model_id, provider_info.provider)

        if existing:
            # Update existing
            provider_info.id = existing[0].id
            self.update_provider(provider_info)
        else:
            # Create new
            self.create_provider(provider_info)

        return self.get_provider_by_id(provider_info.id)

    def get_models_without_provider_info(self, provider: str = 'civitai', include_tags: bool = False) -> List[Model]:
        """Get models that don't have info from a specific provider"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT m.* FROM models m
                LEFT JOIN providers p ON m.id = p.model_id AND p.provider = ?
                WHERE p.model_id IS NULL
                ORDER BY m.filename
            """, (provider,))
            models = [Model.from_row(row) for row in cursor.fetchall()]

            if include_tags:
                from src.features.tags.repository import tag_repo
                for model in models:
                    model.tags = tag_repo.get_model_tags(model.id)

            return models


    def count_total(self, tag_ids: Optional[List[str]] = None, search: Optional[str] = None,
                    model_type: Optional[str] = None, allowed_model_ids: Optional[List[str]] = None,
                    assignment_filter: Optional[str] = None, assigned_user_id: Optional[str] = None,
                    assigned_group_id: Optional[str] = None, library_user_id: Optional[str] = None,
                    favorites_only: bool = False, collection_id: Optional[str] = None,
                    in_any_collection: bool = False,
                    search_filter: Optional[ModelSearchFilter] = None) -> int:
        """Count total models with optional tag, search, type, and access filtering"""
        rows = self._filtered_aggregate(
            "COUNT(DISTINCT m.id) AS count", "", "",
            tag_ids=tag_ids, search=search, model_type=model_type, allowed_model_ids=allowed_model_ids,
            assignment_filter=assignment_filter, assigned_user_id=assigned_user_id,
            assigned_group_id=assigned_group_id, library_user_id=library_user_id,
            favorites_only=favorites_only, collection_id=collection_id,
            in_any_collection=in_any_collection, search_filter=search_filter,
        )
        return rows[0]['count'] if rows else 0

    def count_filtered_by_type(self, **filters: Any) -> Dict[str, int]:
        rows = self._filtered_aggregate(
            "m.model_type AS model_type, COUNT(DISTINCT m.id) AS count", "", " GROUP BY m.model_type", **filters
        )
        return {row['model_type']: row['count'] for row in rows}

    def count_filtered_by_tag(self, **filters: Any) -> Dict[str, int]:
        rows = self._filtered_aggregate(
            "fmt.tag_id AS tag_id, COUNT(DISTINCT m.id) AS count",
            " INNER JOIN model_tags fmt ON fmt.model_id = m.id",
            " GROUP BY fmt.tag_id",
            **filters,
        )
        return {row['tag_id']: row['count'] for row in rows}

    def _filtered_aggregate(self, select_sql: str, extra_join: str, group_sql: str,
                            tag_ids: Optional[List[str]] = None, search: Optional[str] = None,
                            model_type: Optional[str] = None, allowed_model_ids: Optional[List[str]] = None,
                            assignment_filter: Optional[str] = None, assigned_user_id: Optional[str] = None,
                            assigned_group_id: Optional[str] = None, library_user_id: Optional[str] = None,
                            favorites_only: bool = False, collection_id: Optional[str] = None,
                            in_any_collection: bool = False,
                            search_filter: Optional[ModelSearchFilter] = None) -> List[Any]:
        if allowed_model_ids is not None and len(allowed_model_ids) == 0:
            return []

        joins = ""
        params: List = []
        if library_user_id:
            joins += " LEFT JOIN user_model_meta umm ON umm.model_id = m.id AND umm.user_id = ?"
            params.append(library_user_id)
        if collection_id:
            joins += " INNER JOIN model_collection_members mcm ON mcm.model_id = m.id AND mcm.collection_id = ?"
            params.append(collection_id)
        if search_filter and search_filter.needs_usage:
            joins += USAGE_JOIN
        joins += extra_join

        where_clauses: List[str] = []
        if tag_ids:
            placeholders = ','.join('?' * len(tag_ids))
            where_clauses.append(
                f"m.id IN (SELECT model_id FROM model_tags WHERE tag_id IN ({placeholders}) "
                "GROUP BY model_id HAVING COUNT(DISTINCT tag_id) = ?)"
            )
            params.extend(tag_ids)
            params.append(len(tag_ids))
        if favorites_only and library_user_id:
            where_clauses.append("COALESCE(umm.is_favorite, 0) = 1")
        advanced_clauses, advanced_params = search_filter_clauses(search_filter, bool(library_user_id))
        where_clauses.extend(advanced_clauses)
        params.extend(advanced_params)
        if search:
            where_clauses.append("LOWER(m.filename) LIKE LOWER(?)")
            params.append(f"%{search}%")
        if in_any_collection and library_user_id:
            where_clauses.append(
                "m.id IN (SELECT mcm2.model_id FROM model_collection_members mcm2 "
                "JOIN model_collections mc ON mc.id = mcm2.collection_id WHERE mc.user_id = ?)"
            )
            params.append(library_user_id)
        if model_type:
            where_clauses.append("m.model_type = ?")
            params.append(model_type)
        if allowed_model_ids is not None:
            where_clauses.append(f"m.id IN ({','.join('?' * len(allowed_model_ids))})")
            params.extend(allowed_model_ids)
        if assignment_filter and assigned_user_id:
            subquery = "SELECT model_id FROM user_models WHERE user_id = ?"
            if assignment_filter == 'assigned':
                where_clauses.append(f"m.id IN ({subquery})")
            elif assignment_filter == 'unassigned':
                where_clauses.append(f"m.id NOT IN ({subquery})")
            params.append(assigned_user_id)
        if assignment_filter and assigned_group_id:
            subquery = "SELECT model_id FROM user_group_models WHERE group_id = ?"
            if assignment_filter == 'assigned':
                where_clauses.append(f"m.id IN ({subquery})")
            elif assignment_filter == 'unassigned':
                where_clauses.append(f"m.id NOT IN ({subquery})")
            params.append(assigned_group_id)

        query = f"SELECT {select_sql} FROM models m{joins}"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += group_sql

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    # Model Files Methods
    def get_model_file_by_id(self, model_file_id: str) -> Optional[ModelFile]:
        """Get model file by ID"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM model_files WHERE id = ?", (model_file_id,))
            row = cursor.fetchone()
            return ModelFile.from_row(row) if row else None

    def get_model_files(self, model_id: str, file_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all files for a model with file details"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            query = """
                SELECT mf.*, f.file_path, f.file_size, f.created_at as file_created_at,
                       f.thumbnail_small, f.thumbnail_medium, f.thumbnail_large
                FROM model_files mf
                JOIN files f ON mf.file_id = f.id
                WHERE mf.model_id = ?
            """
            params = [model_id]

            if file_type:
                query += " AND mf.file_type = ?"
                params.append(file_type)

            query += " ORDER BY mf.created_at DESC"

            cursor.execute(query, params)
            results = []
            for row in cursor.fetchall():
                results.append({
                    'model_file_id': row['id'],
                    'file_id': row['file_id'],
                    'file_type': row['file_type'],
                    'file_path': row['file_path'],
                    'file_size': row['file_size'],
                    'created_at': row['created_at'],
                    'file_created_at': row['file_created_at'],
                    'thumbnail_small': row['thumbnail_small'],
                    'thumbnail_medium': row['thumbnail_medium'],
                    'thumbnail_large': row['thumbnail_large']
                })
            return results

    def _get_model_files_with_urls(self, model_id: str) -> List[Dict[str, Any]]:
        return [self._file_with_urls(file_data) for file_data in self.get_model_files(model_id)]

    def _file_with_urls(self, file_data: Dict[str, Any]) -> Dict[str, Any]:
        file_id = file_data['file_id']
        return {
            'id': file_id,
            'file_type': file_data['file_type'],
            'url': f"/api/media/files/{file_id}",
            'file_size': file_data['file_size'],
            'created_at': file_data['created_at'],
            'thumbnail_small': f"/api/media/files/{file_id}?size=small" if file_data.get('thumbnail_small') else None,
            'thumbnail_medium': f"/api/media/files/{file_id}?size=medium" if file_data.get('thumbnail_medium') else None,
            'thumbnail_large': f"/api/media/files/{file_id}?size=large" if file_data.get('thumbnail_large') else None,
            'display_order': file_data.get('display_order', 0),
        }

    def _providers_bulk(self, cursor, model_ids: List[str]) -> Dict[str, List[ModelInfo]]:
        result: Dict[str, List[ModelInfo]] = {model_id: [] for model_id in model_ids}
        for start in range(0, len(model_ids), _SQLITE_IN_CHUNK_SIZE):
            chunk = model_ids[start:start + _SQLITE_IN_CHUNK_SIZE]
            placeholders = ','.join('?' * len(chunk))
            cursor.execute(
                f"SELECT * FROM providers WHERE model_id IN ({placeholders}) ORDER BY model_id ASC, rowid ASC",
                chunk,
            )
            for row in cursor.fetchall():
                result[row['model_id']].append(ModelInfo.from_row(row))
        return result

    def _model_files_bulk(self, cursor, model_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        result: Dict[str, List[Dict[str, Any]]] = {model_id: [] for model_id in model_ids}
        for start in range(0, len(model_ids), _SQLITE_IN_CHUNK_SIZE):
            chunk = model_ids[start:start + _SQLITE_IN_CHUNK_SIZE]
            placeholders = ','.join('?' * len(chunk))
            cursor.execute(f"""
                SELECT mf.*, f.file_path, f.file_size, f.created_at as file_created_at,
                       f.thumbnail_small, f.thumbnail_medium, f.thumbnail_large
                FROM model_files mf
                JOIN files f ON mf.file_id = f.id
                WHERE mf.model_id IN ({placeholders})
                ORDER BY mf.model_id ASC, mf.created_at DESC, mf.rowid ASC
            """, chunk)
            for row in cursor.fetchall():
                result[row['model_id']].append({
                    'model_file_id': row['id'],
                    'file_id': row['file_id'],
                    'file_type': row['file_type'],
                    'file_path': row['file_path'],
                    'file_size': row['file_size'],
                    'created_at': row['created_at'],
                    'file_created_at': row['file_created_at'],
                    'thumbnail_small': row['thumbnail_small'],
                    'thumbnail_medium': row['thumbnail_medium'],
                    'thumbnail_large': row['thumbnail_large'],
                })
        return result

    def _files_with_urls_bulk(self, cursor, model_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        files_by_model = self._model_files_bulk(cursor, model_ids)
        return {
            model_id: [self._file_with_urls(file_data) for file_data in files]
            for model_id, files in files_by_model.items()
        }

    # ===== User-Model Assignment =====

    def assign_model_to_user(self, model_id: str, user_id: str) -> Optional[UserModel]:
        """
        Assign a model to a user, or return None if the insert violated a constraint.

        `user_models` enforces `UNIQUE(user_id, model_id)` plus foreign keys onto
        both `users` and `models`, so four different mistakes all surface here as
        one `IntegrityError`: the pair is already assigned, the model id is
        unknown (or blank), or the user id is unknown. SQLite doesn't say which
        foreign key failed - `ModelAssignmentService.assign_model_to_user` re-queries
        to tell the caller which. The exception is logged rather than swallowed,
        so the real constraint is always recoverable from the logs.
        """
        assignment_id = generate_ulid()
        from src.platform.database.database import db
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO user_models (id, user_id, model_id) VALUES (?, ?, ?)",
                    (assignment_id, user_id, model_id)
                )
            return self.get_user_model_assignment(assignment_id)
        except Exception as e:
            logger.warning(
                f"assign_model_to_user(model_id={model_id!r}, user_id={user_id!r}) failed: {e}"
            )
            return None

    def find_user_model_assignment(self, model_id: str, user_id: str) -> Optional[UserModel]:
        """The existing assignment for this (model, user) pair, if there is one."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_models WHERE model_id = ? AND user_id = ?",
                (model_id, user_id)
            )
            row = cursor.fetchone()
            return UserModel.from_row(row) if row else None

    def get_user_model_assignment(self, assignment_id: str) -> Optional[UserModel]:
        """Get a user-model assignment by ID"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_models WHERE id = ?", (assignment_id,))
            row = cursor.fetchone()
            return UserModel.from_row(row) if row else None

    def unassign_model_from_user(self, model_id: str, user_id: str) -> bool:
        """Unassign a model from a user"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM user_models WHERE model_id = ? AND user_id = ?",
                (model_id, user_id)
            )
            return cursor.rowcount > 0

    def get_user_model_assignments(self, user_id: str) -> List[str]:
        """Get model IDs assigned directly to a user"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT model_id FROM user_models WHERE user_id = ?",
                (user_id,)
            )
            return [row['model_id'] for row in cursor.fetchall()]

    def get_user_models(self, user_id: str) -> List[UserModel]:
        """Get all UserModel records for a user"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_models WHERE user_id = ? ORDER BY assigned_at DESC",
                (user_id,)
            )
            return [UserModel.from_row(row) for row in cursor.fetchall()]

    def get_model_users(self, model_id: str) -> List[UserModel]:
        """Get all users directly assigned to a model"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_models WHERE model_id = ? ORDER BY assigned_at DESC",
                (model_id,)
            )
            return [UserModel.from_row(row) for row in cursor.fetchall()]

    def get_model_assignment_summary(self) -> Dict[str, Dict[str, int]]:
        """Direct-user and group assignment counts per model, batched (two
        GROUP BY queries, not one per model)."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT model_id, COUNT(*) as c FROM user_models GROUP BY model_id")
            direct = {row['model_id']: row['c'] for row in cursor.fetchall()}
            cursor.execute("SELECT model_id, COUNT(*) as c FROM user_group_models GROUP BY model_id")
            group = {row['model_id']: row['c'] for row in cursor.fetchall()}
        return {
            model_id: {
                'assignment_count': direct.get(model_id, 0),
                'group_count': group.get(model_id, 0)
            }
            for model_id in (direct.keys() | group.keys())
        }

    def is_model_assigned_to_user(self, model_id: str, user_id: str) -> bool:
        """Check if a model is assigned to a user (direct or via group)"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT 1 FROM (
                    SELECT model_id FROM user_models WHERE user_id = ? AND model_id = ?
                    UNION
                    SELECT ugm2.model_id FROM user_group_models ugm2
                    JOIN user_group_members ugm ON ugm2.group_id = ugm.group_id
                    WHERE ugm.user_id = ? AND ugm2.model_id = ?
                ) LIMIT 1
            """, (user_id, model_id, user_id, model_id))
            return cursor.fetchone() is not None

    def get_available_model_ids_for_user(self, user_id: str) -> List[str]:
        """Get model IDs available to a user from direct + group assignments"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT model_id FROM (
                    SELECT model_id FROM user_models WHERE user_id = ?
                    UNION
                    SELECT ugm2.model_id FROM user_group_models ugm2
                    JOIN user_group_members ugm ON ugm2.group_id = ugm.group_id
                    WHERE ugm.user_id = ?
                )
            """, (user_id, user_id))
            return [row['model_id'] for row in cursor.fetchall()]

# Global repository instance
model_repo = ModelRepository()
