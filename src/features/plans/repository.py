from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.features.plans.records import GroupPlan, Plan, PlanLimit, PlanSubject, dump_limits
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid


def _db():
    from src.platform.database.database import db

    return db


class PlanRepository:

    def list_plans(self) -> List[Plan]:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT * FROM plans ORDER BY is_system, name COLLATE NOCASE")
            return [Plan.from_row(row) for row in cursor.fetchall()]

    def get(self, plan_id: Optional[str]) -> Optional[Plan]:
        if not plan_id:
            return None
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT * FROM plans WHERE id = ?", (plan_id,))
            row = cursor.fetchone()
            return Plan.from_row(row) if row else None

    def name_taken(self, name: str, exclude_id: Optional[str] = None) -> bool:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM plans WHERE name = ? COLLATE NOCASE AND id != ?", (name, exclude_id or "")
            )
            return cursor.fetchone() is not None

    def create(self, name: str, description: Optional[str], limits: Tuple[PlanLimit, ...]) -> Plan:
        plan_id = generate_ulid()
        now = now_iso()
        with _db().get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO plans (id, name, description, limits_json, is_system, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?)",
                (plan_id, name, description, dump_limits(limits), now, now),
            )
        return self.get(plan_id)

    def update(self, plan_id: str, name: str, description: Optional[str], limits: Tuple[PlanLimit, ...]) -> Plan:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "UPDATE plans SET name = ?, description = ?, limits_json = ?, updated_at = ? WHERE id = ?",
                (name, description, dump_limits(limits), now_iso(), plan_id),
            )
        return self.get(plan_id)

    def delete(self, plan_id: str, reassign_to: Optional[str]) -> Dict[str, int]:
        with _db().get_cursor() as cursor:
            cursor.execute("UPDATE user_groups SET plan_id = ? WHERE plan_id = ?", (reassign_to, plan_id))
            groups = cursor.rowcount
            cursor.execute("UPDATE users SET plan_id = ? WHERE plan_id = ?", (reassign_to, plan_id))
            users = cursor.rowcount
            cursor.execute("DELETE FROM plans WHERE id = ? AND is_system = 0", (plan_id,))
        return {"groups": groups, "users": users}

    def group(self, group_id: str) -> Optional[Dict[str, Any]]:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT id, name, is_system, plan_id FROM user_groups WHERE id = ?", (group_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def groups(self) -> List[Dict[str, Any]]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT ug.id, ug.name, ug.is_system, ug.plan_id, "
                "(SELECT COUNT(*) FROM user_group_members m WHERE m.group_id = ug.id) AS members "
                "FROM user_groups ug ORDER BY ug.is_system DESC, ug.name COLLATE NOCASE"
            )
            return [dict(row) for row in cursor.fetchall()]

    def set_group_plan(self, group_id: str, plan_id: Optional[str]) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute("UPDATE user_groups SET plan_id = ? WHERE id = ?", (plan_id, group_id))

    def default_plan_id(self) -> Optional[str]:
        group = self.group(ALL_USERS_GROUP_ID)
        return group["plan_id"] if group else None

    def group_member_ids(self, group_id: str) -> List[str]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT m.user_id FROM user_group_members m JOIN users u ON u.id = m.user_id "
                "WHERE m.group_id = ? ORDER BY u.username COLLATE NOCASE",
                (group_id,),
            )
            return [row["user_id"] for row in cursor.fetchall()]

    def set_user_plan(self, user_id: str, plan_id: Optional[str]) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute("UPDATE users SET plan_id = ? WHERE id = ?", (plan_id, user_id))

    def users_with_plan(self, plan_id: str) -> List[Dict[str, Any]]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT id, username FROM users WHERE plan_id = ? ORDER BY username COLLATE NOCASE", (plan_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def assignment_counts(self) -> Dict[str, Dict[str, int]]:
        counts: Dict[str, Dict[str, int]] = {}
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT plan_id, COUNT(*) AS n FROM user_groups WHERE plan_id IS NOT NULL AND id != ? GROUP BY plan_id",
                (ALL_USERS_GROUP_ID,),
            )
            for row in cursor.fetchall():
                counts.setdefault(row["plan_id"], {"groups": 0, "users": 0})["groups"] = row["n"]
            cursor.execute("SELECT plan_id, COUNT(*) AS n FROM users WHERE plan_id IS NOT NULL GROUP BY plan_id")
            for row in cursor.fetchall():
                counts.setdefault(row["plan_id"], {"groups": 0, "users": 0})["users"] = row["n"]
        return counts

    def subjects(self, user_ids: Optional[Iterable[str]] = None) -> List[PlanSubject]:
        wanted = None if user_ids is None else list(user_ids)
        if wanted == []:
            return []
        default_plan_id = self.default_plan_id()
        with _db().get_cursor() as cursor:
            if wanted is None:
                cursor.execute("SELECT id, username, email, account_type, plan_id FROM users ORDER BY username COLLATE NOCASE")
            else:
                marks = ",".join("?" for _ in wanted)
                cursor.execute(
                    f"SELECT id, username, email, account_type, plan_id FROM users WHERE id IN ({marks}) "
                    "ORDER BY username COLLATE NOCASE",
                    wanted,
                )
            users = [dict(row) for row in cursor.fetchall()]
            groups: Dict[str, List[GroupPlan]] = {}
            if users:
                marks = ",".join("?" for _ in users)
                cursor.execute(
                    "SELECT m.user_id, ug.id, ug.name, ug.plan_id FROM user_group_members m "
                    f"JOIN user_groups ug ON ug.id = m.group_id WHERE m.user_id IN ({marks}) "
                    "ORDER BY ug.name COLLATE NOCASE",
                    [user["id"] for user in users],
                )
                for row in cursor.fetchall():
                    groups.setdefault(row["user_id"], []).append(GroupPlan(row["id"], row["name"], row["plan_id"]))
        return [
            PlanSubject(
                user_id=user["id"],
                username=user["username"],
                email=user["email"],
                account_type=user["account_type"],
                override_plan_id=user["plan_id"],
                groups=tuple(groups.get(user["id"], ())),
                default_plan_id=default_plan_id,
            )
            for user in users
        ]

    def subject(self, user_id: str) -> Optional[PlanSubject]:
        found = self.subjects([user_id])
        return found[0] if found else None


