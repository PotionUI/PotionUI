from typing import List, Optional

from src.platform.util.ids import generate_ulid

from src.platform.database import db
from src.features.prompt_enhancement.records import EnhancementFeedback


class EnhancementFeedbackRepository:
    def create(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        prompt_text: str,
        verdict: str,
        model_id: Optional[str] = None,
        reason: Optional[str] = None,
        prompt_id: Optional[str] = None,
        mode: str = 'generation',
    ) -> EnhancementFeedback:
        """Persist a feedback verdict for an enhancement-proposed prompt."""
        feedback_id = generate_ulid()
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO enhancement_feedback
                    (id, user_id, session_id, message_id, prompt_text, verdict, model_id, reason, prompt_id, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                feedback_id, user_id, session_id, message_id,
                prompt_text, verdict, model_id, reason, prompt_id, mode,
            ))
        return self.get_by_id(feedback_id, user_id)

    def get_by_id(self, id: str, user_id: str) -> Optional[EnhancementFeedback]:
        """Get a feedback row by ID scoped to user."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM enhancement_feedback WHERE id = ? AND user_id = ?",
                (id, user_id)
            )
            row = cursor.fetchone()
            return EnhancementFeedback.from_row(row) if row else None

    def list_feedback(
        self,
        user_id: str,
        model_id: Optional[str] = None,
        verdict: Optional[str] = None,
        limit: int = 50,
    ) -> List[EnhancementFeedback]:
        """List feedback rows with optional filters, newest first."""
        where_clauses = ["user_id = ?"]
        params: list = [user_id]

        if model_id is not None:
            where_clauses.append("model_id = ?")
            params.append(model_id)

        if verdict is not None:
            where_clauses.append("verdict = ?")
            params.append(verdict)

        params.append(limit)
        query = (
            f"SELECT * FROM enhancement_feedback"
            f" WHERE {' AND '.join(where_clauses)}"
            f" ORDER BY created_at DESC LIMIT ?"
        )

        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [EnhancementFeedback.from_row(row) for row in cursor.fetchall()]

    def get_recent_rejection_reasons(
        self,
        user_id: str,
        model_id: Optional[str] = None,
        limit: int = 5,
    ) -> List[str]:
        """Most recent non-empty rejection reasons, optionally scoped to a model."""
        rows = self.list_feedback(
            user_id=user_id, model_id=model_id, verdict="rejected", limit=limit * 3,
        )
        reasons = [r.reason.strip() for r in rows if r.reason and r.reason.strip()]
        return reasons[:limit]
