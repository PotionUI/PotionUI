"""
Remove the orphaned `precision` setting.

`precision` predates `dtype` and duplicated it: `SettingsManager.get_precision()`
defaulted to `"fp16"` while `get_dtype()` defaulted to `"float16"`, and no preset,
pipe, or controller ever read `precision`. Migration 070 moved `dtype` onto the
native backend; this removes the vestigial twin it left behind.

Not folded into 070 because that migration has already been applied on live
databases.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = 'precision'")
        print("Migration 072: removed the orphaned 'precision' setting")


def down():
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT OR REPLACE INTO settings (id, key, value, value_type, description, type)
            VALUES ('setting_precision', 'precision', 'fp16', 'string',
                    'Configuration for precision', 'SYSTEM')
            """
        )
        print("Migration 072: restored the 'precision' setting")
