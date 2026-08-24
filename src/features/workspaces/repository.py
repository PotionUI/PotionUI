"""
Workspace Repository

Handles database operations for workspaces (tab layout configurations).
"""
from typing import List, Optional
import json
from datetime import datetime, timezone

from src.platform.database import db
from src.features.workspaces.records import Workspace
from src.platform.util.ids import generate_ulid


class WorkspaceRepository:
    """Repository for workspace database operations."""

    def _row_to_workspace(self, row) -> Workspace:
        """Convert a database row to Workspace model."""
        data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']

        return Workspace(
            id=row['id'],
            user_id=row['user_id'],
            name=row['name'],
            data=data,
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def create(self, workspace: Workspace) -> Workspace:
        """Create a new workspace."""
        workspace_id = workspace.id if workspace.id else generate_ulid()
        created_at = workspace.created_at if workspace.created_at else datetime.now(timezone.utc)
        updated_at = datetime.now(timezone.utc)

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO workspaces (id, user_id, name, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                workspace_id,
                workspace.user_id,
                workspace.name,
                json.dumps(workspace.data),
                created_at.isoformat() if isinstance(created_at, datetime) else created_at,
                updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at
            ))

        return Workspace(
            id=workspace_id,
            user_id=workspace.user_id,
            name=workspace.name,
            data=workspace.data,
            created_at=created_at,
            updated_at=updated_at
        )

    def get_by_id(self, workspace_id: str) -> Optional[Workspace]:
        """Get workspace by ID."""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
            row = cursor.fetchone()

            if row:
                return self._row_to_workspace(row)
            return None

    def get_by_user(self, user_id: str) -> List[Workspace]:
        """Get all workspaces for a user."""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM workspaces
                WHERE user_id = ?
                ORDER BY updated_at DESC
            """, (user_id,))

            rows = cursor.fetchall()
            return [self._row_to_workspace(row) for row in rows]

    def update(self, workspace: Workspace) -> Workspace:
        """Update an existing workspace."""
        updated_at = datetime.now(timezone.utc)

        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE workspaces
                SET name = ?, data = ?, updated_at = ?
                WHERE id = ?
            """, (
                workspace.name,
                json.dumps(workspace.data),
                updated_at.isoformat(),
                workspace.id
            ))

        return Workspace(
            id=workspace.id,
            user_id=workspace.user_id,
            name=workspace.name,
            data=workspace.data,
            created_at=workspace.created_at,
            updated_at=updated_at
        )

    def delete(self, workspace_id: str) -> bool:
        """Delete a workspace."""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            return cursor.rowcount > 0
