from typing import List, Optional
from datetime import datetime

from src.platform.database import db
from src.features.automation.records import Automation, AutomationRun, AutomationRunNode
from src.platform.util.ids import generate_ulid


class AutomationRepository:
    """CRUD + run-tracking persistence for the automation module."""

    # -- automations -----------------------------------------------------

    def create(self, automation: Automation) -> Automation:
        """Create a new automation. Assigns a ULID if `automation.id` is falsy."""
        automation_id = automation.id or generate_ulid()

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO automations (
                    id, name, description, enabled, graph, version, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                automation_id,
                automation.name,
                automation.description,
                int(automation.enabled),
                automation.serialize_graph(),
                automation.version,
                automation.user_id,
            ))

        return self.get_by_id(automation_id)

    def get_by_id(self, automation_id: str, user_id: Optional[str] = None) -> Optional[Automation]:
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute(
                    "SELECT * FROM automations WHERE id = ? AND user_id = ?",
                    (automation_id, user_id),
                )
            else:
                cursor.execute("SELECT * FROM automations WHERE id = ?", (automation_id,))
            row = cursor.fetchone()
            return Automation.from_row(row) if row else None

    def get_all(self, user_id: Optional[str] = None, enabled_only: bool = False) -> List[Automation]:
        query = "SELECT * FROM automations"
        conditions = []
        params: list = []

        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)

        if enabled_only:
            conditions.append("enabled = 1")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC"

        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [Automation.from_row(row) for row in cursor.fetchall()]

    def update(self, automation: Automation, bump_version: bool = False) -> Optional[Automation]:
        """Update name/description/graph/enabled. `bump_version` increments version (graph changed)."""
        with db.get_cursor() as cursor:
            if bump_version:
                cursor.execute("""
                    UPDATE automations
                    SET name = ?, description = ?, enabled = ?, graph = ?,
                        version = version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    automation.name,
                    automation.description,
                    int(automation.enabled),
                    automation.serialize_graph(),
                    automation.id,
                ))
            else:
                cursor.execute("""
                    UPDATE automations
                    SET name = ?, description = ?, enabled = ?, graph = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    automation.name,
                    automation.description,
                    int(automation.enabled),
                    automation.serialize_graph(),
                    automation.id,
                ))

            if cursor.rowcount == 0:
                return None

        return self.get_by_id(automation.id)

    def set_enabled(self, automation_id: str, enabled: bool) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE automations SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(enabled), automation_id),
            )
            return cursor.rowcount > 0

    def touch_last_run(self, automation_id: str, status: str) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE automations
                SET last_run_at = CURRENT_TIMESTAMP, last_run_status = ?
                WHERE id = ?
            """, (status, automation_id))
            return cursor.rowcount > 0

    def delete(self, automation_id: str) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM automations WHERE id = ?", (automation_id,))
            return cursor.rowcount > 0

    # -- runs --------------------------------------------------------------

    def create_run(self, run: AutomationRun) -> AutomationRun:
        run_id = run.id or generate_ulid()

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO automation_runs (
                    id, automation_id, trigger_node_id, trigger_type, status, event_payload
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                run_id,
                run.automation_id,
                run.trigger_node_id,
                run.trigger_type,
                run.status,
                run.serialize_event_payload(),
            ))

        return self.get_run(run_id)

    def finish_run(self, run_id: str, status: str, error: Optional[str] = None,
                    duration_ms: Optional[int] = None) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE automation_runs
                SET status = ?, error = ?, duration_ms = ?, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, error, duration_ms, run_id))
            return cursor.rowcount > 0

    def get_run(self, run_id: str) -> Optional[AutomationRun]:
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
            return AutomationRun.from_row(row) if row else None

    def list_runs(self, automation_id: str, limit: int = 20,
                   before: Optional[str] = None) -> List[AutomationRun]:
        """Keyset-paginated run history, newest first. `before` is a run id cursor."""
        params: list = [automation_id]
        query = "SELECT * FROM automation_runs WHERE automation_id = ?"

        if before:
            before_run = self.get_run(before)
            if before_run and before_run.started_at:
                query += " AND started_at < ?"
                params.append(before_run.started_at.isoformat())

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [AutomationRun.from_row(row) for row in cursor.fetchall()]

    # -- run nodes -----------------------------------------------------------

    def create_run_node(self, run_node: AutomationRunNode) -> AutomationRunNode:
        run_node_id = run_node.id or generate_ulid()

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO automation_run_nodes (
                    id, run_id, node_id, node_type, status, input, output
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                run_node_id,
                run_node.run_id,
                run_node.node_id,
                run_node.node_type,
                run_node.status,
                run_node.serialize_input(),
                run_node.serialize_output(),
            ))

        return self.get_run_node(run_node_id)

    def update_run_node(self, run_node_id: str, status: str, output: Optional[str] = None,
                         error: Optional[str] = None, finished: bool = False) -> bool:
        with db.get_cursor() as cursor:
            if finished:
                cursor.execute("""
                    UPDATE automation_run_nodes
                    SET status = ?, output = ?, error = ?, finished_at = CURRENT_TIMESTAMP,
                        duration_ms = CAST((julianday(CURRENT_TIMESTAMP) - julianday(started_at)) * 86400000 AS INTEGER)
                    WHERE id = ?
                """, (status, output, error, run_node_id))
            else:
                cursor.execute("""
                    UPDATE automation_run_nodes
                    SET status = ?, output = ?, error = ?
                    WHERE id = ?
                """, (status, output, error, run_node_id))
            return cursor.rowcount > 0

    def get_run_node(self, run_node_id: str) -> Optional[AutomationRunNode]:
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM automation_run_nodes WHERE id = ?", (run_node_id,))
            row = cursor.fetchone()
            return AutomationRunNode.from_row(row) if row else None

    def list_run_nodes(self, run_id: str) -> List[AutomationRunNode]:
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM automation_run_nodes WHERE run_id = ? ORDER BY started_at ASC",
                (run_id,),
            )
            return [AutomationRunNode.from_row(row) for row in cursor.fetchall()]


# Global repository instance
automation_repo = AutomationRepository()
