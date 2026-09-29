from src.platform.database.database import db

_NAME = "040_rename_nsfw_setting"
_DESCRIPTION = "Allow NSFW content generation"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM settings WHERE key = 'nsfw'")
        if cursor.fetchone() is not None:
            cursor.execute("DELETE FROM settings WHERE key = 'nsfw_filter'")
        else:
            cursor.execute(
                "UPDATE settings SET key = 'nsfw', description = ? WHERE key = 'nsfw_filter'",
                (_DESCRIPTION,),
            )
    print(f"Migration {_NAME}: renamed settings key nsfw_filter to nsfw")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM settings WHERE key = 'nsfw_filter'")
        if cursor.fetchone() is None:
            cursor.execute("UPDATE settings SET key = 'nsfw_filter' WHERE key = 'nsfw'")
    print(f"Migration {_NAME}: renamed settings key nsfw back to nsfw_filter")
