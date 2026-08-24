"""
Add gpu_max_vram and attention_mechanism settings
"""

from src.platform.database.database import db

def up():
    """Add GPU and attention mechanism settings"""
    with db.get_cursor() as cursor:
        # Insert the gpu_max_vram setting
        cursor.execute("""
            INSERT INTO settings (id, key, value, value_type, description, type) VALUES
            ('setting_gpu_max_vram', 'gpu_max_vram', '8', 'integer', 'Maximum GPU VRAM usage in GB', 'SYSTEM')
        """)

        # Insert the attention_mechanism setting
        cursor.execute("""
            INSERT INTO settings (id, key, value, value_type, description, type) VALUES
            ('setting_attention_mechanism', 'attention_mechanism', 'flash_attention', 'string', 'Attention mechanism', 'SYSTEM')
        """)

def down():
    """Remove GPU and attention mechanism settings"""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = 'gpu_max_vram'")
        cursor.execute("DELETE FROM settings WHERE key = 'attention_mechanism'")
