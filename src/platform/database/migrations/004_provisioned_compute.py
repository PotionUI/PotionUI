"""Migration 004: create `provisioned_compute` - one row per rented GPU
resource created through a registered `ComputeProvisioner` (see
`src.features.provisioning`), linked to the `native.remote` backend row core
creates for it.

`handle` is opaque to core - whatever the provisioner needs to look its own
resource back up (a profile name, a pod id, ...). `backend_id` has no FK
constraint deletion cascade beyond SET NULL: terminating a provisioned
resource removes the backend row through `BackendRegistry.remove_backend`
first (see `operations.terminate_compute`), so a dangling reference here
would only ever be transient.

IDEMPOTENT. `CREATE TABLE IF NOT EXISTS` - a second run is a no-op.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provisioned_compute (
                id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                handle TEXT NOT NULL,
                profile_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unknown',
                backend_id TEXT,
                resource_ref TEXT,
                gpu_type_id TEXT,
                region TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (backend_id) REFERENCES backends(id) ON DELETE SET NULL
            )
        """)
    print("Migration 004_provisioned_compute: created provisioned_compute table")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS provisioned_compute")
    print("Migration 004_provisioned_compute: dropped provisioned_compute table")
