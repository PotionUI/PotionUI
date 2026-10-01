import json
import sqlite3
from typing import List, Optional

from src.features.formulas.records import Formula
from src.platform.database import get_database_connection
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid


class FormulaNameTaken(Exception):
    pass


class FormulaLimitReached(Exception):
    pass


class FormulaRepository:

    def create(self, formula: Formula, limit: int) -> Formula:
        formula_id = generate_ulid()
        stamp = now_iso()
        try:
            with get_database_connection() as conn:
                cursor = conn.execute(
                    "INSERT INTO formulas (id, owner_id, preset_id, mode, variant, name, note, "
                    "groups, values_json, signatures, preset_version, created_at, updated_at) "
                    "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? "
                    "WHERE (SELECT COUNT(*) FROM formulas WHERE owner_id = ? AND preset_id = ? AND mode = ?) < ?",
                    (
                        formula_id, formula.owner_id, formula.preset_id, formula.mode,
                        formula.variant, formula.name, formula.note, json.dumps(formula.groups),
                        json.dumps(formula.values), json.dumps(formula.signatures),
                        formula.preset_version, stamp, stamp,
                        formula.owner_id, formula.preset_id, formula.mode, limit,
                    ),
                )
                conn.commit()
                inserted = cursor.rowcount > 0
        except sqlite3.IntegrityError as exc:
            raise FormulaNameTaken(formula.name) from exc
        if not inserted:
            raise FormulaLimitReached()
        return self.get_for_owner(formula.owner_id, formula_id)

    def get_for_owner(self, owner_id: str, formula_id: str) -> Optional[Formula]:
        with get_database_connection() as conn:
            row = conn.execute(
                "SELECT * FROM formulas WHERE id = ? AND owner_id = ?", (formula_id, owner_id)
            ).fetchone()
        return Formula.from_row(row) if row else None

    def list_for_owner(self, owner_id: str, preset_id: str, mode: str) -> List[Formula]:
        with get_database_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM formulas WHERE owner_id = ? AND preset_id = ? AND mode = ? "
                "ORDER BY name COLLATE NOCASE ASC, id ASC",
                (owner_id, preset_id, mode),
            ).fetchall()
        return [Formula.from_row(row) for row in rows]

    def names_for_owner(self, owner_id: str, preset_id: str, mode: str) -> List[str]:
        with get_database_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM formulas WHERE owner_id = ? AND preset_id = ? AND mode = ?",
                (owner_id, preset_id, mode),
            ).fetchall()
        return [row["name"] for row in rows]

    def update(self, formula: Formula) -> Optional[Formula]:
        try:
            with get_database_connection() as conn:
                cursor = conn.execute(
                    "UPDATE formulas SET name = ?, note = ?, variant = ?, groups = ?, values_json = ?, "
                    "signatures = ?, preset_version = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                    (
                        formula.name, formula.note, formula.variant, json.dumps(formula.groups),
                        json.dumps(formula.values), json.dumps(formula.signatures),
                        formula.preset_version, now_iso(), formula.id, formula.owner_id,
                    ),
                )
                conn.commit()
                updated = cursor.rowcount > 0
        except sqlite3.IntegrityError as exc:
            raise FormulaNameTaken(formula.name) from exc
        return self.get_for_owner(formula.owner_id, formula.id) if updated else None

    def delete(self, owner_id: str, formula_id: str) -> bool:
        with get_database_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM formulas WHERE id = ? AND owner_id = ?", (formula_id, owner_id)
            )
            conn.commit()
            return cursor.rowcount > 0
