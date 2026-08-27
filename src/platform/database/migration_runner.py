import os
import importlib.util
from pathlib import Path
from typing import List, Dict, Any
from .database import db

class MigrationRunner:
    def __init__(self):
        # Resolved against this module, not the working directory, so the
        # runner finds its migrations no matter where the process was started.
        self.migrations_dir = Path(__file__).parent / "migrations"
        
    def get_applied_migrations(self) -> List[str]:
        """Get list of applied migrations from database"""
        with db.get_cursor() as cursor:
            # Create migrations table if it doesn't exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS applied_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_name TEXT UNIQUE NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("SELECT migration_name FROM applied_migrations ORDER BY migration_name")
            return [row[0] for row in cursor.fetchall()]
    
    def get_available_migrations(self) -> List[str]:
        """Get list of available migration files"""
        migrations = []
        for file in self.migrations_dir.glob("*.py"):
            if file.name != "__init__.py":
                migrations.append(file.stem)
        return sorted(migrations)
    
    def has_pending_migrations(self) -> bool:
        """Check if there are pending migrations without running them"""
        applied = self.get_applied_migrations()
        available = self.get_available_migrations()
        pending = [m for m in available if m not in applied]
        return len(pending) > 0

    def run_migrations(self):
        """Run all pending migrations"""
        applied = self.get_applied_migrations()
        available = self.get_available_migrations()

        pending = [m for m in available if m not in applied]

        if not pending:
            return

        print(f"Running {len(pending)} migrations...")

        for migration_name in pending:
            print(f"Applying migration: {migration_name}")
            self._run_migration(migration_name)
            self._mark_migration_applied(migration_name)
            print(f"Migration {migration_name} applied successfully")
    
    def _run_migration(self, migration_name: str):
        """Run a specific migration"""
        migration_path = self.migrations_dir / f"{migration_name}.py"
        
        spec = importlib.util.spec_from_file_location(migration_name, migration_path)
        migration_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration_module)
        
        if hasattr(migration_module, 'up'):
            migration_module.up()
        else:
            raise AttributeError(f"Migration {migration_name} must have an 'up' function")
    
    def _mark_migration_applied(self, migration_name: str):
        """Mark migration as applied in database"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO applied_migrations (migration_name) VALUES (?)",
                (migration_name,)
            )

# Global migration manager instance
migration_runner = MigrationRunner()