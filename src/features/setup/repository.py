"""Read access to the single-row instance-claim sentinel.

The sentinel is *written* atomically alongside the first user by
`UserRepository.create_claiming_instance` (the two live in one transaction, so
claiming is race-safe). This repository only reads it: whether the instance has
an owner and, informationally, who that owner is. It satisfies the
`InstanceClaimStore` protocol that `Auth` depends on.

It also carries `check_connection`, a trivial DB reachability probe unrelated
to the sentinel - reused by `ReadinessAggregator`'s service facet, which needs a
setup-feature collaborator that already holds a `db` handle.
"""

from typing import Optional

from src.platform.database import db


class InstanceClaimRepository:
    """Reads the ``instance_claim`` sentinel row."""

    def is_claimed(self) -> bool:
        """True once an owner has been created (the sentinel row exists)."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='instance_claim'"
            )
            if not cursor.fetchone():
                return False
            cursor.execute("SELECT 1 FROM instance_claim WHERE id = 1 LIMIT 1")
            return cursor.fetchone() is not None

    def owner_user_id(self) -> Optional[str]:
        """The id of the account that claimed the instance, or None if unclaimed."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='instance_claim'"
            )
            if not cursor.fetchone():
                return None
            cursor.execute("SELECT owner_user_id FROM instance_claim WHERE id = 1")
            row = cursor.fetchone()
            return row[0] if row else None

    def check_connection(self) -> None:
        """Raise if the database is unreachable; a plain connectivity probe."""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
