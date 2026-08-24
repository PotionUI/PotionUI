"""
Enable WAL (Write-Ahead Logging) mode for better concurrent access
"""

from src.platform.database.database import db

def up():
    """Enable WAL mode for better concurrent access"""
    with db.get_connection() as conn:
        # Enable WAL mode permanently
        conn.execute("PRAGMA journal_mode=WAL")
        
        # Optimize for concurrent access
        conn.execute("PRAGMA synchronous=NORMAL")
        
        # Set page cache size (negative value = KiB)
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        
        # Enable memory-mapped I/O
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB

def down():
    """Revert to default journal mode (not recommended)"""
    with db.get_connection() as conn:
        conn.execute("PRAGMA journal_mode=DELETE")