from typing import List, Tuple
from src.platform.database import db
from src.features.generation.records import Generation, GenerationModel
from src.features.models.records import Model
from src.platform.util.ids import generate_ulid

class GenerationModelRepository:
    def create(self, generation_model: GenerationModel) -> GenerationModel:
        """Create a new generation-model association"""
        if not generation_model.id:
            generation_model.id = generate_ulid()

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generation_models (
                    id, generation_id, model_id
                ) VALUES (?, ?, ?)
            """, (
                generation_model.id,
                generation_model.generation_id,
                generation_model.model_id
            ))

            cursor.execute("SELECT * FROM generation_models WHERE id = ?", (generation_model.id,))
            row = cursor.fetchone()

            if not row:
                return None

            return GenerationModel.from_row(row)

    def create_batch(self, generation_id: str, model_ids: List[str]) -> List[GenerationModel]:
        """Create multiple generation-model associations"""
        generation_models = []

        with db.get_cursor() as cursor:
            for model_id in model_ids:
                # Check if association already exists
                cursor.execute("""
                    SELECT * FROM generation_models
                    WHERE generation_id = ? AND model_id = ?
                """, (generation_id, model_id))

                existing_row = cursor.fetchone()
                if existing_row:
                    # Association already exists, use it
                    generation_models.append(GenerationModel.from_row(existing_row))
                    continue

                # Create new association
                gm_id = generate_ulid()

                cursor.execute("""
                    INSERT INTO generation_models (
                        id, generation_id, model_id
                    ) VALUES (?, ?, ?)
                """, (gm_id, generation_id, model_id))

                cursor.execute("SELECT * FROM generation_models WHERE id = ?", (gm_id,))
                row = cursor.fetchone()
                if row:
                    generation_models.append(GenerationModel.from_row(row))

        return generation_models

    def get_by_generation(self, generation_id: str, include_model_info: bool = False, include_files: bool = False) -> List[Model]:
        """Get all models for a generation with full model data via JOIN"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT m.* FROM models m
                JOIN generation_models gm ON m.id = gm.model_id
                WHERE gm.generation_id = ?
                ORDER BY gm.created_at
            """, (generation_id,))

            models = [Model.from_row(row) for row in cursor.fetchall()]

            # Load additional data if requested
            if include_model_info or include_files:
                # The MODELS feature's repository, not this module (a
                # self-import here 500'd every get_params call and blanked
                # the history modal's Parameters/Models sections).
                from src.features.models.repository import model_repo
                for model in models:
                    if include_files:
                        model.files = model_repo._get_model_files_with_urls(model.id)

            return models

    def get_generations_by_model(self, model_id: str, user_id: str,
                                limit: int = 50, offset: int = 0) -> Tuple[List[Generation], int]:
        """Get generations that used a specific model, filtered by user.
        Returns a tuple of (generations_list, total_count)."""
        from .file_repository import file_repo

        with db.get_cursor() as cursor:
            # Get total count
            cursor.execute("""
                SELECT COUNT(DISTINCT g.id) FROM generations g
                JOIN generation_models gm ON g.id = gm.generation_id
                WHERE gm.model_id = ? AND g.user_id = ? AND g.status = 'completed'
            """, (model_id, user_id))
            total = cursor.fetchone()[0]

            # Get paginated generations
            cursor.execute("""
                SELECT DISTINCT g.* FROM generations g
                JOIN generation_models gm ON g.id = gm.generation_id
                WHERE gm.model_id = ? AND g.user_id = ? AND g.status = 'completed'
                ORDER BY g.created_at DESC
                LIMIT ? OFFSET ?
            """, (model_id, user_id, limit, offset))

            generations = [Generation.from_row(row) for row in cursor.fetchall()]

            # Load files for each generation
            for gen in generations:
                gen.files = file_repo.get_generation_files(gen.id)

            return generations, total

    def delete_by_generation(self, generation_id: str) -> int:
        """Delete all model associations for a generation"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM generation_models WHERE generation_id = ?",
                (generation_id,)
            )
            return cursor.rowcount

# Global repository instance
generation_model_repo = GenerationModelRepository()
