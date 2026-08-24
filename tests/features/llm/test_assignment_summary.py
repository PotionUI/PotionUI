"""`LLMRepository.get_llm_assignment_summary` against a real database - per-
config direct/group assignment counts, backing the admin assignment card and
its unassigned badge."""
import unittest
import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.llm.repository import LLMRepository
from src.platform.util.ids import generate_ulid


class TestLLMAssignmentSummary(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        import src.features.llm.repository as llm_repository_module
        llm_repository_module.db = self.db
        self.repository = LLMRepository()

    def tearDown(self):
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute("DELETE FROM user_group_llms")
                cursor.execute("DELETE FROM user_llms")
                cursor.execute("DELETE FROM user_group_members")
                cursor.execute("DELETE FROM user_groups")
                cursor.execute("DELETE FROM llm_configurations")
                cursor.execute("DELETE FROM users")
        except Exception:
            pass
        super().tearDown()

    def _create_user(self, username="testuser"):
        user_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
                (user_id, username, f"{username}@test.com", "hash")
            )
        return user_id

    def _create_config(self, name="test-config"):
        config_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """INSERT INTO llm_configurations
                   (id, name, type, base_url, model, system_message)
                   VALUES (?, ?, 'openai', 'https://api.test', 'gpt-4', 'system')""",
                (config_id, name)
            )
        return config_id

    def _create_group(self, name="testgroup"):
        group_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_groups (id, name, description) VALUES (?, ?, ?)",
                (group_id, name, f"Test group {name}")
            )
        return group_id

    def _assign_direct(self, user_id, config_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_llms (id, user_id, llm_config_id) VALUES (?, ?, ?)",
                (generate_ulid(), user_id, config_id)
            )

    def _assign_group(self, group_id, config_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_group_llms (id, group_id, llm_config_id) VALUES (?, ?, ?)",
                (generate_ulid(), group_id, config_id)
            )

    def test_summary_counts_direct_and_group_assignments(self):
        user_a = self._create_user("user_a")
        user_b = self._create_user("user_b")
        group_id = self._create_group("group1")
        assigned_config = self._create_config("assigned")
        group_only_config = self._create_config("group_only")
        unassigned_config = self._create_config("unassigned")

        self._assign_direct(user_a, assigned_config)
        self._assign_direct(user_b, assigned_config)
        self._assign_group(group_id, group_only_config)

        summary = self.repository.get_llm_assignment_summary()

        self.assertEqual(summary[assigned_config], {"assignment_count": 2, "group_count": 0})
        self.assertEqual(summary[group_only_config], {"assignment_count": 0, "group_count": 1})
        self.assertNotIn(unassigned_config, summary)

    def test_summary_empty_when_no_assignments(self):
        self._create_config("lonely")

        self.assertEqual(self.repository.get_llm_assignment_summary(), {})


if __name__ == '__main__':
    unittest.main()
