from typing import Any, Dict, List, Optional
from src.platform.database import db
from src.features.generation.source_records import GenerationSource
from src.platform.util.ids import generate_ulid


class GenerationSourceRepository:
    def create_for_generation(
        self, generation_id: str, origins: List[Dict[str, Any]]
    ) -> List[GenerationSource]:
        """Bulk-insert `<field>__origin` links for a generation.

        `origins` is a list of `{field_name, source_generation_id,
        source_file_index}` dicts, already shape- and ownership-validated by
        the caller (`orchestrator.py`'s `_parse_generation_origins` /
        `_validate_generation_origins`) - this method does not re-validate.
        """
        created: List[GenerationSource] = []

        with db.get_cursor() as cursor:
            for origin in origins:
                source_id = generate_ulid()

                cursor.execute("""
                    INSERT INTO generation_sources (
                        id, generation_id, field_name,
                        source_generation_id, source_file_index
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    source_id,
                    generation_id,
                    origin['field_name'],
                    origin['source_generation_id'],
                    origin['source_file_index'],
                ))

                cursor.execute("SELECT * FROM generation_sources WHERE id = ?", (source_id,))
                row = cursor.fetchone()
                if row:
                    created.append(GenerationSource.from_row(row))

        return created

    def get_by_generation(self, generation_id: str) -> List[GenerationSource]:
        """All origin links for a generation, ordered by field_name.

        When a generation carries more than one origin field, callers that
        need a single "the" source (e.g. `get_params` inheritance) take the
        first row here - see that method's docstring for the limitation."""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM generation_sources
                WHERE generation_id = ?
                ORDER BY field_name
            """, (generation_id,))

            return [GenerationSource.from_row(row) for row in cursor.fetchall()]

    def get_primary_for_generation(self, generation_id: str) -> Optional[GenerationSource]:
        """The first origin link (by field_name), or None if the generation
        carries no provenance link."""
        sources = self.get_by_generation(generation_id)
        return sources[0] if sources else None


# Global repository instance
generation_source_repo = GenerationSourceRepository()
