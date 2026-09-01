"""Persistence for LLM tool governance: admin per-(LLM-config, tool) config
(`tool_governance`) and a global per-user opt-out set (`user_disabled_tools`).
See src.features.llm.tools.governance for the composition rules this data
feeds.
"""

from datetime import datetime
from typing import Dict, Iterable, Optional, Set, Tuple


class ToolGovernanceRepository:
    """Persistence for per-config admin tool config and the global per-user
    opt-out set."""

    def get_all_config(self, llm_config_id: str) -> Dict[str, Dict[str, bool]]:
        """Every governed tool's {enabled, locked} for one LLM config, keyed
        by tool name."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT tool_name, enabled, locked FROM tool_governance WHERE llm_config_id = ?",
                (llm_config_id,),
            )
            return {
                row["tool_name"]: {"enabled": bool(row["enabled"]), "locked": bool(row["locked"])}
                for row in cursor.fetchall()
            }

    def get_config(self, llm_config_id: str, tool_name: str) -> Optional[Dict[str, bool]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT enabled, locked FROM tool_governance "
                "WHERE llm_config_id = ? AND tool_name = ?",
                (llm_config_id, tool_name),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {"enabled": bool(row["enabled"]), "locked": bool(row["locked"])}

    def get_config_snapshot(
        self, llm_config_id: str, tool_names: Iterable[str]
    ) -> Dict[str, Tuple[bool, bool]]:
        """The governance rows for one LLM config relevant to `tool_names`, as
        a hashable {name: (enabled, locked)} snapshot - built for use as a
        cache-key component (see ChatContextBuilder), so it always reflects
        the current row rather than a value memoized elsewhere."""
        names = list(tool_names)
        if not names:
            return {}
        placeholders = ",".join("?" for _ in names)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT tool_name, enabled, locked FROM tool_governance "
                f"WHERE llm_config_id = ? AND tool_name IN ({placeholders})",
                (llm_config_id, *names),
            )
            return {
                row["tool_name"]: (bool(row["enabled"]), bool(row["locked"]))
                for row in cursor.fetchall()
            }

    def upsert_config(
        self,
        llm_config_id: str,
        tool_name: str,
        enabled: Optional[bool] = None,
        locked: Optional[bool] = None,
    ) -> Dict[str, bool]:
        """Merge whichever of `enabled`/`locked` is given onto this config's
        existing row for the tool, defaulting the other to
        enabled=True/locked=False on first write."""
        existing = self.get_config(llm_config_id, tool_name) or {"enabled": True, "locked": False}
        merged_enabled = existing["enabled"] if enabled is None else enabled
        merged_locked = existing["locked"] if locked is None else locked
        now = datetime.now().isoformat()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(llm_config_id, tool_name) DO UPDATE SET
                    enabled = excluded.enabled,
                    locked = excluded.locked,
                    updated_at = excluded.updated_at
                """,
                (llm_config_id, tool_name, merged_enabled, merged_locked, now),
            )
        return {"enabled": merged_enabled, "locked": merged_locked}

    def delete_config(self, llm_config_id: str) -> None:
        """Drop every governance row for a deleted LLM config."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM tool_governance WHERE llm_config_id = ?", (llm_config_id,))

    def get_user_disabled(self, user_id: str) -> Set[str]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT tool_name FROM user_disabled_tools WHERE user_id = ?", (user_id,)
            )
            return {row["tool_name"] for row in cursor.fetchall()}

    def set_user_disabled(self, user_id: str, tool_name: str, disabled: bool) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if disabled:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_disabled_tools (user_id, tool_name, created_at) "
                    "VALUES (?, ?, ?)",
                    (user_id, tool_name, datetime.now().isoformat()),
                )
            else:
                cursor.execute(
                    "DELETE FROM user_disabled_tools WHERE user_id = ? AND tool_name = ?",
                    (user_id, tool_name),
                )
