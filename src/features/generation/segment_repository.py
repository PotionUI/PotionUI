from typing import List, Dict, Any, Union
from src.platform.database import db
from src.features.generation.segment_records import GenerationSegment, GenerationSegmentPhrasebook
from src.platform.util.ids import generate_ulid


def _get(obj: Union[Dict[str, Any], object], key: str, default=None):
    """Read `key` from a dict or an attribute-bearing object (pydantic model, etc)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class GenerationSegmentRepository:
    def create_for_generation(self, generation_id: str, segments: List[Any]) -> List[GenerationSegment]:
        """Bulk-insert segments (and their phrasebooks) for a generation.

        `segments` is a list of SegmentInput pydantic models or plain dicts.
        """
        created: List[GenerationSegment] = []

        with db.get_cursor() as cursor:
            for segment in segments:
                segment_id = generate_ulid()

                cursor.execute("""
                    INSERT INTO generation_segments (
                        id, generation_id, channel, prompt_index, segment_index,
                        segment_type, text, name, color, description, is_disabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    segment_id,
                    generation_id,
                    _get(segment, 'channel', 'positive'),
                    _get(segment, 'prompt_index', 0),
                    _get(segment, 'segment_index', 0),
                    _get(segment, 'segment_type', 'content'),
                    _get(segment, 'text', ''),
                    _get(segment, 'name'),
                    _get(segment, 'color'),
                    _get(segment, 'description'),
                    1 if _get(segment, 'is_disabled', False) else 0
                ))

                phrasebooks = _get(segment, 'phrasebooks', []) or []
                for phrasebook in phrasebooks:
                    cursor.execute("""
                        INSERT INTO generation_segment_phrasebook (
                            id, segment_id, generation_id, phrasebook_value_id,
                            category_path, value
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        generate_ulid(),
                        segment_id,
                        generation_id,
                        _get(phrasebook, 'phrasebook_value_id'),
                        _get(phrasebook, 'category_path'),
                        _get(phrasebook, 'value')
                    ))

                cursor.execute("SELECT * FROM generation_segments WHERE id = ?", (segment_id,))
                row = cursor.fetchone()
                if row:
                    created.append(GenerationSegment.from_row(row))

        return created

    def get_by_generation(self, generation_id: str) -> List[GenerationSegment]:
        """Get all segments for a generation, ordered and populated with their phrasebooks."""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM generation_segments
                WHERE generation_id = ?
                ORDER BY channel, prompt_index, segment_index
            """, (generation_id,))
            segments = [GenerationSegment.from_row(row) for row in cursor.fetchall()]

            cursor.execute("""
                SELECT * FROM generation_segment_phrasebook
                WHERE generation_id = ?
            """, (generation_id,))
            phrasebooks_by_segment: Dict[str, List[GenerationSegmentPhrasebook]] = {}
            for row in cursor.fetchall():
                phrasebook = GenerationSegmentPhrasebook.from_row(row)
                phrasebooks_by_segment.setdefault(phrasebook.segment_id, []).append(phrasebook)

            for segment in segments:
                segment.phrasebooks = phrasebooks_by_segment.get(segment.id, [])

            return segments


# Global repository instance
generation_segment_repo = GenerationSegmentRepository()
