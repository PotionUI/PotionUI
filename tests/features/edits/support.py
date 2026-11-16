from datetime import datetime, timedelta, timezone

from src.features.edits.manager import EditManager
from src.features.edits.repository import EditRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from tests.fixtures.persistence_base import PersistenceTestBase

SOURCE = {"kind": "library", "id": "up_source"}
STATE = {"v": 1, "stack": {"steps": [], "cursor": 0}, "activeTool": None, "viewport": {"fit": True}, "mask": None, "saved": None}


class Clock:
    def __init__(self):
        self.moment = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        self.moment += timedelta(seconds=1)
        return self.moment.isoformat()


class EditsTestBase(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.uploads = UploadRepository()
        self.manager = EditManager(EditRepository(), self.uploads, self.clock)
        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def upload(self, filename, user_id=None):
        return self.uploads.create(Upload(
            user_id=user_id or self.user_id,
            filename=filename,
            original_filename="thumb.png",
            media_type="image",
            mime_type="image/png",
            file_size=10,
        ))

    def row(self, edit_id):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM edits WHERE id = ?", (edit_id,))
            row = cursor.fetchone()
        return dict(row) if row else None
