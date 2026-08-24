"""
Add `mode` and `prompt_state` columns to generations so a past generation's
mode (txt2img/img2img/...) and full prompt state (segments/chips/prompt-relay
timeline/multi-prompt tabs) can be faithfully restored via "Reuse".
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(generations)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'mode' not in columns:
            cursor.execute('''
                ALTER TABLE generations
                ADD COLUMN mode TEXT NOT NULL DEFAULT 'txt2img'
            ''')

        if 'prompt_state' not in columns:
            cursor.execute('''
                ALTER TABLE generations
                ADD COLUMN prompt_state TEXT
            ''')


def down():
    # SQLite doesn't support DROP COLUMN in the version used here; leave columns in
    # place (consistent with other migrations' down()).
    pass
