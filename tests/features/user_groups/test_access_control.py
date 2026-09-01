import unittest
import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.presets.repository import DatabasePresetRepository
from src.features.user_groups.repository import UserGroupRepository
from src.features.llm.repository import LLMRepository

try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


class TestUserGroupAccessControl(PersistenceTestBase):
    """Tests access control integration between repositories and user groups."""

    def setUp(self):
        super().setUp()
        self.preset_repo = DatabasePresetRepository()
        self.group_repo = UserGroupRepository()
        self.llm_repo = LLMRepository()

    def tearDown(self):
        try:
            if hasattr(self, 'db'):
                with self.db.get_cursor() as cursor:
                    cursor.execute("DELETE FROM user_group_llms")
                    cursor.execute("DELETE FROM user_group_presets")
                    cursor.execute("DELETE FROM user_group_members")
                    cursor.execute("DELETE FROM user_groups")
                    cursor.execute("DELETE FROM user_llms")
                    cursor.execute("DELETE FROM user_presets")
                    cursor.execute("DELETE FROM presets")
                    cursor.execute("DELETE FROM llm_configurations")
                    cursor.execute("DELETE FROM users")
        except:
            pass
        super().tearDown()

    # Helper methods
    def create_test_user(self, username="testuser"):
        """Create a test user and return user_id."""
        user_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO users (id, username, email, password_hash)
                VALUES (?, ?, ?, ?)
            """, (user_id, username, f"{username}@test.com", "hash"))
        return user_id

    def create_test_preset(self, preset_id, yaml_preset_id):
        """Create a test preset and return database ID."""
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO presets (id, preset_id) VALUES (?, ?)",
                         (preset_id, yaml_preset_id))
        return preset_id

    def create_test_llm(self, llm_config_id):
        """Create a test LLM configuration and return ID."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO llm_configurations (id, name, type, enabled, base_url, model,
                                               system_message, temperature, max_tokens, timeout)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (llm_config_id, "Test LLM", "openai", 1, "http://localhost",
                 "gpt-4", "test", 0.7, 1000, 30))
        return llm_config_id

    def create_test_group(self, name="testgroup"):
        """Create a test user group and return group_id."""
        group_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_groups (id, name, description)
                VALUES (?, ?, ?)
            """, (group_id, name, f"Test group {name}"))
        return group_id

    def add_user_to_group(self, user_id, group_id):
        """Add user to a group."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_group_members (id, group_id, user_id)
                VALUES (?, ?, ?)
            """, (generate_ulid(), group_id, user_id))

    def assign_preset_directly(self, user_id, preset_db_id):
        """Direct assignment via user_presets table."""
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO user_presets (id, user_id, preset_id) VALUES (?, ?, ?)",
                         (generate_ulid(), user_id, preset_db_id))

    def assign_preset_to_group(self, group_id, preset_db_id):
        """Group-based assignment via user_group_presets table."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_group_presets (id, group_id, preset_id)
                VALUES (?, ?, ?)
            """, (generate_ulid(), group_id, preset_db_id))

    def assign_llm_directly(self, user_id, llm_config_id):
        """Direct assignment via user_llms table."""
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO user_llms (id, user_id, llm_config_id) VALUES (?, ?, ?)",
                         (generate_ulid(), user_id, llm_config_id))

    def assign_llm_to_group(self, group_id, llm_config_id):
        """Group-based assignment via user_group_llms table."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_group_llms (id, group_id, llm_config_id)
                VALUES (?, ?, ?)
            """, (generate_ulid(), group_id, llm_config_id))

    # Preset Access Control Tests
    def test_preset_direct_only(self):
        """User with only direct preset assignment can see it."""
        user_id = self.create_test_user("user_direct")
        preset_db_id = generate_ulid()
        self.create_test_preset(preset_db_id, "test.preset.direct")
        self.assign_preset_directly(user_id, preset_db_id)

        # Get available presets
        preset_ids = self.preset_repo.get_available_preset_ids_for_user(user_id)

        self.assertIn("test.preset.direct", preset_ids)
        self.assertEqual(len(preset_ids), 1)

    def test_preset_group_only(self):
        """User with only group-based preset assignment can see it."""
        user_id = self.create_test_user("user_group")
        group_id = self.create_test_group("group1")
        preset_db_id = generate_ulid()
        self.create_test_preset(preset_db_id, "test.preset.group")

        # Add user to group and assign preset to group
        self.add_user_to_group(user_id, group_id)
        self.assign_preset_to_group(group_id, preset_db_id)

        # Get available presets
        preset_ids = self.preset_repo.get_available_preset_ids_for_user(user_id)

        self.assertIn("test.preset.group", preset_ids)
        self.assertEqual(len(preset_ids), 1)

    def test_preset_both_direct_and_group(self):
        """User with both direct + group access sees preset once (no duplicates)."""
        user_id = self.create_test_user("user_both")
        group_id = self.create_test_group("group_both")
        preset_db_id = generate_ulid()
        self.create_test_preset(preset_db_id, "test.preset.both")

        # Assign directly
        self.assign_preset_directly(user_id, preset_db_id)

        # Also assign via group
        self.add_user_to_group(user_id, group_id)
        self.assign_preset_to_group(group_id, preset_db_id)

        # Get available presets
        preset_ids = self.preset_repo.get_available_preset_ids_for_user(user_id)

        # Should see preset only once
        self.assertIn("test.preset.both", preset_ids)
        self.assertEqual(preset_ids.count("test.preset.both"), 1)
        self.assertEqual(len(preset_ids), 1)

    def test_preset_multiple_groups(self):
        """User in multiple groups with different presets sees all."""
        user_id = self.create_test_user("user_multigroup")
        group1_id = self.create_test_group("group1")
        group2_id = self.create_test_group("group2")

        # Create two presets
        preset1_db_id = generate_ulid()
        preset2_db_id = generate_ulid()
        self.create_test_preset(preset1_db_id, "test.preset.group1")
        self.create_test_preset(preset2_db_id, "test.preset.group2")

        # Add user to both groups
        self.add_user_to_group(user_id, group1_id)
        self.add_user_to_group(user_id, group2_id)

        # Assign different presets to each group
        self.assign_preset_to_group(group1_id, preset1_db_id)
        self.assign_preset_to_group(group2_id, preset2_db_id)

        # Get available presets
        preset_ids = self.preset_repo.get_available_preset_ids_for_user(user_id)

        self.assertIn("test.preset.group1", preset_ids)
        self.assertIn("test.preset.group2", preset_ids)
        self.assertEqual(len(preset_ids), 2)

    def test_preset_multiple_groups_same_preset(self):
        """User in multiple groups with same preset sees it once."""
        user_id = self.create_test_user("user_overlap")
        group1_id = self.create_test_group("group_a")
        group2_id = self.create_test_group("group_b")

        # Create one preset
        preset_db_id = generate_ulid()
        self.create_test_preset(preset_db_id, "test.preset.shared")

        # Add user to both groups
        self.add_user_to_group(user_id, group1_id)
        self.add_user_to_group(user_id, group2_id)

        # Assign same preset to both groups
        self.assign_preset_to_group(group1_id, preset_db_id)
        self.assign_preset_to_group(group2_id, preset_db_id)

        # Get available presets
        preset_ids = self.preset_repo.get_available_preset_ids_for_user(user_id)

        # Should see preset only once
        self.assertIn("test.preset.shared", preset_ids)
        self.assertEqual(preset_ids.count("test.preset.shared"), 1)
        self.assertEqual(len(preset_ids), 1)

    def test_is_preset_assigned_direct(self):
        """is_preset_assigned_to_user returns True for direct assignment."""
        user_id = self.create_test_user("user_check_direct")
        preset_db_id = generate_ulid()
        self.create_test_preset(preset_db_id, "test.preset.check")
        self.assign_preset_directly(user_id, preset_db_id)

        result = self.preset_repo.is_preset_assigned_to_user("test.preset.check", user_id)
        self.assertTrue(result)

    def test_is_preset_assigned_via_group(self):
        """is_preset_assigned_to_user returns True for group assignment."""
        user_id = self.create_test_user("user_check_group")
        group_id = self.create_test_group("check_group")
        preset_db_id = generate_ulid()
        self.create_test_preset(preset_db_id, "test.preset.checkgroup")

        self.add_user_to_group(user_id, group_id)
        self.assign_preset_to_group(group_id, preset_db_id)

        result = self.preset_repo.is_preset_assigned_to_user("test.preset.checkgroup", user_id)
        self.assertTrue(result)
        self.assertFalse(
            self.preset_repo.is_preset_directly_assigned_to_user(
                "test.preset.checkgroup",
                user_id,
            )
        )

    def test_is_preset_not_assigned(self):
        """is_preset_assigned_to_user returns False when not assigned at all."""
        user_id = self.create_test_user("user_no_access")
        preset_db_id = generate_ulid()
        self.create_test_preset(preset_db_id, "test.preset.noaccess")

        result = self.preset_repo.is_preset_assigned_to_user("test.preset.noaccess", user_id)
        self.assertFalse(result)

    def test_direct_assignment_can_be_added_when_group_access_exists(self):
        """Inherited access must not prevent creating a removable direct link."""
        user_id = self.create_test_user("user_inherited_then_direct")
        group_id = self.create_test_group("inherited_group")
        preset_db_id = generate_ulid()
        self.create_test_preset(preset_db_id, "test.preset.inherited")
        self.add_user_to_group(user_id, group_id)
        self.assign_preset_to_group(group_id, preset_db_id)

        assignments = self.preset_repo.assign_preset_to_users(
            "test.preset.inherited",
            [user_id],
        )

        self.assertEqual(len(assignments), 1)
        self.assertTrue(
            self.preset_repo.is_preset_directly_assigned_to_user(
                "test.preset.inherited",
                user_id,
            )
        )
        self.assertEqual(
            self.preset_repo.get_preset_assignment_summary(
                "test.preset.inherited"
            )['total_assignments'],
            1,
        )

    # LLM Access Control Tests
    def test_llm_direct_only(self):
        """User with only direct LLM assignment can see it."""
        user_id = self.create_test_user("user_llm_direct")
        llm_id = generate_ulid()
        self.create_test_llm(llm_id)
        self.assign_llm_directly(user_id, llm_id)

        # Get available LLMs
        llm_ids = self.llm_repo.get_user_llm_assignments(user_id)

        self.assertIn(llm_id, llm_ids)
        self.assertEqual(len(llm_ids), 1)

    def test_llm_group_only(self):
        """User with only group-based LLM assignment can see it."""
        user_id = self.create_test_user("user_llm_group")
        group_id = self.create_test_group("llm_group")
        llm_id = generate_ulid()
        self.create_test_llm(llm_id)

        # Add user to group and assign LLM to group
        self.add_user_to_group(user_id, group_id)
        self.assign_llm_to_group(group_id, llm_id)

        # Get available LLMs
        llm_ids = self.llm_repo.get_user_llm_assignments(user_id)

        self.assertIn(llm_id, llm_ids)
        self.assertEqual(len(llm_ids), 1)

    def test_llm_both_direct_and_group(self):
        """User with both direct + group LLM access sees it once (no duplicates)."""
        user_id = self.create_test_user("user_llm_both")
        group_id = self.create_test_group("llm_group_both")
        llm_id = generate_ulid()
        self.create_test_llm(llm_id)

        # Assign directly
        self.assign_llm_directly(user_id, llm_id)

        # Also assign via group
        self.add_user_to_group(user_id, group_id)
        self.assign_llm_to_group(group_id, llm_id)

        # Get available LLMs
        llm_ids = self.llm_repo.get_user_llm_assignments(user_id)

        # Should see LLM only once
        self.assertIn(llm_id, llm_ids)
        self.assertEqual(llm_ids.count(llm_id), 1)
        self.assertEqual(len(llm_ids), 1)

    def test_llm_multiple_groups(self):
        """User in multiple groups with different LLMs sees all."""
        user_id = self.create_test_user("user_llm_multigroup")
        group1_id = self.create_test_group("llm_group1")
        group2_id = self.create_test_group("llm_group2")

        # Create two LLMs
        llm1_id = generate_ulid()
        llm2_id = generate_ulid()
        self.create_test_llm(llm1_id)
        self.create_test_llm(llm2_id)

        # Add user to both groups
        self.add_user_to_group(user_id, group1_id)
        self.add_user_to_group(user_id, group2_id)

        # Assign different LLMs to each group
        self.assign_llm_to_group(group1_id, llm1_id)
        self.assign_llm_to_group(group2_id, llm2_id)

        # Get available LLMs
        llm_ids = self.llm_repo.get_user_llm_assignments(user_id)

        self.assertIn(llm1_id, llm_ids)
        self.assertIn(llm2_id, llm_ids)
        self.assertEqual(len(llm_ids), 2)

    def test_llm_multiple_groups_same_llm(self):
        """User in multiple groups with same LLM sees it once."""
        user_id = self.create_test_user("user_llm_overlap")
        group1_id = self.create_test_group("llm_group_a")
        group2_id = self.create_test_group("llm_group_b")

        # Create one LLM
        llm_id = generate_ulid()
        self.create_test_llm(llm_id)

        # Add user to both groups
        self.add_user_to_group(user_id, group1_id)
        self.add_user_to_group(user_id, group2_id)

        # Assign same LLM to both groups
        self.assign_llm_to_group(group1_id, llm_id)
        self.assign_llm_to_group(group2_id, llm_id)

        # Get available LLMs
        llm_ids = self.llm_repo.get_user_llm_assignments(user_id)

        # Should see LLM only once
        self.assertIn(llm_id, llm_ids)
        self.assertEqual(llm_ids.count(llm_id), 1)
        self.assertEqual(len(llm_ids), 1)

    def test_is_llm_assigned_direct(self):
        """is_llm_assigned_to_user returns True for direct assignment."""
        user_id = self.create_test_user("user_llm_check_direct")
        llm_id = generate_ulid()
        self.create_test_llm(llm_id)
        self.assign_llm_directly(user_id, llm_id)

        result = self.llm_repo.is_llm_assigned_to_user(user_id, llm_id)
        self.assertTrue(result)

    def test_is_llm_assigned_via_group(self):
        """is_llm_assigned_to_user returns True for group assignment."""
        user_id = self.create_test_user("user_llm_check_group")
        group_id = self.create_test_group("llm_check_group")
        llm_id = generate_ulid()
        self.create_test_llm(llm_id)

        self.add_user_to_group(user_id, group_id)
        self.assign_llm_to_group(group_id, llm_id)

        result = self.llm_repo.is_llm_assigned_to_user(user_id, llm_id)
        self.assertTrue(result)

    def test_is_llm_not_assigned(self):
        """is_llm_assigned_to_user returns False when not assigned at all."""
        user_id = self.create_test_user("user_llm_no_access")
        llm_id = generate_ulid()
        self.create_test_llm(llm_id)

        result = self.llm_repo.is_llm_assigned_to_user(user_id, llm_id)
        self.assertFalse(result)

    # Combined access tests
    def test_user_with_mixed_direct_and_group_access(self):
        """User with some direct and some group assignments sees all correctly."""
        user_id = self.create_test_user("user_mixed")
        group_id = self.create_test_group("mixed_group")

        # Create presets and LLMs
        preset1_db_id = generate_ulid()
        preset2_db_id = generate_ulid()
        llm1_id = generate_ulid()
        llm2_id = generate_ulid()

        self.create_test_preset(preset1_db_id, "test.preset.direct")
        self.create_test_preset(preset2_db_id, "test.preset.group")
        self.create_test_llm(llm1_id)
        self.create_test_llm(llm2_id)

        # Direct assignments
        self.assign_preset_directly(user_id, preset1_db_id)
        self.assign_llm_directly(user_id, llm1_id)

        # Group assignments
        self.add_user_to_group(user_id, group_id)
        self.assign_preset_to_group(group_id, preset2_db_id)
        self.assign_llm_to_group(group_id, llm2_id)

        # Check presets
        preset_ids = self.preset_repo.get_available_preset_ids_for_user(user_id)
        self.assertIn("test.preset.direct", preset_ids)
        self.assertIn("test.preset.group", preset_ids)
        self.assertEqual(len(preset_ids), 2)

        # Check LLMs
        llm_ids = self.llm_repo.get_user_llm_assignments(user_id)
        self.assertIn(llm1_id, llm_ids)
        self.assertIn(llm2_id, llm_ids)
        self.assertEqual(len(llm_ids), 2)

    def test_user_not_in_any_group(self):
        """User not in any group only sees direct assignments."""
        user_id = self.create_test_user("user_no_groups")
        group_id = self.create_test_group("other_group")

        # Create resources
        preset1_db_id = generate_ulid()
        preset2_db_id = generate_ulid()
        self.create_test_preset(preset1_db_id, "test.preset.direct")
        self.create_test_preset(preset2_db_id, "test.preset.group")

        # Assign one directly to user
        self.assign_preset_directly(user_id, preset1_db_id)

        # Assign another to group (user not member)
        self.assign_preset_to_group(group_id, preset2_db_id)

        # User should only see direct assignment
        preset_ids = self.preset_repo.get_available_preset_ids_for_user(user_id)
        self.assertIn("test.preset.direct", preset_ids)
        self.assertNotIn("test.preset.group", preset_ids)
        self.assertEqual(len(preset_ids), 1)


if __name__ == '__main__':
    unittest.main()
