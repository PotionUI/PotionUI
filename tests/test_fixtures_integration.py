"""
Integration test for test fixtures.

This test file verifies that all fixtures work correctly together
and provides examples of how to use them in tests.
"""

import pytest
from pathlib import Path
from PIL import Image

from src.features.generation.records import Generation
from src.platform.security.user import User, AccountType
from src.features.presets.templates import PresetTemplate


class TestDatabaseFixtures:
    """Test database-related fixtures"""

    def test_test_db_provides_fresh_database(self, test_db):
        """Verify test_db fixture provides a working database"""
        with test_db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM settings")
            result = cursor.fetchone()
            assert result['count'] >= 0

    def test_db_alias_works(self, db):
        """Verify db alias fixture works"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM settings")
            result = cursor.fetchone()
            assert result['count'] >= 0


class TestStorageFixtures:
    """Test storage-related fixtures"""

    def test_test_storage_creates_directories(self, test_storage):
        """Verify test_storage fixture creates proper directory structure"""
        assert test_storage.exists()
        assert (test_storage / "generations").exists()
        assert (test_storage / "tmp").exists()
        assert (test_storage / "models").exists()

    def test_file_service_uses_test_storage(self, file_service, test_storage):
        """Verify file_service uses isolated test storage"""
        assert str(file_service.base_storage_dir) == str(test_storage)
        assert file_service.generations_dir == test_storage / "generations"

    def test_file_service_can_save_files(self, file_service, fake_image_bytes):
        """Verify file_service can save files in test storage"""
        generation_id = "test_gen_123"
        file_path, metadata = file_service.save_file(
            generation_id=generation_id,
            file_data=fake_image_bytes,
            extension="png",
            prefix="0"
        )

        assert file_path is not None
        assert metadata is not None
        assert Path(file_path).exists()
        assert metadata['file_type'] == 'IMAGE'


class TestUserFixtures:
    """Test user-related fixtures"""

    def test_sample_user_is_created(self, sample_user, test_db):
        """Verify sample_user fixture creates a user in the database"""
        assert sample_user.id is not None
        assert sample_user.username == 'testuser'
        assert sample_user.account_type == AccountType.USER

        # Verify in database
        with test_db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE id = ?", (sample_user.id,))
            result = cursor.fetchone()
            assert result is not None
            assert result['username'] == 'testuser'

    def test_sample_admin_user_has_admin_type(self, sample_admin_user):
        """Verify sample_admin_user fixture creates an admin"""
        assert sample_admin_user.account_type == AccountType.ADMIN
        assert sample_admin_user.username == 'adminuser'

    def test_multiple_users_can_coexist(self, sample_user, sample_admin_user, test_db):
        """Verify multiple user fixtures work together"""
        with test_db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM users")
            result = cursor.fetchone()
            assert result['count'] >= 2


class TestGenerationFixtures:
    """Test generation-related fixtures"""

    def test_fake_form_data_structure(self, fake_form_data):
        """Verify fake_form_data provides expected structure"""
        assert 'prompt' in fake_form_data
        assert 'negative_prompt' in fake_form_data
        assert 'width' in fake_form_data
        assert 'height' in fake_form_data
        assert fake_form_data['width'] == 1024
        assert fake_form_data['height'] == 1024

    def test_sample_generation_is_created(self, sample_generation, test_db):
        """Verify sample_generation fixture creates a generation"""
        assert sample_generation.id is not None
        assert sample_generation.status == 'pending'
        assert sample_generation.progress == 0.0

        # Verify in database
        with test_db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM generations WHERE id = ?", (sample_generation.id,))
            result = cursor.fetchone()
            assert result is not None
            assert result['status'] == 'pending'

    def test_sample_generation_with_files_has_files(self, sample_generation_with_files):
        """Verify sample_generation_with_files includes file records"""
        assert len(sample_generation_with_files.files) == 2
        assert all(f.file_type == 'IMAGE' for f in sample_generation_with_files.files)
        assert all(f.is_final for f in sample_generation_with_files.files)


class TestImageFixtures:
    """Test image-related fixtures"""

    def test_fake_image_creates_pil_image(self, fake_image):
        """Verify fake_image creates a valid PIL Image"""
        assert isinstance(fake_image, Image.Image)
        assert fake_image.size == (512, 512)
        assert fake_image.mode == 'RGB'

    def test_fake_image_bytes_are_valid_png(self, fake_image_bytes):
        """Verify fake_image_bytes can be loaded as PNG"""
        assert len(fake_image_bytes) > 0

        # Verify it can be loaded back as an image
        from io import BytesIO
        img = Image.open(BytesIO(fake_image_bytes))
        assert img.size == (512, 512)

    def test_fake_image_1024_has_correct_size(self, fake_image_1024):
        """Verify fake_image_1024 creates 1024x1024 image"""
        assert fake_image_1024.size == (1024, 1024)

    def test_fake_image_rgba_has_alpha_channel(self, fake_image_rgba):
        """Verify fake_image_rgba has RGBA mode"""
        assert fake_image_rgba.mode == 'RGBA'
        assert fake_image_rgba.size == (512, 512)


class TestPresetFixtures:
    """Test preset-related fixtures"""

    def test_sample_preset_template_structure(self, sample_preset_template):
        """Verify sample_preset_template has expected structure"""
        from src.features.presets.templates import GenerationMode

        assert isinstance(sample_preset_template, PresetTemplate)
        assert sample_preset_template.id == 'workbench/sdxl/realistic'
        assert sample_preset_template.name == 'SDXL Realistic'
        assert GenerationMode.TXT2IMG in sample_preset_template.modes
        assert GenerationMode.IMG2IMG in sample_preset_template.modes

    def test_sample_form_template_has_fields(self, sample_form_template):
        """Verify sample_form_template contains form fields"""
        assert sample_form_template.name == 'generation_form'
        assert len(sample_form_template.fields) >= 4
        assert any(f.name == 'prompt' for f in sample_form_template.fields)
        assert any(f.name == 'steps' for f in sample_form_template.fields)

    def test_sample_mode_template_has_pipes(self, sample_mode_template):
        """Verify sample_mode_template contains pipes"""
        assert len(sample_mode_template.pipes) >= 4
        pipe_names = [p.name for p in sample_mode_template.pipes]
        assert 'downloader' in pipe_names
        assert 'generator' in pipe_names


class TestFixtureIsolation:
    """Test that fixtures provide proper isolation between tests"""

    def test_database_isolation_first_test(self, test_db):
        """First test - insert data and verify"""
        with test_db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO users (id, username, email, password_hash)
                VALUES ('test_id_1', 'isolation_test_1', 'test1@example.com', 'hash')
            """)
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE username LIKE 'isolation_test%'")
            result = cursor.fetchone()
            assert result['count'] == 1

    def test_database_isolation_second_test(self, test_db):
        """Second test - verify previous test data doesn't exist"""
        with test_db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE username LIKE 'isolation_test%'")
            result = cursor.fetchone()
            # Should be 0 because each test gets a fresh database
            assert result['count'] == 0

    def test_storage_isolation_first_test(self, test_storage, fake_image_bytes):
        """First test - create file in storage"""
        test_file = test_storage / "tmp" / "test_file_1.png"
        test_file.write_bytes(fake_image_bytes)
        assert test_file.exists()

    def test_storage_isolation_second_test(self, test_storage):
        """Second test - verify previous test file doesn't exist"""
        test_file = test_storage / "tmp" / "test_file_1.png"
        # Should not exist because each test gets fresh storage
        assert not test_file.exists()
