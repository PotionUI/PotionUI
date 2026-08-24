"""Aggregate queries backing the admin Stats page.

Everything here is a SQL aggregate: counts, sums and percentiles are computed by SQLite and
only the aggregated rows cross into Python. Never fetch generations to count them.

Two data-shape facts drive the SQL:

- `generation_parameters.parameter_value` is JSON-encoded, so `"euler"` is stored with its
  quotes. `json_extract(value, '$')` decodes it; a bare `TRIM(value, '"')` would corrupt any
  value that legitimately contains a quote.
- `duration_ms` exists only from migration 075 onward, and is NULL for rows the migration
  could not trust. Every duration aggregate filters on `duration_ms IS NOT NULL` so an
  unknown duration never reads as a zero.

Date filters bound `generations.created_at`, which SQLite writes in UTC.
"""

from typing import Any, Dict, List, Optional, Tuple

from src.platform.database import db


# Public dimension name -> how to aggregate it. Nothing outside this map can reach the SQL,
# so a caller can never inject a column or a parameter name.
#
# 'param' dimensions live as rows in generation_parameters keyed by parameter_name.
# 'column' dimensions are columns on generations.
#
# `numeric` params are grouped by their numeric value, not their stored text. Different
# presets write the same number differently -- cfg is stored as both "1" and "1.0" -- and
# grouping on the raw string would split one value across two bars.
_DIMENSIONS: Dict[str, Dict[str, Any]] = {
    'preset': {'kind': 'column', 'column': 'g.preset_id'},
    'mode': {'kind': 'column', 'column': 'g.mode'},
    'status': {'kind': 'column', 'column': 'g.status'},
    'model': {'kind': 'model'},
    'model_type': {'kind': 'model_type'},
    'sampler': {'kind': 'param', 'param': 'sampler'},
    'scheduler': {'kind': 'param', 'param': 'scheduler'},
    'steps': {'kind': 'param', 'param': 'steps', 'numeric': True},
    'cfg': {'kind': 'param', 'param': 'cfg', 'numeric': True},
    'resolution': {'kind': 'param', 'param': 'resolution'},
    'denoise': {'kind': 'param', 'param': 'denoise', 'numeric': True},
}

DIMENSIONS: Tuple[str, ...] = tuple(_DIMENSIONS)
METRICS: Tuple[str, ...] = ('count', 'duration', 'bytes')
BUCKETS: Dict[str, str] = {'day': '%Y-%m-%d', 'week': '%Y-W%W', 'month': '%Y-%m'}

# Total output bytes for one generation. Mirrors the `file_size` sort expression in
# generation_repository so the two never disagree about what a generation "weighs".
_BYTES_FOR_GENERATION = (
    '(SELECT COALESCE(SUM(f.file_size), 0) FROM generation_files gf '
    'JOIN files f ON gf.file_id = f.id WHERE gf.generation_id = g.id)'
)


def _format_key(value: Any, numeric: bool) -> str:
    """Render a group key for display. Numeric params come back as REAL after the CAST, so
    an integral step count must print as '9', not '9.0'."""
    if numeric and isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _percentile(cursor, fraction: float, where: str, params: List[Any]) -> Optional[int]:
    """Nearest-rank percentile of duration_ms.

    SQLite has no percentile function without an extension, and pulling 5k rows into Python
    to sort them defeats the point. LIMIT 1 OFFSET n over an ordered index does it in SQL.
    """
    cursor.execute(f"SELECT COUNT(*) FROM generations g WHERE {where} AND g.duration_ms IS NOT NULL", params)
    n = cursor.fetchone()[0]
    if not n:
        return None
    offset = max(0, min(n - 1, int(round(fraction * n)) - 1))
    cursor.execute(
        f"SELECT g.duration_ms FROM generations g WHERE {where} AND g.duration_ms IS NOT NULL "
        f"ORDER BY g.duration_ms LIMIT 1 OFFSET ?",
        params + [offset],
    )
    row = cursor.fetchone()
    return row[0] if row else None


