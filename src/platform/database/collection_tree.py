from typing import Dict, Optional

MAX_TREE_DEPTH = 256

_TREE_TABLES = frozenset({"collections", "model_collections", "inspiration_collections"})
_MEMBER_TABLES = frozenset({
    "collection_generations",
    "collection_uploads",
    "collection_prompts",
    "model_collection_members",
    "inspiration_collection_items",
})


def _tree(table: str) -> str:
    if table not in _TREE_TABLES:
        raise ValueError(f"Unknown collection table: {table}")
    return table


def _member(table: str) -> str:
    if table not in _MEMBER_TABLES:
        raise ValueError(f"Unknown membership table: {table}")
    return table


def subtree_ids_sql(tree_table: str) -> str:
    tree = _tree(tree_table)
    return (
        "(WITH RECURSIVE subtree(id, user_id, depth) AS ("
        f"SELECT id, user_id, 0 FROM {tree} WHERE id = ? "
        "UNION "
        f"SELECT c.id, c.user_id, s.depth + 1 FROM {tree} c "
        "JOIN subtree s ON c.parent_id = s.id AND c.user_id = s.user_id "
        f"WHERE s.depth < {MAX_TREE_DEPTH}"
        ") SELECT id FROM subtree)"
    )


def membership_clause(
    *,
    tree_table: str,
    member_table: str,
    item_column: str,
    item_ref: str,
    include_descendants: bool = True,
) -> str:
    member = _member(member_table)
    if include_descendants:
        target = f"IN {subtree_ids_sql(tree_table)}"
    else:
        _tree(tree_table)
        target = "= ?"
    return (
        f"EXISTS (SELECT 1 FROM {member} cm "
        f"WHERE cm.{item_column} = {item_ref} AND cm.collection_id {target})"
    )


def unsorted_clause(
    *,
    tree_table: str,
    member_table: str,
    item_column: str,
    item_ref: str,
    scope_column: Optional[str] = None,
) -> str:
    tree = _tree(tree_table)
    member = _member(member_table)
    scope = f" AND uc.{scope_column} = ?" if scope_column else ""
    return (
        f"NOT EXISTS (SELECT 1 FROM {member} um "
        f"JOIN {tree} uc ON uc.id = um.collection_id "
        f"WHERE um.{item_column} = {item_ref} AND uc.user_id = ?{scope})"
    )


def rolled_up_counts(
    cursor,
    *,
    tree_table: str,
    member_table: str,
    item_column: str,
    user_id: str,
    include_descendants: bool = True,
    scope: Optional[str] = None,
    owned_item_table: Optional[str] = None,
) -> Dict[str, int]:
    tree = _tree(tree_table)
    member = _member(member_table)
    scope_sql = " AND scope = ?" if scope else ""
    scope_params = [scope] if scope else []

    if include_descendants:
        closure = (
            "WITH RECURSIVE closure(ancestor, id, depth) AS ("
            f"SELECT id, id, 0 FROM {tree} WHERE user_id = ?{scope_sql} "
            "UNION "
            "SELECT cl.ancestor, c.id, cl.depth + 1 FROM closure cl "
            f"JOIN {tree} c ON c.parent_id = cl.id AND c.user_id = ? "
            f"WHERE cl.depth < {MAX_TREE_DEPTH}"
            ") "
        )
        params = [user_id, *scope_params, user_id]
    else:
        closure = (
            "WITH closure(ancestor, id, depth) AS ("
            f"SELECT id, id, 0 FROM {tree} WHERE user_id = ?{scope_sql}"
            ") "
        )
        params = [user_id, *scope_params]

    owner_join = ""
    if owned_item_table:
        owner_join = f" JOIN {owned_item_table} oi ON oi.id = m.{item_column} AND oi.user_id = ?"
        params.append(user_id)

    cursor.execute(
        closure
        + f"SELECT cl.ancestor AS collection_id, COUNT(DISTINCT m.{item_column}) AS n "
        f"FROM closure cl JOIN {member} m ON m.collection_id = cl.id{owner_join} "
        "GROUP BY cl.ancestor",
        params,
    )
    return {row["collection_id"]: row["n"] for row in cursor.fetchall()}
