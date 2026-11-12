from src.features.kits.repository import KitRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from tests.fixtures.persistence_base import PersistenceTestBase


class KitsTestBase(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = KitRepository()
        self.uploads = UploadRepository()
        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def upload(self, filename, media_type="image", user_id=None, original="source.png"):
        return self.uploads.create(Upload(
            user_id=user_id or self.user_id,
            filename=filename,
            original_filename=original,
            media_type=media_type,
            mime_type=f"{media_type}/x",
            file_size=10,
            thumbnail_small=f"thumb_{filename}",
        ))

    def delete_upload(self, upload, user_id=None):
        return self.uploads.delete(upload.filename, user_id or self.user_id)
