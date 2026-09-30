import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from src.platform.database.rows import json_column, now_iso
from src.platform.util.ids import generate_ulid


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _day_start(day: str) -> str:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc).isoformat()


def _day_after(day: str) -> str:
    return (datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=1)).isoformat()


def _summary(rows: List[Any]) -> Dict[str, Any]:
    total = Decimal(0)
    known = 0
    sources = set()
    for row in rows:
        amount = _decimal(row["amount_usd"])
        sources.add(row["source"])
        if amount is not None:
            total += amount
            known += 1
    if not rows:
        source = None
    elif len(sources) == 1:
        source = next(iter(sources))
    else:
        source = "mixed"
    return {
        "amount_usd": str(total) if known else None,
        "source": source,
        "entries": len(rows),
        "unpriced": len(rows) - known,
    }


class GenerationCostRepository:
    def record(
        self,
        generation_id: str,
        *,
        backend_id: Optional[str],
        model_id: Optional[str],
        user_id: Optional[str],
        amount_usd: Optional[Decimal],
        source: str,
        detail: Dict[str, Any],
    ) -> str:
        from src.platform.database.database import db

        cost_id = generate_ulid()
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generation_costs "
                "(id, generation_id, backend_id, model_id, user_id, amount_usd, source, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cost_id, generation_id, backend_id, model_id, user_id,
                    str(amount_usd) if amount_usd is not None else None,
                    source, json.dumps(detail, default=str), now_iso(),
                ),
            )
        return cost_id

    def for_generation(self, generation_id: str) -> List[Dict[str, Any]]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM generation_costs WHERE generation_id = ? ORDER BY created_at, id", (generation_id,)
            )
            return [
                {
                    "id": row["id"],
                    "backend_id": row["backend_id"],
                    "model_id": row["model_id"],
                    "amount_usd": row["amount_usd"],
                    "source": row["source"],
                    "detail": json_column(row["detail"], {}),
                    "created_at": row["created_at"],
                }
                for row in cursor.fetchall()
            ]

    def summaries(self, generation_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        ids = list(generation_ids)
        if not ids:
            return {}
        from src.platform.database.database import db

        grouped: Dict[str, List[Any]] = {}
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT generation_id, amount_usd, source FROM generation_costs "
                f"WHERE generation_id IN ({','.join('?' for _ in ids)})",
                ids,
            )
            for row in cursor.fetchall():
                grouped.setdefault(row["generation_id"], []).append(row)
        return {generation_id: _summary(rows) for generation_id, rows in grouped.items()}

    def spend(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        from src.platform.database.database import db

        where: List[str] = []
        params: List[Any] = []
        if date_from:
            where.append("c.created_at >= ?")
            params.append(_day_start(date_from))
        if date_to:
            where.append("c.created_at < ?")
            params.append(_day_after(date_to))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT c.backend_id, c.model_id, c.amount_usd, c.source, b.name AS backend_name, "
                "COALESCE(p.name, m.filename) AS model_label "
                "FROM generation_costs c "
                "LEFT JOIN backends b ON b.id = c.backend_id "
                "LEFT JOIN models m ON m.id = c.model_id "
                "LEFT JOIN providers p ON p.model_id = c.model_id AND p.provider LIKE 'cloud.%' "
                f"{clause}",
                params,
            )
            rows = cursor.fetchall()

        def bucket(key: str, extra: Dict[str, str]) -> List[Dict[str, Any]]:
            grouped: Dict[Any, List[Any]] = {}
            for row in rows:
                grouped.setdefault(row[key], []).append(row)
            items = []
            for ident, members in grouped.items():
                summary = _summary(members)
                items.append({key: ident, **{name: members[0][column] for name, column in extra.items()}, **summary})
            items.sort(key=lambda item: (_decimal(item["amount_usd"]) or Decimal(0)), reverse=True)
            return items

        overall = _summary(rows)
        return {
            "from": date_from,
            "to": date_to,
            "total_usd": overall["amount_usd"] or "0",
            "entries": overall["entries"],
            "unpriced": overall["unpriced"],
            "by_backend": bucket("backend_id", {"backend_name": "backend_name"}),
            "by_model": bucket("model_id", {"model": "model_label"}),
        }
