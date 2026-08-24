import unittest
import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.models.repository import model_repo
from src.features.user_groups.repository import user_group_repo

try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


class TestUserModelAccessControl(PersistenceTestBase):
    """Tests access control integration between model repository and user groups."""

    def setUp(self):
        super().setUp()

        # Patch db references for ALL repos
        import src.features.models.repository
        src.features.models.repository.db = self.db
        import src.features.user_groups.repository
        src.features.user_groups.repository.db = self.db

    def tearDown(self):
        try:
            if hasattr(self, 'db'):
                with self.db.get_cursor() as cursor:
                    cursor.execute("DELETE FROM user_group_models")
                    cursor.execute("DELETE FROM user_group_members")
                    cursor.execute("DELETE FROM user_groups")
                    cursor.execute("DELETE FROM user_models")
                    cursor.execute("DELETE FROM models")
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

    def create_test_model(self, filename="test_model.safetensors", model_type="checkpoint"):
        """Create a test model and return model ID."""
        model_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO models (id, filename, file_path, model_type)
                VALUES (?, ?, ?, ?)
            """, (model_id, filename, f"/models/{filename}", model_type))
        return model_id

    def create_test_group(self, name="testgroup"):
        """Create a test user group and return group_id."""
        group_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_groups (id, name, description)
                VALUES (?, ?, ?)
            """, (group_id, name, f"Test group {name}"))
        return group_id

    def add_user_to_group(self, group_id, user_id):
        """Add user to a group."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_group_members (id, group_id, user_id)
                VALUES (?, ?, ?)
            """, (generate_ulid(), group_id, user_id))

    def assign_model_directly(self, model_id, user_id):
        """Direct assignment via user_models table."""
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO user_models (id, user_id, model_id) VALUES (?, ?, ?)",
                         (generate_ulid(), user_id, model_id))

    def assign_model_to_group(self, group_id, model_id):
        """Group-based assignment via user_group_models table."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_group_models (id, group_id, model_id)
                VALUES (?, ?, ?)
            """, (generate_ulid(), group_id, model_id))

    # Model Access Control Tests
    def test_model_access_via_direct_assignment_only(self):
        """User with only direct model assignment can see it."""
        user_id = self.create_test_user("user_direct")
        model_id = self.create_test_model("direct_model.safetensors", "checkpoint")
        self.assign_model_directly(model_id, user_id)

        # Get available models
        model_ids = model_repo.get_available_model_ids_for_user(user_id)

        self.assertIn(model_id, model_ids)
        self.assertEqual(len(model_ids), 1)

    def test_model_access_via_group_only(self):
        """User with only group-based model assignment can see it."""
        user_id = self.create_test_user("user_group")
        group_id = self.create_test_group("group1")
        model_id = self.create_test_model("group_model.safetensors", "checkpoint")

        # Add user to group and assign model to group
        self.add_user_to_group(group_id, user_id)
        self.assign_model_to_group(group_id, model_id)

        # Get available models
        model_ids = model_repo.get_available_model_ids_for_user(user_id)

        self.assertIn(model_id, model_ids)
        self.assertEqual(len(model_ids), 1)

    def test_model_access_via_both_direct_and_group(self):
        """User with both direct + group access sees model once (no duplicates)."""
        user_id = self.create_test_user("user_both")
        group_id = self.create_test_group("group_both")
        model_id = self.create_test_model("both_model.safetensors", "checkpoint")

        # Assign directly
        self.assign_model_directly(model_id, user_id)

        # Also assign via group
        self.add_user_to_group(group_id, user_id)
        self.assign_model_to_group(group_id, model_id)

        # Get available models
        model_ids = model_repo.get_available_model_ids_for_user(user_id)

        # Should see model only once
        self.assertIn(model_id, model_ids)
        self.assertEqual(model_ids.count(model_id), 1)
        self.assertEqual(len(model_ids), 1)

    def test_model_access_across_multiple_groups(self):
        """User in multiple groups with different models sees all."""
        user_id = self.create_test_user("user_multigroup")
        group1_id = self.create_test_group("group1")
        group2_id = self.create_test_group("group2")

        # Create two models
        model1_id = self.create_test_model("model1.safetensors", "checkpoint")
        model2_id = self.create_test_model("model2.safetensors", "lora")

        # Add user to both groups
        self.add_user_to_group(group1_id, user_id)
        self.add_user_to_group(group2_id, user_id)

        # Assign different models to each group
        self.assign_model_to_group(group1_id, model1_id)
        self.assign_model_to_group(group2_id, model2_id)

        # Get available models
        model_ids = model_repo.get_available_model_ids_for_user(user_id)

        self.assertIn(model1_id, model_ids)
        self.assertIn(model2_id, model_ids)
        self.assertEqual(len(model_ids), 2)

    def test_same_model_in_multiple_groups(self):
        """User in multiple groups with same model sees it once."""
        user_id = self.create_test_user("user_overlap")
        group1_id = self.create_test_group("group_a")
        group2_id = self.create_test_group("group_b")

        # Create one model
        model_id = self.create_test_model("shared_model.safetensors", "checkpoint")

        # Add user to both groups
        self.add_user_to_group(group1_id, user_id)
        self.add_user_to_group(group2_id, user_id)

        # Assign same model to both groups
        self.assign_model_to_group(group1_id, model_id)
        self.assign_model_to_group(group2_id, model_id)

        # Get available models
        model_ids = model_repo.get_available_model_ids_for_user(user_id)

        # Should see model only once
        self.assertIn(model_id, model_ids)
        self.assertEqual(model_ids.count(model_id), 1)
        self.assertEqual(len(model_ids), 1)

    def test_is_model_assigned_direct(self):
        """is_model_assigned_to_user returns True for direct assignment."""
        user_id = self.create_test_user("user_check_direct")
        model_id = self.create_test_model("check_model.safetensors", "checkpoint")
        self.assign_model_directly(model_id, user_id)

        result = model_repo.is_model_assigned_to_user(model_id, user_id)
        self.assertTrue(result)

    def test_is_model_assigned_via_group(self):
        """is_model_assigned_to_user returns True for group assignment."""
        user_id = self.create_test_user("user_check_group")
        group_id = self.create_test_group("check_group")
        model_id = self.create_test_model("checkgroup_model.safetensors", "checkpoint")

        self.add_user_to_group(group_id, user_id)
        self.assign_model_to_group(group_id, model_id)

        result = model_repo.is_model_assigned_to_user(model_id, user_id)
        self.assertTrue(result)

    def test_is_model_not_assigned(self):
        """is_model_assigned_to_user returns False when not assigned at all."""
        user_id = self.create_test_user("user_no_access")
        model_id = self.create_test_model("noaccess_model.safetensors", "checkpoint")

        result = model_repo.is_model_assigned_to_user(model_id, user_id)
        self.assertFalse(result)

    def test_user_not_in_group_only_sees_direct(self):
        """User not in any group only sees direct assignments."""
        user_id = self.create_test_user("user_no_groups")
        group_id = self.create_test_group("other_group")

        # Create models
        model1_id = self.create_test_model("direct_model.safetensors", "checkpoint")
        model2_id = self.create_test_model("group_model.safetensors", "checkpoint")

        # Assign one directly to user
        self.assign_model_directly(model1_id, user_id)

        # Assign another to group (user not member)
        self.assign_model_to_group(group_id, model2_id)

        # User should only see direct assignment
        model_ids = model_repo.get_available_model_ids_for_user(user_id)
        self.assertIn(model1_id, model_ids)
        self.assertNotIn(model2_id, model_ids)
        self.assertEqual(len(model_ids), 1)

    def test_mixed_direct_and_group_access(self):
        """User with some direct and some group assignments sees all correctly."""
        user_id = self.create_test_user("user_mixed")
        group_id = self.create_test_group("mixed_group")

        # Create models
        model1_id = self.create_test_model("direct_model.safetensors", "checkpoint")
        model2_id = self.create_test_model("group_model.safetensors", "lora")

        # Direct assignments
        self.assign_model_directly(model1_id, user_id)

        # Group assignments
        self.add_user_to_group(group_id, user_id)
        self.assign_model_to_group(group_id, model2_id)

        # Check models
        model_ids = model_repo.get_available_model_ids_for_user(user_id)
        self.assertIn(model1_id, model_ids)
        self.assertIn(model2_id, model_ids)
        self.assertEqual(len(model_ids), 2)