class StatsRepository:
    # --- filters ------------------------------------------------------------------

    def _range(self, date_from: Optional[str], date_to: Optional[str],
               alias: str = 'g') -> Tuple[str, List[Any]]:
        """WHERE fragment bounding created_at to [date_from 00:00:00, date_to 23:59:59].

        Lexicographic comparison is valid because created_at is always
        'YYYY-MM-DD HH:MM:SS' (SQLite CURRENT_TIMESTAMP).
        """
        clauses: List[str] = ['1=1']
        params: List[Any] = []
        if date_from:
            clauses.append(f"{alias}.created_at >= ?")
            params.append(f"{date_from} 00:00:00")
        if date_to:
            clauses.append(f"{alias}.created_at <= ?")
            params.append(f"{date_to} 23:59:59")
        return ' AND '.join(clauses), params

    # --- overview -----------------------------------------------------------------

    def overview(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        where, params = self._range(date_from, date_to)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN g.status = 'completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN g.status = 'failed' THEN 1 ELSE 0 END) AS failed,
                       COUNT(DISTINCT DATE(g.created_at)) AS active_days,
                       MIN(g.created_at) AS first_at,
                       MAX(g.created_at) AS last_at
                FROM generations g WHERE {where}
                """,
                params,
            )
            row = cursor.fetchone()

            cursor.execute(
                f"""
                SELECT COUNT(f.id) AS files,
                       COALESCE(SUM(f.file_size), 0) AS bytes,
                       COALESCE(SUM(CASE WHEN f.file_type = 'IMAGE' THEN f.file_size END), 0) AS image_bytes,
                       COALESCE(SUM(CASE WHEN f.file_type = 'VIDEO' THEN f.file_size END), 0) AS video_bytes
                FROM generations g
                JOIN generation_files gf ON gf.generation_id = g.id
                JOIN files f ON f.id = gf.file_id
                WHERE {where}
                """,
                params,
            )
            files = cursor.fetchone()

            cursor.execute(
                f"""
                SELECT COUNT(DISTINCT gm.model_id) FROM generations g
                JOIN generation_models gm ON gm.generation_id = g.id WHERE {where}
                """,
                params,
            )
            distinct_models = cursor.fetchone()[0]

            cursor.execute(
                f"SELECT AVG(g.duration_ms) FROM generations g WHERE {where} AND g.duration_ms IS NOT NULL",
                params,
            )
            avg_duration = cursor.fetchone()[0]

            return {
                'total_generations': row['total'] or 0,
                'completed': row['completed'] or 0,
                'failed': row['failed'] or 0,
                'active_days': row['active_days'] or 0,
                'first_generation_at': row['first_at'],
                'last_generation_at': row['last_at'],
                'total_outputs': files['files'] or 0,
                'total_bytes': files['bytes'] or 0,
                'image_bytes': files['image_bytes'] or 0,
                'video_bytes': files['video_bytes'] or 0,
                'distinct_models': distinct_models or 0,
                'avg_duration_ms': int(avg_duration) if avg_duration is not None else None,
                'median_duration_ms': _percentile(cursor, 0.50, where, params),
                'p95_duration_ms': _percentile(cursor, 0.95, where, params),
            }

    # --- time series --------------------------------------------------------------

    def timeseries(self, metric: str = 'count', bucket: str = 'day',
                   date_from: Optional[str] = None, date_to: Optional[str] = None) -> List[Dict[str, Any]]:
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric!r}")
        if bucket not in BUCKETS:
            raise ValueError(f"unknown bucket {bucket!r}")

        fmt = BUCKETS[bucket]
        where, params = self._range(date_from, date_to)

        if metric == 'count':
            value_expr, extra = 'COUNT(*)', ''
        elif metric == 'duration':
            # Mean of known durations only; rows the migration could not trust are excluded
            # rather than counted as zero.
            value_expr, extra = 'COALESCE(AVG(g.duration_ms), 0)', ' AND g.duration_ms IS NOT NULL'
        else:
            value_expr, extra = f'COALESCE(SUM({_BYTES_FOR_GENERATION}), 0)', ''

        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT strftime(?, g.created_at) AS bucket, {value_expr} AS value
                FROM generations g WHERE {where}{extra}
                GROUP BY bucket ORDER BY bucket
                """,
                [fmt] + params,
            )
            return [{'bucket': r['bucket'], 'value': r['value']} for r in cursor.fetchall()]

    # --- breakdown ----------------------------------------------------------------

    def breakdown(self, dimension: str, limit: int = 10,
                  date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        spec = _DIMENSIONS.get(dimension)
        if spec is None:
            raise ValueError(f"unknown dimension {dimension!r}")

        where, params = self._range(date_from, date_to)
        kind = spec['kind']

        with db.get_cursor() as cursor:
            if kind == 'column':
                col = spec['column']
                sql = (
                    f"SELECT {col} AS key, NULL AS grp, COUNT(*) AS count "
                    f"FROM generations g WHERE {where} AND {col} IS NOT NULL "
                    f"GROUP BY key ORDER BY count DESC"
                )
                count_sql = f"SELECT COUNT(DISTINCT {col}) FROM generations g WHERE {where}"
            elif kind == 'model':
                sql = (
                    "SELECT m.filename AS key, m.model_type AS grp, "
                    "COUNT(DISTINCT gm.generation_id) AS count "
                    "FROM generations g "
                    "JOIN generation_models gm ON gm.generation_id = g.id "
                    "JOIN models m ON m.id = gm.model_id "
                    f"WHERE {where} GROUP BY m.id ORDER BY count DESC"
                )
                count_sql = (
                    "SELECT COUNT(DISTINCT gm.model_id) FROM generations g "
                    f"JOIN generation_models gm ON gm.generation_id = g.id WHERE {where}"
                )
            elif kind == 'model_type':
                sql = (
                    "SELECT m.model_type AS key, NULL AS grp, "
                    "COUNT(DISTINCT gm.generation_id) AS count "
                    "FROM generations g "
                    "JOIN generation_models gm ON gm.generation_id = g.id "
                    "JOIN models m ON m.id = gm.model_id "
                    f"WHERE {where} GROUP BY m.model_type ORDER BY count DESC"
                )
                count_sql = (
                    "SELECT COUNT(DISTINCT m.model_type) FROM generations g "
                    "JOIN generation_models gm ON gm.generation_id = g.id "
                    f"JOIN models m ON m.id = gm.model_id WHERE {where}"
                )
            else:  # param
                # CAST(... AS REAL) collapses "1" and "1.0" onto one bar. Non-numeric params
                # keep their text: "1080x1920" must not become 1080.
                value = "json_extract(p.parameter_value, '$')"
                if spec.get('numeric'):
                    value = f"CAST({value} AS REAL)"
                sql = (
                    f"SELECT {value} AS key, NULL AS grp, "
                    "COUNT(*) AS count FROM generations g "
                    "JOIN generation_parameters p ON p.generation_id = g.id "
                    f"WHERE {where} AND p.parameter_name = ? "
                    "GROUP BY key ORDER BY count DESC"
                )
                count_sql = (
                    f"SELECT COUNT(DISTINCT {value}) FROM generations g "
                    "JOIN generation_parameters p ON p.generation_id = g.id "
                    f"WHERE {where} AND p.parameter_name = ?"
                )
                params = params + [spec['param']]

            cursor.execute(count_sql, params)
            total_distinct = cursor.fetchone()[0] or 0

            cursor.execute(f"{sql} LIMIT ?", params + [limit])
            numeric = bool(spec.get('numeric'))
            items = [
                {'key': _format_key(r['key'], numeric), 'group': r['grp'], 'count': r['count']}
                for r in cursor.fetchall()
                if r['key'] is not None
            ]

        return {'dimension': dimension, 'items': items, 'total_distinct': total_distinct}

    # --- durations ----------------------------------------------------------------

    def durations(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        """Histogram of duration_ms over fixed second-boundary buckets, plus percentiles.

        Buckets are fixed rather than computed from min/max so the x-axis is stable as the
        date filter changes: a chart whose bins move under you cannot be compared over time.
        """
        edges = [0, 5, 10, 20, 30, 60, 120, 300, 600]  # seconds; last bin is open-ended
        where, params = self._range(date_from, date_to)

        with db.get_cursor() as cursor:
            buckets: List[Dict[str, Any]] = []
            for i, lower in enumerate(edges):
                upper = edges[i + 1] if i + 1 < len(edges) else None
                if upper is None:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM generations g WHERE {where} "
                        f"AND g.duration_ms IS NOT NULL AND g.duration_ms >= ?",
                        params + [lower * 1000],
                    )
                else:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM generations g WHERE {where} "
                        f"AND g.duration_ms IS NOT NULL AND g.duration_ms >= ? AND g.duration_ms < ?",
                        params + [lower * 1000, upper * 1000],
                    )
                buckets.append({'lower_s': lower, 'upper_s': upper, 'count': cursor.fetchone()[0]})

            cursor.execute(
                f"SELECT COUNT(*) FROM generations g WHERE {where} AND g.duration_ms IS NULL",
                params,
            )
            unknown = cursor.fetchone()[0]

            return {
                'buckets': buckets,
                'unknown': unknown,
                'p50_ms': _percentile(cursor, 0.50, where, params),
                'p95_ms': _percentile(cursor, 0.95, where, params),
                'p99_ms': _percentile(cursor, 0.99, where, params),
            }

    # --- storage ------------------------------------------------------------------

    def storage(self, date_from: Optional[str] = None, date_to: Optional[str] = None,
                bucket: str = 'day', limit: int = 30) -> Dict[str, Any]:
        """``limit`` bounds BOTH lists this admin page renders unbounded rows
        for: the most recent ``limit`` time buckets of ``over_time`` (with no
        date filter this renders one row per day since the instance's first
        generation) and the top ``limit`` resolutions. One knob, because the
        admin page exposes one row-limit control per section.
        """
        if bucket not in BUCKETS:
            raise ValueError(f"unknown bucket {bucket!r}")
        fmt = BUCKETS[bucket]
        where, params = self._range(date_from, date_to)

        join = (
            "FROM generations g "
            "JOIN generation_files gf ON gf.generation_id = g.id "
            "JOIN files f ON f.id = gf.file_id "
        )

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT f.file_type, COUNT(*) AS count, COALESCE(SUM(f.file_size), 0) AS bytes "
                f"{join} WHERE {where} GROUP BY f.file_type ORDER BY bytes DESC",
                params,
            )
            by_type = [dict(r) for r in cursor.fetchall()]

            # Most recent `limit` buckets: DESC + LIMIT picks the newest rows,
            # then reversed back to ascending so the chart still reads left
            # (oldest) to right (newest).
            cursor.execute(
                f"""
                SELECT strftime(?, g.created_at) AS bucket,
                       COALESCE(SUM(CASE WHEN f.file_type = 'IMAGE' THEN f.file_size END), 0) AS image_bytes,
                       COALESCE(SUM(CASE WHEN f.file_type = 'VIDEO' THEN f.file_size END), 0) AS video_bytes
                {join} WHERE {where} GROUP BY bucket ORDER BY bucket DESC LIMIT ?
                """,
                [fmt] + params + [limit],
            )
            over_time = [dict(r) for r in cursor.fetchall()]
            over_time.reverse()

            cursor.execute(
                f"SELECT f.width, f.height, COUNT(*) AS count, COALESCE(SUM(f.file_size), 0) AS bytes "
                f"{join} WHERE {where} AND f.width IS NOT NULL AND f.height IS NOT NULL "
                f"GROUP BY f.width, f.height ORDER BY count DESC LIMIT ?",
                params + [limit],
            )
            resolutions = [
                {'label': f"{r['width']}x{r['height']}", 'width': r['width'],
                 'height': r['height'], 'count': r['count'], 'bytes': r['bytes']}
                for r in cursor.fetchall()
            ]

            cursor.execute(
                f"SELECT COALESCE(AVG(f.file_size), 0) {join} WHERE {where}",
                params,
            )
            avg_bytes = cursor.fetchone()[0]

        return {
            'by_type': by_type,
            'over_time': over_time,
            'top_resolutions': resolutions,
            'avg_file_bytes': int(avg_bytes or 0),
        }


stats_repo = StatsRepository()
