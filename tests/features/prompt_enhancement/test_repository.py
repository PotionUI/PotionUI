import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.prompt_enhancement.repository import EnhancementFeedbackRepository


class TestEnhancementFeedbackModePersistence(PersistenceTestBase):
    """Mode column persistence on enhancement_feedback (migration 057)."""

    def setUp(self):
        super().setUp()
        self.repo = EnhancementFeedbackRepository()

        # Patch the db reference in the repository module
        import src.features.prompt_enhancement.repository
        src.features.prompt_enhancement.repository.db = self.db

        self.test_user_id = self.create_test_user()

    def tearDown(self):
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute("DELETE FROM enhancement_feedback")
        except Exception:
            pass
        super().tearDown()

    def _create(self, verdict="approved", mode="generation", reason=None):
        return self.repo.create(
            user_id=self.test_user_id,
            session_id="s-1",
            message_id="m-1",
            prompt_text="a prompt",
            verdict=verdict,
            reason=reason,
            mode=mode,
        )

    def test_mode_persisted_and_read_back(self):
        row = self._create(mode="dataset")
        self.assertEqual(row.mode, "dataset")
        fetched = self.repo.get_by_id(row.id, self.test_user_id)
        self.assertEqual(fetched.mode, "dataset")

    def test_mode_defaults_to_generation(self):
        row = self.repo.create(
            user_id=self.test_user_id,
            session_id="s-1",
            message_id="m-1",
            prompt_text="a prompt",
            verdict="rejected",
            reason="too dark",
        )
        self.assertEqual(row.mode, "generation")

    def test_mode_in_to_dict(self):
        row = self._create(mode="dataset")
        self.assertEqual(row.to_dict()["mode"], "dataset")