class TestModelAssignmentReverseLookup(TestUserModelAccessControl):
    """`get_model_users` (who has this model directly) and
    `get_model_assignment_summary` (per-model direct/group counts), backing
    the admin assignment card and its unassigned badge."""

    def test_get_model_users_returns_only_direct_assignments(self):
        user_id = self.create_test_user("direct_user")
        group_id = self.create_test_group("group1")
        model_id = self.create_test_model("m.safetensors", "checkpoint")
        other_model_id = self.create_test_model("other.safetensors", "checkpoint")

        self.assign_model_directly(model_id, user_id)
        # A group-only assignment on the same model must not appear here -
        # this method answers "who is directly assigned", not "who has access".
        group_user_id = self.create_test_user("group_user")
        self.add_user_to_group(group_id, group_user_id)
        self.assign_model_to_group(group_id, model_id)
        self.assign_model_directly(other_model_id, user_id)

        users = model_repo.get_model_users(model_id)

        self.assertEqual([u.user_id for u in users], [user_id])

    def test_get_model_users_empty_for_unassigned_model(self):
        model_id = self.create_test_model("unassigned.safetensors", "checkpoint")

        self.assertEqual(model_repo.get_model_users(model_id), [])

    def test_get_model_assignment_summary_counts_direct_and_group(self):
        user_a = self.create_test_user("user_a")
        user_b = self.create_test_user("user_b")
        group_id = self.create_test_group("group1")
        assigned_model = self.create_test_model("assigned.safetensors", "checkpoint")
        group_only_model = self.create_test_model("group_only.safetensors", "checkpoint")
        unassigned_model = self.create_test_model("unassigned.safetensors", "checkpoint")

        self.assign_model_directly(assigned_model, user_a)
        self.assign_model_directly(assigned_model, user_b)
        self.add_user_to_group(group_id, user_a)
        self.assign_model_to_group(group_id, group_only_model)

        summary = model_repo.get_model_assignment_summary()

        self.assertEqual(summary[assigned_model], {"assignment_count": 2, "group_count": 0})
        self.assertEqual(summary[group_only_model], {"assignment_count": 0, "group_count": 1})
        self.assertNotIn(unassigned_model, summary)


if __name__ == '__main__':
    unittest.main()
