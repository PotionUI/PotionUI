import unittest
from contextlib import contextmanager
from datetime import datetime
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.records import File, GenerationFile
from src.features.generation.file_repository import FileRepository
try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    # Mock for testing
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


def _count_cursor_executes(db_instance, fn):
    """Run `fn()` and return how many `cursor.execute` calls it issued.

    `sqlite3.Cursor` is a C type and refuses attribute patching, so the count
    is taken by wrapping the `Database.get_cursor()` context manager instead
    of the cursor class itself.
    """
    counter = {"n": 0}
    original_get_cursor = db_instance.get_cursor

    class _CountingCursor:
        def __init__(self, cursor):
            self._cursor = cursor

        def execute(self, *args, **kwargs):
            counter["n"] += 1
            return self._cursor.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._cursor, name)

    @contextmanager
    def counting_get_cursor():
        with original_get_cursor() as cursor:
            yield _CountingCursor(cursor)

    db_instance.get_cursor = counting_get_cursor
    try:
        fn()
    finally:
        db_instance.get_cursor = original_get_cursor
    return counter["n"]


class TestFileRepository(PersistenceTestBase):
    
    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = FileRepository()
        self.test_user_id = self.create_test_user()
        
        self.test_file = File(
            file_path="/test/image.jpg",
            file_type="image",
            user_id=self.test_user_id,
            file_size=1024,
            pipe_name="generator",
            is_final=True
        )
    
    def test_create_file(self):
        """Test creating a new file"""
        created = self.repo.create(self.test_file)
        
        self.assertIsNotNone(created)
        self.assertIsNotNone(created.id)
        self.assertEqual(created.file_path, self.test_file.file_path)
        self.assertEqual(created.file_type, self.test_file.file_type)
        self.assertEqual(created.user_id, self.test_file.user_id)
        self.assertEqual(created.file_size, self.test_file.file_size)
        self.assertEqual(created.pipe_name, self.test_file.pipe_name)
        self.assertEqual(created.is_final, self.test_file.is_final)
        self.assertIsNotNone(created.created_at)
    
    def test_create_file_is_derived_defaults_false(self):
        created = self.repo.create(self.test_file)

        self.assertIs(created.is_derived, False)
        self.assertIs(created.to_dict()['is_derived'], False)

    def test_create_file_persists_is_derived(self):
        self.test_file.is_derived = True
        created = self.repo.create(self.test_file)

        self.assertIs(created.is_derived, True)

        retrieved = self.repo.get_by_id(created.id)
        self.assertIs(retrieved.is_derived, True)
        self.assertIs(retrieved.to_dict()['is_derived'], True)

    def test_create_file_with_existing_id(self):
        """Test creating a file with predefined ID"""
        file_id = generate_ulid()
        self.test_file.id = file_id
        
        created = self.repo.create(self.test_file)
        
        self.assertEqual(created.id, file_id)
    
    def test_get_by_id(self):
        """Test getting file by ID"""
        created = self.repo.create(self.test_file)
        retrieved = self.repo.get_by_id(created.id)
        
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.id, created.id)
        self.assertEqual(retrieved.file_path, created.file_path)
        self.assertEqual(retrieved.file_type, created.file_type)
        self.assertEqual(retrieved.user_id, created.user_id)
    
    def test_get_by_id_with_user_filter(self):
        """Test getting file by ID with user filter"""
        created = self.repo.create(self.test_file)
        
        # Should find with correct user_id
        retrieved = self.repo.get_by_id(created.id, user_id=self.test_user_id)
        self.assertIsNotNone(retrieved)
        
        # Should not find with incorrect user_id
        retrieved = self.repo.get_by_id(created.id, user_id="wrong_user")
        self.assertIsNone(retrieved)
    
    def test_get_by_id_nonexistent(self):
        """Test getting nonexistent file"""
        result = self.repo.get_by_id("nonexistent_id")
        self.assertIsNone(result)
    
    def test_get_all(self):
        """Test getting all files"""
        # Create multiple files
        file1 = self.repo.create(self.test_file)
        
        file2_data = File(
            file_path="/test/video.mp4",
            file_type="video",
            user_id=self.test_user_id,
            file_size=2048,
            pipe_name="upscaler",
            is_final=False
        )
        file2 = self.repo.create(file2_data)
        
        all_files = self.repo.get_all()
        
        self.assertGreaterEqual(len(all_files), 2)
        file_ids = [f.id for f in all_files]
        self.assertIn(file1.id, file_ids)
        self.assertIn(file2.id, file_ids)
    
    def test_get_all_with_user_filter(self):
        """Test getting files filtered by user"""
        # Create file for test user
        file1 = self.repo.create(self.test_file)
        
        # Create another user and file
        other_user_id = self.create_test_user("other_user", "other@example.com", "other@example.com")
        file2_data = File(
            file_path="/test/other.jpg",
            file_type="image",
            user_id=other_user_id
        )
        file2 = self.repo.create(file2_data)
        
        # Filter by first user
        user_files = self.repo.get_all(user_id=self.test_user_id)
        file_ids = [f.id for f in user_files]
        
        self.assertIn(file1.id, file_ids)
        self.assertNotIn(file2.id, file_ids)
    
    def test_get_all_with_file_type_filter(self):
        """Test getting files filtered by file type"""
        # Create files with different types
        image_file = self.repo.create(self.test_file)
        
        video_file_data = File(
            file_path="/test/video.mp4",
            file_type="video",
            user_id=self.test_user_id
        )
        video_file = self.repo.create(video_file_data)
        
        # Filter by file type
        image_files = self.repo.get_all(file_type="image")
        video_files = self.repo.get_all(file_type="video")
        
        image_ids = [f.id for f in image_files]
        video_ids = [f.id for f in video_files]
        
        self.assertIn(image_file.id, image_ids)
        self.assertNotIn(image_file.id, video_ids)
        self.assertIn(video_file.id, video_ids)
        self.assertNotIn(video_file.id, image_ids)
    
    def test_get_all_with_limit_offset(self):
        """Test getting files with pagination"""
        # Create multiple files
        files = []
        for i in range(5):
            file_data = File(
                file_path=f"/test/file_{i}.jpg",
                file_type="image",
                user_id=self.test_user_id
            )
            files.append(self.repo.create(file_data))
        
        # Test limit
        limited = self.repo.get_all(limit=3)
        self.assertEqual(len(limited), 3)
        
        # Test offset
        offset_results = self.repo.get_all(limit=2, offset=2)
        self.assertEqual(len(offset_results), 2)
        
        # Results should be different due to offset
        limited_ids = {f.id for f in limited[:2]}
        offset_ids = {f.id for f in offset_results}
        self.assertNotEqual(limited_ids, offset_ids)
    
    def test_delete(self):
        """Test deleting file"""
        created = self.repo.create(self.test_file)
        
        success = self.repo.delete(created.id)
        self.assertTrue(success)
        
        # Should not be found after deletion
        deleted = self.repo.get_by_id(created.id)
        self.assertIsNone(deleted)
    
    def test_delete_nonexistent(self):
        """Test deleting nonexistent file"""
        success = self.repo.delete("nonexistent_id")
        self.assertFalse(success)
    
    def test_associate_with_generation(self):
        """Test associating file with generation"""
        # Create test file and generation
        file = self.repo.create(self.test_file)
        generation_id = self.create_test_generation(user_id=self.test_user_id)
        
        # Associate file with generation
        association = self.repo.associate_with_generation(generation_id, file.id)
        
        self.assertIsNotNone(association)
        self.assertIsNotNone(association.id)
        self.assertEqual(association.generation_id, generation_id)
        self.assertEqual(association.file_id, file.id)
        self.assertIsNotNone(association.created_at)
    
    def test_get_generation_files(self):
        """Test getting files associated with a generation"""
        # Create test files and generation
        file1 = self.repo.create(self.test_file)
        
        file2_data = File(
            file_path="/test/other.jpg",
            file_type="image",
            user_id=self.test_user_id,
            is_final=False
        )
        file2 = self.repo.create(file2_data)
        
        generation_id = self.create_test_generation(user_id=self.test_user_id)
        
        # Associate files with generation
        self.repo.associate_with_generation(generation_id, file1.id)
        self.repo.associate_with_generation(generation_id, file2.id)
        
        # Get generation files
        gen_files = self.repo.get_generation_files(generation_id)
        
        self.assertEqual(len(gen_files), 2)
        file_ids = [f.id for f in gen_files]
        self.assertIn(file1.id, file_ids)
        self.assertIn(file2.id, file_ids)
    
    def test_get_generation_files_with_filters(self):
        """Test getting generation files with various filters"""
        # Create test files with different properties
        image_final = File(
            file_path="/test/final.jpg",
            file_type="image",
            user_id=self.test_user_id,
            is_final=True
        )
        image_final = self.repo.create(image_final)
        
        image_temp = File(
            file_path="/test/temp.jpg",
            file_type="image",
            user_id=self.test_user_id,
            is_final=False
        )
        image_temp = self.repo.create(image_temp)
        
        video_file = File(
            file_path="/test/video.mp4",
            file_type="video",
            user_id=self.test_user_id,
            is_final=True
        )
        video_file = self.repo.create(video_file)
        
        generation_id = self.create_test_generation(user_id=self.test_user_id)
        
        # Associate all files with generation
        self.repo.associate_with_generation(generation_id, image_final.id)
        self.repo.associate_with_generation(generation_id, image_temp.id)
        self.repo.associate_with_generation(generation_id, video_file.id)
        
        # Test file_type filter
        image_files = self.repo.get_generation_files(generation_id, file_type="image")
        self.assertEqual(len(image_files), 2)
        
        video_files = self.repo.get_generation_files(generation_id, file_type="video")
        self.assertEqual(len(video_files), 1)
        
        # Test is_final filter
        final_files = self.repo.get_generation_files(generation_id, is_final=True)
        self.assertEqual(len(final_files), 2)
        
        temp_files = self.repo.get_generation_files(generation_id, is_final=False)
        self.assertEqual(len(temp_files), 1)
        
        # Test user_id filter
        user_files = self.repo.get_generation_files(generation_id, user_id=self.test_user_id)
        self.assertEqual(len(user_files), 3)
        
        other_user_files = self.repo.get_generation_files(generation_id, user_id="other_user")
        self.assertEqual(len(other_user_files), 0)
    
    def test_remove_generation_association(self):
        """Test removing association between generation and file"""
        # Create test file and generation
        file = self.repo.create(self.test_file)
        generation_id = self.create_test_generation(user_id=self.test_user_id)
        
        # Associate file with generation
        self.repo.associate_with_generation(generation_id, file.id)
        
        # Verify association exists
        gen_files = self.repo.get_generation_files(generation_id)
        self.assertEqual(len(gen_files), 1)
        
        # Remove association
        success = self.repo.remove_generation_association(generation_id, file.id)
        self.assertTrue(success)
        
        # Verify association is removed
        gen_files = self.repo.get_generation_files(generation_id)
        self.assertEqual(len(gen_files), 0)
    
    def test_remove_nonexistent_association(self):
        """Test removing nonexistent association"""
        success = self.repo.remove_generation_association("nonexistent_gen", "nonexistent_file")
        self.assertFalse(success)

    def test_get_generation_files_bulk_empty_for_generation_with_no_files(self):
        """Every requested generation id must be present in the result, even
        with an empty list, not simply absent."""
        empty_generation_id = self.create_test_generation(generation_id="gen_empty", user_id=self.test_user_id)
        other_generation_id = self.create_test_generation(generation_id="gen_other", user_id=self.test_user_id)
        file = self.repo.create(self.test_file)
        self.repo.associate_with_generation(other_generation_id, file.id)

        bulk = self.repo.get_generation_files_bulk([empty_generation_id, other_generation_id])

        self.assertEqual(bulk[empty_generation_id], [])
        self.assertEqual([f.id for f in bulk[other_generation_id]], [file.id])

    def test_get_generation_files_bulk_matches_single_lookup_order_with_ties(self):
        """`get_generation_files` breaks created_at ties by id (see its
        docstring) so carousels/parameter rows keep save order. The batched
        query must reproduce that per-generation order exactly."""
        generation_a = self.create_test_generation(generation_id="gen_a", user_id=self.test_user_id)
        generation_b = self.create_test_generation(generation_id="gen_b", user_id=self.test_user_id)

        with self.db.get_cursor() as cursor:
            # file_a2 saved before file_a1 but ties with it on created_at -
            # the id ordering must still put file_a1 first.
            cursor.execute(
                "INSERT INTO files (id, file_path, file_type, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                ("file_a2", "/test/a2.jpg", "image", self.test_user_id, "2026-01-01 00:00:00"),
            )
            cursor.execute(
                "INSERT INTO files (id, file_path, file_type, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                ("file_a1", "/test/a1.jpg", "image", self.test_user_id, "2026-01-01 00:00:00"),
            )
            cursor.execute(
                "INSERT INTO files (id, file_path, file_type, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                ("file_b1", "/test/b1.jpg", "image", self.test_user_id, "2026-01-01 00:00:01"),
            )

        self.repo.associate_with_generation(generation_a, "file_a2")
        self.repo.associate_with_generation(generation_a, "file_a1")
        self.repo.associate_with_generation(generation_b, "file_b1")

        expected_a = [f.id for f in self.repo.get_generation_files(generation_a)]
        expected_b = [f.id for f in self.repo.get_generation_files(generation_b)]
        self.assertEqual(expected_a, ["file_a1", "file_a2"])  # sanity: the tie really exists

        bulk = self.repo.get_generation_files_bulk([generation_a, generation_b])

        self.assertEqual([f.id for f in bulk[generation_a]], expected_a)
        self.assertEqual([f.id for f in bulk[generation_b]], expected_b)

    def test_get_generation_files_bulk_preserves_user_id_filter(self):
        """The user_id filter must still be applied in the batched query, not
        just carried as an unused parameter."""
        generation_id = self.create_test_generation(generation_id="gen_shared", user_id=self.test_user_id)
        own_file = self.repo.create(self.test_file)

        other_user_id = self.create_test_user("other_user", "other@example.com", "other@example.com")
        other_file = self.repo.create(File(
            file_path="/test/other.jpg",
            file_type="image",
            user_id=other_user_id,
        ))

        self.repo.associate_with_generation(generation_id, own_file.id)
        self.repo.associate_with_generation(generation_id, other_file.id)

        bulk = self.repo.get_generation_files_bulk([generation_id], user_id=self.test_user_id)

        self.assertEqual([f.id for f in bulk[generation_id]], [own_file.id])

    def test_get_generation_files_bulk_issues_constant_query_count(self):
        """The regression guard: query count must not scale with generation
        count, which is exactly the N+1 the batching replaced."""

        def make_generation_with_file(gen_id):
            generation_id = self.create_test_generation(generation_id=gen_id, user_id=self.test_user_id)
            file = self.repo.create(File(
                file_path=f"/test/{gen_id}.jpg",
                file_type="image",
                user_id=self.test_user_id,
            ))
            self.repo.associate_with_generation(generation_id, file.id)
            return generation_id

        small_ids = [make_generation_with_file(f"small_{i}") for i in range(3)]
        large_ids = [make_generation_with_file(f"large_{i}") for i in range(40)]

        small_query_count = _count_cursor_executes(
            self.db, lambda: self.repo.get_generation_files_bulk(small_ids)
        )
        large_query_count = _count_cursor_executes(
            self.db, lambda: self.repo.get_generation_files_bulk(large_ids)
        )

        self.assertEqual(small_query_count, large_query_count)
        self.assertEqual(small_query_count, 1)

    def test_create_persists_thumbnails_and_dimensions(self):
        """Thumbnails/dimensions are written at create() time - the path that replaced
        the removed update_thumbnails. Exercised against the real migrated schema so a
        column mismatch (as the old `files.updated_at` write was) can't go untested."""
        thumbed = File(
            file_path="/test/thumbed.png",
            file_type="IMAGE",
            user_id=self.test_user_id,
            thumbnail_small="thumbnails/x_small.webp",
            thumbnail_medium="thumbnails/x_medium.webp",
            thumbnail_large="thumbnails/x_large.webp",
            width=1200,
            height=900,
        )
        created = self.repo.create(thumbed)
        retrieved = self.repo.get_by_id(created.id)

        self.assertEqual(retrieved.thumbnail_small, "thumbnails/x_small.webp")
        self.assertEqual(retrieved.thumbnail_medium, "thumbnails/x_medium.webp")
        self.assertEqual(retrieved.thumbnail_large, "thumbnails/x_large.webp")
        self.assertEqual(retrieved.width, 1200)
        self.assertEqual(retrieved.height, 900)

    def test_create_persists_mime_type(self):
        """mime_type is carried by File and set by the upload path, but was left
        out of the INSERT column list - every row landed with a NULL mime_type
        while the in-memory File still reported the right one. Read the row back
        rather than trusting create()'s return value."""
        typed = File(
            file_path="/test/clip.mp4",
            file_type="VIDEO",
            user_id=self.test_user_id,
            mime_type="video/mp4",
        )
        created = self.repo.create(typed)

        self.assertEqual(created.mime_type, "video/mp4")

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT mime_type FROM files WHERE id = ?", (created.id,))
            self.assertEqual(cursor.fetchone()["mime_type"], "video/mp4")

        self.assertEqual(self.repo.get_by_id(created.id).mime_type, "video/mp4")

    def test_create_without_mime_type_stores_null(self):
        created = self.repo.create(self.test_file)

        self.assertIsNone(self.repo.get_by_id(created.id).mime_type)

    def test_set_thumbnail_paths_updates_all_matching_files(self):
        """Duplicate video file records (same filename, two saves) both get the
        new thumbnail paths in one call."""
        first = self.repo.create(self.test_file)
        second = self.repo.create(File(
            file_path="/test/image.jpg",
            file_type="image",
            user_id=self.test_user_id,
        ))

        updated_count = self.repo.set_thumbnail_paths(
            [first.id, second.id], "small.jpg", "medium.jpg", "large.jpg"
        )

        self.assertEqual(updated_count, 2)
        for file_id in (first.id, second.id):
            retrieved = self.repo.get_by_id(file_id)
            self.assertEqual(retrieved.thumbnail_small, "small.jpg")
            self.assertEqual(retrieved.thumbnail_medium, "medium.jpg")
            self.assertEqual(retrieved.thumbnail_large, "large.jpg")

    def test_debug_recent_generation_files_reproduces_the_stale_column_bug(self):
        """`generation_files` has been a bare junction table (id, generation_id,
        file_id, created_at) since migration 010, but this debug dump still
        reads file_path/file_type/file_size/pipe_name/is_final off its rows -
        columns that live on `files`, not `generation_files`. That query
        predates this move; it stays byte-for-byte so the move itself changes
        nothing, not even the bug. The caller (the debug route) already turns
        this into a JSON error response."""
        generation_id = self.create_test_generation(user_id=self.test_user_id)
        file_id = self.repo.create(File(
            file_path="/test/f0.jpg", file_type="image", user_id=self.test_user_id,
        )).id
        self.repo.associate_with_generation(generation_id, file_id)

        with self.assertRaises(Exception):
            self.repo.debug_recent_generation_files(limit=2)

    def test_files_schema_has_no_updated_at(self):
        """The fresh `files` schema has no `updated_at` column. The removed
        update_thumbnails wrote `updated_at = CURRENT_TIMESTAMP` and raised
        OperationalError for every caller; guard against reintroducing either."""
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(files)")
            columns = {row[1] for row in cursor.fetchall()}

        self.assertNotIn("updated_at", columns)
        self.assertFalse(hasattr(self.repo, "update_thumbnails"))


if __name__ == '__main__':
    unittest.main()