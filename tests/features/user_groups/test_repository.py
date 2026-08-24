import unittest
import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.user_groups.repository import UserGroupRepository

try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


class TestUserGroupRepository(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repo = UserGroupRepository()
        import src.features.user_groups.repository
        src.features.user_groups.repository.db = self.db
        # Migration 095 seeds the built-in All Users/All Admins groups onto
        # every fresh schema - clear them so this file's count-based
        # assertions start from an empty table, as they did before that
        # migration existed.
        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM user_groups")
        # Create test users
        self.user1_id = self.create_test_user("user-1", "user1", "user1@test.com")
        self.user2_id = self.create_test_user("user-2", "user2", "user2@test.com")

    def tearDown(self):
        try:
            if hasattr(self, 'db'):
                with self.db.get_cursor() as cursor:
                    cursor.execute("DELETE FROM user_group_llms")
                    cursor.execute("DELETE FROM user_group_presets")
                    cursor.execute("DELETE FROM user_group_members")
                    cursor.execute("DELETE FROM user_groups")
                    cursor.execute("DELETE FROM users")
                    cursor.execute("DELETE FROM llm_configurations")
                    cursor.execute("DELETE FROM presets")
        except:
            pass
        super().tearDown()

    def create_test_preset(self, preset_id="test-preset-1", yaml_preset_id="test/preset/1"):
        """Helper to create a test preset."""
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO presets (id, preset_id) VALUES (?, ?)", (preset_id, yaml_preset_id))
        return preset_id

    def create_test_llm(self, llm_config_id="test-llm-1"):
        """Helper to create a test LLM configuration."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO llm_configurations (id, name, type, enabled, base_url, model, system_message, temperature, max_tokens, timeout)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (llm_config_id, "Test LLM", "openai", 1, "http://localhost", "gpt-4", "test", 0.7, 1000, 30))
        return llm_config_id

    # Group CRUD Tests

    def test_create_group(self):
        """Test creating a basic group."""
        group = self.repo.create_group("Test Group", "Test description")
        self.assertIsNotNone(group)
        self.assertEqual(group.name, "Test Group")
        self.assertEqual(group.description, "Test description")
        self.assertIsNotNone(group.id)
        self.assertIsNotNone(group.created_at)

    def test_create_group_with_description(self):
        """Test creating a group with optional description."""
        group = self.repo.create_group("No Description Group")
        self.assertIsNotNone(group)
        self.assertEqual(group.name, "No Description Group")
        self.assertIsNone(group.description)

    def test_create_group_defaults_is_system_false(self):
        """Groups created through the repository are never built-in/system groups."""
        group = self.repo.create_group("Test Group")
        self.assertFalse(group.is_system)

    def test_get_group_by_id_reads_is_system_flag(self):
        """is_system round-trips from the row (create_group never sets it - only
        migration 095's seed rows do)."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_groups (id, name, is_system) VALUES (?, ?, 1)",
                ("sys-group", "System Group"),
            )
        group = self.repo.get_group_by_id("sys-group")
        self.assertTrue(group.is_system)

    def test_get_group_by_id(self):
        """Test retrieving a group by ID."""
        created_group = self.repo.create_group("Test Group", "Description")
        retrieved_group = self.repo.get_group_by_id(created_group.id)
        self.assertIsNotNone(retrieved_group)
        self.assertEqual(retrieved_group.id, created_group.id)
        self.assertEqual(retrieved_group.name, "Test Group")
        self.assertEqual(retrieved_group.description, "Description")

    def test_get_group_by_id_not_found(self):
        """Test retrieving a non-existent group returns None."""
        group = self.repo.get_group_by_id("nonexistent-id")
        self.assertIsNone(group)

    def test_get_group_by_name(self):
        """Test retrieving a group by name."""
        self.repo.create_group("Unique Name", "Description")
        group = self.repo.get_group_by_name("Unique Name")
        self.assertIsNotNone(group)
        self.assertEqual(group.name, "Unique Name")
        self.assertEqual(group.description, "Description")

    def test_get_all_groups(self):
        """Test retrieving all groups ordered by name."""
        self.repo.create_group("Zebra Group")
        self.repo.create_group("Alpha Group")
        self.repo.create_group("Beta Group")

        groups = self.repo.get_all_groups()
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].name, "Alpha Group")
        self.assertEqual(groups[1].name, "Beta Group")
        self.assertEqual(groups[2].name, "Zebra Group")

    def test_update_group_name(self):
        """Test updating only the group name."""
        group = self.repo.create_group("Old Name", "Description")
        updated_group = self.repo.update_group(group.id, name="New Name")
        self.assertIsNotNone(updated_group)
        self.assertEqual(updated_group.name, "New Name")
        self.assertEqual(updated_group.description, "Description")

    def test_update_group_description(self):
        """Test updating only the group description."""
        group = self.repo.create_group("Name", "Old Description")
        updated_group = self.repo.update_group(group.id, description="New Description")
        self.assertIsNotNone(updated_group)
        self.assertEqual(updated_group.name, "Name")
        self.assertEqual(updated_group.description, "New Description")

    def test_delete_group(self):
        """Test deleting a group."""
        group = self.repo.create_group("To Delete")
        self.assertTrue(self.repo.delete_group(group.id))
        deleted_group = self.repo.get_group_by_id(group.id)
        self.assertIsNone(deleted_group)

    def test_create_duplicate_name_fails(self):
        """Test that creating a group with duplicate name fails."""
        self.repo.create_group("Duplicate Name")
        with self.assertRaises(Exception):
            self.repo.create_group("Duplicate Name")

    # Member Management Tests

    def test_add_user_to_group(self):
        """Test adding a user to a group."""
        group = self.repo.create_group("Test Group")
        membership = self.repo.add_user_to_group(group.id, self.user1_id)
        self.assertIsNotNone(membership)
        self.assertEqual(membership.group_id, group.id)
        self.assertEqual(membership.user_id, self.user1_id)
        self.assertIsNotNone(membership.assigned_at)

    def test_remove_user_from_group(self):
        """Test removing a user from a group."""
        group = self.repo.create_group("Test Group")
        self.repo.add_user_to_group(group.id, self.user1_id)
        self.assertTrue(self.repo.remove_user_from_group(group.id, self.user1_id))
        self.assertFalse(self.repo.is_user_in_group(group.id, self.user1_id))

    def test_get_group_members(self):
        """Test retrieving all members of a group."""
        group = self.repo.create_group("Test Group")
        self.repo.add_user_to_group(group.id, self.user1_id)
        self.repo.add_user_to_group(group.id, self.user2_id)

        members = self.repo.get_group_members(group.id)
        self.assertEqual(len(members), 2)
        member_ids = [m.user_id for m in members]
        self.assertIn(self.user1_id, member_ids)
        self.assertIn(self.user2_id, member_ids)

    def test_get_user_groups(self):
        """Test retrieving all groups a user belongs to."""
        group1 = self.repo.create_group("Group 1")
        group2 = self.repo.create_group("Group 2")
        self.repo.add_user_to_group(group1.id, self.user1_id)
        self.repo.add_user_to_group(group2.id, self.user1_id)

        groups = self.repo.get_user_groups(self.user1_id)
        self.assertEqual(len(groups), 2)
        group_ids = [g.id for g in groups]
        self.assertIn(group1.id, group_ids)
        self.assertIn(group2.id, group_ids)

    def test_is_user_in_group(self):
        """Test checking if a user is in a group."""
        group = self.repo.create_group("Test Group")
        self.assertFalse(self.repo.is_user_in_group(group.id, self.user1_id))

        self.repo.add_user_to_group(group.id, self.user1_id)
        self.assertTrue(self.repo.is_user_in_group(group.id, self.user1_id))
        self.assertFalse(self.repo.is_user_in_group(group.id, self.user2_id))

    def test_add_duplicate_member(self):
        """Test that adding a duplicate member returns None."""
        group = self.repo.create_group("Test Group")
        self.repo.add_user_to_group(group.id, self.user1_id)
        duplicate = self.repo.add_user_to_group(group.id, self.user1_id)
        self.assertIsNone(duplicate)

    # Preset Assignment Tests

    def test_assign_preset_to_group(self):
        """Test assigning a preset to a group."""
        group = self.repo.create_group("Test Group")
        preset_id = self.create_test_preset()

        assignment = self.repo.assign_preset_to_group(group.id, preset_id)
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.group_id, group.id)
        self.assertEqual(assignment.preset_id, preset_id)
        self.assertIsNotNone(assignment.assigned_at)

    def test_assign_preset_to_group_accepts_public_preset_id(self):
        """Public YAML IDs are resolved to the installed preset foreign key."""
        group = self.repo.create_group("Test Group")
        preset_db_id = self.create_test_preset(
            preset_id="preset-db-1",
            yaml_preset_id="native/example/standard",
        )

        assignment = self.repo.assign_preset_to_group(
            group.id,
            "native/example/standard",
        )

        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.preset_id, preset_db_id)

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT preset_id FROM user_group_presets WHERE id = ?",
                (assignment.id,),
            )
            self.assertEqual(cursor.fetchone()['preset_id'], preset_db_id)

    def test_unassign_preset_from_group(self):
        """Test unassigning a preset from a group."""
        group = self.repo.create_group("Test Group")
        preset_id = self.create_test_preset()
        self.repo.assign_preset_to_group(group.id, preset_id)

        self.assertTrue(self.repo.unassign_preset_from_group(group.id, preset_id))
        presets = self.repo.get_group_presets(group.id)
        self.assertEqual(len(presets), 0)

    def test_unassign_preset_from_group_accepts_public_preset_id(self):
        """The public preset ID can remove a relationship stored by DB ID."""
        group = self.repo.create_group("Test Group")
        preset_db_id = self.create_test_preset(
            preset_id="preset-db-1",
            yaml_preset_id="native/example/standard",
        )
        self.repo.assign_preset_to_group(group.id, preset_db_id)

        self.assertTrue(
            self.repo.unassign_preset_from_group(
                group.id,
                "native/example/standard",
            )
        )
        self.assertEqual(self.repo.get_group_presets(group.id), [])

    def test_get_group_presets(self):
        """Test retrieving all presets assigned to a group."""
        group = self.repo.create_group("Test Group")
        preset1_id = self.create_test_preset("preset-1", "test/preset/1")
        preset2_id = self.create_test_preset("preset-2", "test/preset/2")

        self.repo.assign_preset_to_group(group.id, preset1_id)
        self.repo.assign_preset_to_group(group.id, preset2_id)

        presets = self.repo.get_group_presets(group.id)
        self.assertEqual(len(presets), 2)
        preset_ids = [p.preset_id for p in presets]
        self.assertIn(preset1_id, preset_ids)
        self.assertIn(preset2_id, preset_ids)

    def test_get_groups_for_preset_accepts_public_and_database_ids(self):
        """Resource lookups preserve old DB-ID callers and support public IDs."""
        group = self.repo.create_group("Test Group")
        preset_db_id = self.create_test_preset(
            preset_id="preset-db-1",
            yaml_preset_id="native/example/standard",
        )
        assignment = self.repo.assign_preset_to_group(group.id, preset_db_id)

        by_public_id = self.repo.get_groups_for_preset("native/example/standard")
        by_database_id = self.repo.get_groups_for_preset(preset_db_id)

        self.assertEqual([item.id for item in by_public_id], [assignment.id])
        self.assertEqual([item.id for item in by_database_id], [assignment.id])

    def test_unknown_preset_ids_are_safe_noops(self):
        """Missing presets cannot create dangling group relationships."""
        group = self.repo.create_group("Test Group")

        self.assertIsNone(
            self.repo.assign_preset_to_group(group.id, "missing-preset")
        )
        self.assertFalse(
            self.repo.unassign_preset_from_group(group.id, "missing-preset")
        )
        self.assertEqual(self.repo.get_groups_for_preset("missing-preset"), [])
        self.assertEqual(
            self.repo.get_group_count_for_preset("missing-preset"),
            0,
        )

    def test_assign_duplicate_preset(self):
        """Test that assigning a duplicate preset returns None."""
        group = self.repo.create_group("Test Group")
        preset_id = self.create_test_preset()
        self.repo.assign_preset_to_group(group.id, preset_id)

        duplicate = self.repo.assign_preset_to_group(group.id, preset_id)
        self.assertIsNone(duplicate)

    # LLM Assignment Tests

    def test_assign_llm_to_group(self):
        """Test assigning an LLM to a group."""
        group = self.repo.create_group("Test Group")
        llm_id = self.create_test_llm()

        assignment = self.repo.assign_llm_to_group(group.id, llm_id)
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.group_id, group.id)
        self.assertEqual(assignment.llm_config_id, llm_id)
        self.assertIsNotNone(assignment.assigned_at)

    def test_unassign_llm_from_group(self):
        """Test unassigning an LLM from a group."""
        group = self.repo.create_group("Test Group")
        llm_id = self.create_test_llm()
        self.repo.assign_llm_to_group(group.id, llm_id)

        self.assertTrue(self.repo.unassign_llm_from_group(group.id, llm_id))
        llms = self.repo.get_group_llms(group.id)
        self.assertEqual(len(llms), 0)

    def test_get_group_llms(self):
        """Test retrieving all LLMs assigned to a group."""
        group = self.repo.create_group("Test Group")
        llm1_id = self.create_test_llm("llm-1")
        llm2_id = self.create_test_llm("llm-2")

        self.repo.assign_llm_to_group(group.id, llm1_id)
        self.repo.assign_llm_to_group(group.id, llm2_id)

        llms = self.repo.get_group_llms(group.id)
        self.assertEqual(len(llms), 2)
        llm_ids = [l.llm_config_id for l in llms]
        self.assertIn(llm1_id, llm_ids)
        self.assertIn(llm2_id, llm_ids)

    def test_assign_duplicate_llm(self):
        """Test that assigning a duplicate LLM returns None."""
        group = self.repo.create_group("Test Group")
        llm_id = self.create_test_llm()
        self.repo.assign_llm_to_group(group.id, llm_id)

        duplicate = self.repo.assign_llm_to_group(group.id, llm_id)
        self.assertIsNone(duplicate)

    # Cascade Delete Tests

    def test_delete_group_cascades_members(self):
        """Test that deleting a group removes its members."""
        group = self.repo.create_group("Test Group")
        self.repo.add_user_to_group(group.id, self.user1_id)
        self.repo.add_user_to_group(group.id, self.user2_id)

        self.repo.delete_group(group.id)

        # Verify members are removed
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM user_group_members WHERE group_id = ?", (group.id,))
            count = cursor.fetchone()[0]
            self.assertEqual(count, 0)

    def test_delete_group_cascades_presets(self):
        """Test that deleting a group removes preset assignments."""
        group = self.repo.create_group("Test Group")
        preset_id = self.create_test_preset()
        self.repo.assign_preset_to_group(group.id, preset_id)

        self.repo.delete_group(group.id)

        # Verify preset assignments are removed
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM user_group_presets WHERE group_id = ?", (group.id,))
            count = cursor.fetchone()[0]
            self.assertEqual(count, 0)

    def test_delete_group_cascades_llms(self):
        """Test that deleting a group removes LLM assignments."""
        group = self.repo.create_group("Test Group")
        llm_id = self.create_test_llm()
        self.repo.assign_llm_to_group(group.id, llm_id)

        self.repo.delete_group(group.id)

        # Verify LLM assignments are removed
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM user_group_llms WHERE group_id = ?", (group.id,))
            count = cursor.fetchone()[0]
            self.assertEqual(count, 0)

    # Count Methods Tests

    def test_get_group_member_count(self):
        """Test getting the count of members in a group."""
        group = self.repo.create_group("Test Group")
        self.assertEqual(self.repo.get_group_member_count(group.id), 0)

        self.repo.add_user_to_group(group.id, self.user1_id)
        self.assertEqual(self.repo.get_group_member_count(group.id), 1)

        self.repo.add_user_to_group(group.id, self.user2_id)
        self.assertEqual(self.repo.get_group_member_count(group.id), 2)

    def test_get_group_preset_count(self):
        """Test getting the count of presets assigned to a group."""
        group = self.repo.create_group("Test Group")
        self.assertEqual(self.repo.get_group_preset_count(group.id), 0)

        preset1_id = self.create_test_preset("preset-1", "test/preset/1")
        self.repo.assign_preset_to_group(group.id, preset1_id)
        self.assertEqual(self.repo.get_group_preset_count(group.id), 1)

        preset2_id = self.create_test_preset("preset-2", "test/preset/2")
        self.repo.assign_preset_to_group(group.id, preset2_id)
        self.assertEqual(self.repo.get_group_preset_count(group.id), 2)

    def test_get_group_count_for_preset_accepts_public_preset_id(self):
        """Resource-centric counts resolve the public preset ID."""
        group = self.repo.create_group("Test Group")
        preset_db_id = self.create_test_preset(
            preset_id="preset-db-1",
            yaml_preset_id="native/example/standard",
        )
        self.repo.assign_preset_to_group(group.id, preset_db_id)

        self.assertEqual(
            self.repo.get_group_count_for_preset("native/example/standard"),
            1,
        )

    def test_get_group_llm_count(self):
        """Test getting the count of LLMs assigned to a group."""
        group = self.repo.create_group("Test Group")
        self.assertEqual(self.repo.get_group_llm_count(group.id), 0)

        llm1_id = self.create_test_llm("llm-1")
        self.repo.assign_llm_to_group(group.id, llm1_id)
        self.assertEqual(self.repo.get_group_llm_count(group.id), 1)

        llm2_id = self.create_test_llm("llm-2")
        self.repo.assign_llm_to_group(group.id, llm2_id)
        self.assertEqual(self.repo.get_group_llm_count(group.id), 2)


if __name__ == '__main__':
    unittest.main()