class LimitEventRepository:

    def record(self, user_id: str, kind: str, units: float, ref_id: Optional[str], at: datetime) -> str:
        event_id = generate_ulid()
        with _db().get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO limit_events (id, user_id, kind, units, created_at, ref_id) VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, user_id, kind, units, at.isoformat(), ref_id),
            )
        return event_id

    def total(self, user_id: str, kind: str, since: Optional[datetime], until: Optional[datetime]) -> float:
        query = "SELECT COALESCE(SUM(units), 0) AS total FROM limit_events WHERE user_id = ? AND kind = ? AND refunded_at IS NULL"
        params: List[Any] = [user_id, kind]
        if since is not None:
            query += " AND created_at >= ?"
            params.append(since.isoformat())
        if until is not None:
            query += " AND created_at < ?"
            params.append(until.isoformat())
        with _db().get_cursor() as cursor:
            cursor.execute(query, params)
            return float(cursor.fetchone()["total"] or 0)

    def refund(self, ref_id: str, at: datetime) -> int:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "UPDATE limit_events SET refunded_at = ? WHERE ref_id = ? AND refunded_at IS NULL",
                (at.isoformat(), ref_id),
            )
            return cursor.rowcount

    def for_ref(self, ref_id: str) -> List[Dict[str, Any]]:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT * FROM limit_events WHERE ref_id = ? ORDER BY created_at", (ref_id,))
            return [dict(row) for row in cursor.fetchall()]

    def prune(self, before: datetime) -> int:
        with _db().get_cursor() as cursor:
            cursor.execute("DELETE FROM limit_events WHERE created_at < ?", (before.isoformat(),))
            return cursor.rowcount


_BREAKDOWN_LABELS = {"image": "Images", "video": "Videos", "audio": "Audio", "mesh": "3D", "upload": "Library uploads"}


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


class UsageRepository:

    def storage_bytes(self, user_id: str) -> int:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(file_size), 0) AS total FROM files WHERE user_id = ?", (user_id,))
            files = cursor.fetchone()["total"] or 0
            cursor.execute(
                "SELECT COALESCE(SUM(file_size), 0) AS total FROM uploads "
                "WHERE user_id = ? AND COALESCE(purpose, 'user_upload') = 'user_upload'",
                (user_id,),
            )
            uploads = cursor.fetchone()["total"] or 0
        return int(files) + int(uploads)

    def storage_breakdown(self, user_id: str) -> List[Dict[str, Any]]:
        totals: Dict[str, Dict[str, int]] = {}
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT LOWER(file_type) AS kind, COUNT(*) AS files, COALESCE(SUM(file_size), 0) AS bytes "
                "FROM files WHERE user_id = ? GROUP BY LOWER(file_type)",
                (user_id,),
            )
            for row in cursor.fetchall():
                key = row["kind"] if row["kind"] in _BREAKDOWN_LABELS else "image"
                entry = totals.setdefault(key, {"files": 0, "bytes": 0})
                entry["files"] += int(row["files"])
                entry["bytes"] += int(row["bytes"] or 0)
            cursor.execute(
                "SELECT COUNT(*) AS files, COALESCE(SUM(file_size), 0) AS bytes FROM uploads "
                "WHERE user_id = ? AND COALESCE(purpose, 'user_upload') = 'user_upload'",
                (user_id,),
            )
            row = cursor.fetchone()
            if row["files"]:
                totals["upload"] = {"files": int(row["files"]), "bytes": int(row["bytes"] or 0)}
        return [
            {"key": key, "label": label, **totals[key]}
            for key, label in _BREAKDOWN_LABELS.items()
            if key in totals and totals[key]["files"]
        ]

    def cloud_spend(self, user_id: str, since: Optional[datetime], until: Optional[datetime]) -> float:
        query = "SELECT amount_usd FROM generation_costs WHERE user_id = ?"
        params: List[Any] = [user_id]
        if since is not None:
            query += " AND created_at >= ?"
            params.append(since.isoformat())
        if until is not None:
            query += " AND created_at < ?"
            params.append(until.isoformat())
        with _db().get_cursor() as cursor:
            cursor.execute(query, params)
            total = sum((_decimal(row["amount_usd"]) for row in cursor.fetchall()), Decimal(0))
        return float(total)

    def generation_produced_output(self, generation_id: str) -> bool:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM generation_files WHERE generation_id = ? LIMIT 1", (generation_id,))
            if cursor.fetchone() is not None:
                return True
            cursor.execute("SELECT 1 FROM generation_costs WHERE generation_id = ? LIMIT 1", (generation_id,))
            return cursor.fetchone() is not None
