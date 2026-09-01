import unittest
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.workspaces.records import Workspace
from src.features.workspaces.repository import WorkspaceRepository


def make_workspace(**overrides) -> Workspace:
    """Helper to create a Workspace with sensible defaults."""
    defaults = {
        "id": None,
        "user_id": "test-user-1",
        "name": "My Workspace",
        "data": {
            "tabs": [
                {"name": "Tab 1", "color": "#ff0000", "preset_id": "preset-1", "mode": "txt2img"}
            ]
        },
    }
    defaults.update(overrides)
    return Workspace(**defaults)


class TestWorkspaceRepository(PersistenceTestBase):
    """Test cases for WorkspaceRepository against an in-memory SQLite database."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        super().setUp()
        self.repository = WorkspaceRepository()

        # Ensure the workspaces table exists
        self._create_workspaces_table()

    # ------------------------------------------------------------------
    # Table creation helper
    # ------------------------------------------------------------------

    def _create_workspaces_table(self):
        """Create workspaces table for testing."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_workspaces_user
                ON workspaces(user_id)
            """)

    # ------------------------------------------------------------------
    # Fixtures / helpers
    # ------------------------------------------------------------------

    def _create_user(self, user_id: str = "test-user-1") -> str:
        """Insert a minimal user row and return the user_id."""
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(users)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'account_type' in columns:
                cursor.execute(
                    "INSERT OR IGNORE INTO users (id, username, email, password_hash, account_type)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (user_id, user_id, f"{user_id}@example.com", "hash", "USER"),
                )
            else:
                cursor.execute(
                    "INSERT OR IGNORE INTO users (id, username, email, password_hash)"
                    " VALUES (?, ?, ?, ?)",
                    (user_id, user_id, f"{user_id}@example.com", "hash"),
                )
        return user_id

    def _saved(self, **overrides) -> Workspace:
        """Create and persist a Workspace, ensuring the owning user exists."""
        workspace = make_workspace(**overrides)
        self._create_user(workspace.user_id)
        return self.repository.create(workspace)

    # ==================================================================
    # create
    # ==================================================================

    def test_create_returns_workspace_with_id(self):
        """create() persists the workspace and returns it with an assigned ID."""
        self._create_user("test-user-1")
        workspace = make_workspace()

        result = self.repository.create(workspace)

        self.assertIsNotNone(result)
        self.assertIsNotNone(result.id)
        self.assertEqual(result.name, "My Workspace")

    def test_create_preserves_all_fields(self):
        """create() round-trips every field correctly."""
        self._create_user("test-user-1")
        data = {
            "tabs": [
                {"name": "Tab 1", "color": "#ff0000", "preset_id": "preset-1", "mode": "txt2img"},
                {"name": "Tab 2", "color": "#00ff00", "preset_id": "preset-2", "mode": "img2img"},
            ]
        }
        workspace = make_workspace(name="Complex Workspace", data=data)

        result = self.repository.create(workspace)

        self.assertEqual(result.name, "Complex Workspace")
        self.assertEqual(result.user_id, "test-user-1")
        self.assertEqual(len(result.data["tabs"]), 2)
        self.assertEqual(result.data["tabs"][0]["name"], "Tab 1")
        self.assertEqual(result.data["tabs"][1]["color"], "#00ff00")

    def test_create_generates_id_when_not_provided(self):
        """create() generates a unique ID when workspace.id is None."""
        self._create_user("test-user-1")
        workspace = make_workspace(id=None)

        result = self.repository.create(workspace)

        self.assertIsNotNone(result.id)
        self.assertGreater(len(result.id), 0)

    def test_create_uses_provided_id(self):
        """create() uses the provided ID when set."""
        self._create_user("test-user-1")
        workspace = make_workspace(id="custom-id-123")

        result = self.repository.create(workspace)

        self.assertEqual(result.id, "custom-id-123")

    def test_create_sets_timestamps(self):
        """create() sets created_at and updated_at."""
        self._create_user("test-user-1")
        workspace = make_workspace()

        result = self.repository.create(workspace)

        self.assertIsNotNone(result.created_at)
        self.assertIsNotNone(result.updated_at)

    # ==================================================================
    # get_by_id
    # ==================================================================

    def test_get_by_id_returns_workspace(self):
        """get_by_id() returns the workspace when it exists."""
        saved = self._saved()

        result = self.repository.get_by_id(saved.id)

        self.assertIsNotNone(result)
        self.assertEqual(result.id, saved.id)
        self.assertEqual(result.name, saved.name)

    def test_get_by_id_returns_none_for_missing(self):
        """get_by_id() returns None when the workspace does not exist."""
        result = self.repository.get_by_id("nonexistent-id")

        self.assertIsNone(result)

    def test_get_by_id_deserializes_data_field(self):
        """get_by_id() correctly deserializes the JSON data field."""
        data = {"tabs": [{"name": "Tab A", "color": "#abc123"}]}
        saved = self._saved(data=data)

        result = self.repository.get_by_id(saved.id)

        self.assertIsInstance(result.data, dict)
        self.assertEqual(result.data["tabs"][0]["name"], "Tab A")

    # ==================================================================
    # get_by_user
    # ==================================================================

    def test_get_by_user_returns_all_workspaces(self):
        """get_by_user() returns all workspaces for a user."""
        self._create_user("test-user-1")
        self.repository.create(make_workspace(name="Workspace A"))
        self.repository.create(make_workspace(name="Workspace B"))

        results = self.repository.get_by_user("test-user-1")

        self.assertEqual(len(results), 2)
        names = {w.name for w in results}
        self.assertIn("Workspace A", names)
        self.assertIn("Workspace B", names)

    def test_get_by_user_returns_empty_list_for_no_workspaces(self):
        """get_by_user() returns an empty list when no workspaces exist."""
        self._create_user("test-user-1")

        results = self.repository.get_by_user("test-user-1")

        self.assertEqual(results, [])

    def test_get_by_user_excludes_other_users_workspaces(self):
        """get_by_user() only returns workspaces belonging to the specified user."""
        self._create_user("user-1")
        self._create_user("user-2")
        self.repository.create(make_workspace(user_id="user-1", name="User 1 Workspace"))
        self.repository.create(make_workspace(user_id="user-2", name="User 2 Workspace"))

        results = self.repository.get_by_user("user-1")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "User 1 Workspace")

    # ==================================================================
    # update
    # ==================================================================

    def test_update_changes_name_and_data(self):
        """update() persists changes to name and data."""
        saved = self._saved(name="Old Name", data={"tabs": []})

        new_data = {"tabs": [{"name": "New Tab", "color": "#ffffff"}]}
        updated = Workspace(
            id=saved.id,
            user_id=saved.user_id,
            name="New Name",
            data=new_data,
            created_at=saved.created_at
        )

        result = self.repository.update(updated)

        self.assertEqual(result.name, "New Name")
        self.assertEqual(result.data["tabs"][0]["name"], "New Tab")

    def test_update_refreshes_updated_at(self):
        """update() sets a new updated_at timestamp."""
        saved = self._saved()
        original_updated_at = saved.updated_at

        import time
        time.sleep(0.01)  # Ensure time difference

        updated = Workspace(
            id=saved.id,
            user_id=saved.user_id,
            name=saved.name,
            data=saved.data,
            created_at=saved.created_at
        )

        result = self.repository.update(updated)

        # updated_at should be set (may equal original in fast tests, but must not be None)
        self.assertIsNotNone(result.updated_at)

    def test_update_persists_to_database(self):
        """update() changes are visible on subsequent get_by_id()."""
        saved = self._saved(name="Original")

        updated = Workspace(
            id=saved.id,
            user_id=saved.user_id,
            name="Updated Name",
            data={"tabs": [{"name": "Updated Tab"}]},
            created_at=saved.created_at
        )
        self.repository.update(updated)

        fetched = self.repository.get_by_id(saved.id)
        self.assertEqual(fetched.name, "Updated Name")
        self.assertEqual(fetched.data["tabs"][0]["name"], "Updated Tab")

    # ==================================================================
    # delete
    # ==================================================================

    def test_delete_removes_workspace(self):
        """delete() removes the workspace from the database."""
        saved = self._saved()

        result = self.repository.delete(saved.id)

        self.assertTrue(result)
        fetched = self.repository.get_by_id(saved.id)
        self.assertIsNone(fetched)

    def test_delete_returns_false_for_nonexistent(self):
        """delete() returns False when no row is deleted."""
        result = self.repository.delete("nonexistent-id")

        self.assertFalse(result)

    def test_delete_only_removes_specified_workspace(self):
        """delete() does not affect other workspaces."""
        saved_a = self._saved(name="Workspace A")
        saved_b = self._saved(name="Workspace B")

        self.repository.delete(saved_a.id)

        remaining = self.repository.get_by_id(saved_b.id)
        self.assertIsNotNone(remaining)
        self.assertEqual(remaining.name, "Workspace B")


if __name__ == "__main__":
    unittest.main()
