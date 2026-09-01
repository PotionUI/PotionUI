"""
Test the phrasebook import service
"""
import io
import sys
from unittest.mock import patch

import pytest
import yaml

import tests.conftest as ct
from src.features.phrasebook.import_service import (
    PhrasebookImporter,
    PhrasebookImporterRegistry,
    PhrasebookImportService,
    DuplicatePhrasebookImporterError,
    YamlPhrasebookImporter,
    phrasebook_importer_registry,
)
from src.features.phrasebook.repository import (
    phrasebook_category_repo,
    phrasebook_value_repo
)


class _FakeImporter(PhrasebookImporter):
    """A minimal importer used to prove the registration seam."""

    format_id = "fake"

    def __init__(self):
        self.calls = []

    def import_data(self, content, user_id, root_category=None):
        self.calls.append((content, user_id, root_category))
        return {'success': True, 'categories_created': 1, 'values_created': 2}


class TestPhrasebookImporterRegistry:

    def test_yaml_format_is_registered_by_default(self):
        importer = phrasebook_importer_registry.get("yaml")
        assert isinstance(importer, YamlPhrasebookImporter)

    def test_a_second_importer_can_be_registered_and_is_used(self):
        registry = PhrasebookImporterRegistry()
        fake = _FakeImporter()
        registry.register(fake)

        service = PhrasebookImportService(importer_registry=registry, format_id="fake")
        result = service.import_yaml("irrelevant content", "user-xyz", root_category="root")

        assert result == {'success': True, 'categories_created': 1, 'values_created': 2}
        assert fake.calls == [("irrelevant content", "user-xyz", "root")]

    def test_registering_a_duplicate_format_id_raises(self):
        registry = PhrasebookImporterRegistry()
        registry.register(_FakeImporter())

        with pytest.raises(DuplicatePhrasebookImporterError):
            registry.register(_FakeImporter())

    def test_an_unregistered_format_id_fails_without_raising(self):
        service = PhrasebookImportService(
            importer_registry=PhrasebookImporterRegistry(), format_id="unregistered"
        )

        result = service.import_yaml("content", "user-xyz")

        assert result['success'] is False
        assert 'unregistered' in result['error']


class TestPhrasebookImportService:
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup and teardown for each test"""
        from src.platform.util.ids import generate_ulid
        import bcrypt

        # `phrasebook_category_repo`/`phrasebook_value_repo` resolve `db` at
        # call time, so redirecting the canonical name for the whole fixture
        # points them at a fresh, migrated scratch database rather than the
        # real one, which a clean checkout never created.
        test_database = ct.TestDatabase()

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            # Each migration script freshly imports `db` from
            # `database.database` the moment it is exec'd, so that name has
            # to be patched too, not just migration_runner's own.
            with patch('src.platform.database.database.db', test_database), \
                 patch('src.platform.database.migration_runner.db', test_database):
                from src.platform.database.migration_runner import MigrationRunner
                MigrationRunner().run_migrations()
        finally:
            sys.stdout = old_stdout

        with patch('src.platform.database.database.db', test_database):
            # Create a test user if it doesn't exist
            self.test_user = 'test_user_' + generate_ulid()[:8]
            with test_database.get_cursor() as cursor:
                # Create test user
                hashed_password = bcrypt.hashpw('test123'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                cursor.execute("""
                    INSERT INTO users (id, username, email, password_hash, account_type, created_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                """, (self.test_user, 'testuser', 'test@example.com', hashed_password, 'USER'))

            self.service = PhrasebookImportService()

            yield

            # Cleanup after test
            with test_database.get_cursor() as cursor:
                cursor.execute("DELETE FROM phrasebook_values WHERE user_id = ?", (self.test_user,))
                cursor.execute("DELETE FROM phrasebook_categories WHERE user_id = ?", (self.test_user,))
                cursor.execute("DELETE FROM users WHERE id = ?", (self.test_user,))

        test_database.close()
    
    def test_import_nested_dict_format(self):
        """Test importing nested dictionary format (like clothes.yaml)"""
        yaml_content = """
clothes:
  full:
    traditional:
      - kimono
      - hanfu
      - habit
    modern:
      - suit
      - dress
  accessories:
    - hat
    - scarf
"""
        
        result = self.service.import_yaml(yaml_content, self.test_user)
        
        assert result['success'] is True
        assert result['categories_created'] == 5  # clothes, full, traditional, modern, accessories
        assert result['values_created'] == 7  # kimono, hanfu, habit, suit, dress, hat, scarf (7 total)
        
        # Verify categories were created
        categories = phrasebook_category_repo.get_all(self.test_user)
        assert len(categories) == 5
        
        # Verify path structure
        paths = [cat.path for cat in categories]
        assert 'clothes' in paths
        assert 'clothes.full' in paths
        assert 'clothes.full.traditional' in paths
        assert 'clothes.full.modern' in paths
        assert 'clothes.accessories' in paths
        
        # Verify values
        traditional_cat = next(c for c in categories if c.path == 'clothes.full.traditional')
        values = phrasebook_value_repo.get_by_category(traditional_cat.id, self.test_user)
        assert len(values) == 3
        value_labels = [v.label for v in values]
        assert 'kimono' in value_labels
        assert 'hanfu' in value_labels
        assert 'habit' in value_labels
    
    def test_import_label_value_list_format(self):
        """Test importing list of label/value pairs (like emotions.yml)"""
        yaml_content = """
- label: "Happy"
  value: "happy, ^o^, open mouth, xd"
- label: "Sad"
  value: "crying, tears, sad"
- label: "Angry"
  value: "angry, rage, furious"
"""
        
        result = self.service.import_yaml(yaml_content, self.test_user, root_category='emotions')
        
        assert result['success'] is True
        assert result['categories_created'] == 1  # emotions
        assert result['values_created'] == 3  # Happy, Sad, Angry
        
        # Verify category was created
        categories = phrasebook_category_repo.get_all(self.test_user)
        assert len(categories) == 1
        assert categories[0].path == 'emotions'
        
        # Verify values with labels
        values = phrasebook_value_repo.get_by_category(categories[0].id, self.test_user)
        assert len(values) == 3
        
        happy_value = next(v for v in values if v.label == 'Happy')
        assert happy_value.value == 'happy, ^o^, open mouth, xd'
        
        sad_value = next(v for v in values if v.label == 'Sad')
        assert sad_value.value == 'crying, tears, sad'
    
    def test_import_simple_list_format(self):
        """Test importing simple list format"""
        yaml_content = """
- happy
- sad
- angry
- excited
"""
        
        result = self.service.import_yaml(yaml_content, self.test_user, root_category='moods')
        
        assert result['success'] is True
        assert result['categories_created'] == 1  # moods
        assert result['values_created'] == 4  # happy, sad, angry, excited
        
        # Verify values where label equals value
        categories = phrasebook_category_repo.get_all(self.test_user)
        values = phrasebook_value_repo.get_by_category(categories[0].id, self.test_user)
        
        for value in values:
            assert value.label == value.value
    
    def test_import_mixed_nested_format(self):
        """Test importing mixed nested format with both dict and list values"""
        yaml_content = """
styles:
  art:
    - watercolor
    - oil painting
    - sketch
  photo:
    realistic:
      - portrait
      - landscape
    artistic:
      - black and white
      - vintage
"""
        
        result = self.service.import_yaml(yaml_content, self.test_user)
        
        assert result['success'] is True
        assert result['categories_created'] == 5  # styles, art, photo, realistic, artistic
        assert result['values_created'] == 7  # All the leaf values
    
    def test_export_to_yaml(self):
        """Test exporting categories back to YAML"""
        # First import some data
        yaml_content = """
test:
  subcategory:
    - value1
    - value2
"""
        self.service.import_yaml(yaml_content, self.test_user)
        
        # Get the root category
        categories = phrasebook_category_repo.get_all(self.test_user)
        root_category = next(c for c in categories if c.path == 'test')
        
        # Export it
        exported_yaml = self.service.export_to_yaml(root_category.id, self.test_user)
        
        assert exported_yaml is not None
        
        # Parse the exported YAML
        exported_data = yaml.safe_load(exported_yaml)
        
        # Verify structure
        assert 'subcategory' in exported_data
        assert isinstance(exported_data['subcategory'], list)
        assert 'value1' in exported_data['subcategory']
        assert 'value2' in exported_data['subcategory']
    
    def test_import_empty_yaml(self):
        """Test importing empty YAML file"""
        yaml_content = ""
        
        result = self.service.import_yaml(yaml_content, self.test_user)
        
        assert result['success'] is False
        assert 'Empty' in result['error']
    
    def test_import_invalid_yaml(self):
        """Test importing invalid YAML"""
        yaml_content = """
this is not: valid yaml {
  because: it has: multiple: colons
"""
        
        result = self.service.import_yaml(yaml_content, self.test_user)
        
        assert result['success'] is False
        assert 'Invalid YAML' in result['error']
    
    def test_duplicate_category_handling(self):
        """Test that duplicate categories are not created"""
        yaml_content = """
test:
  subcategory:
    - value1
"""
        
        # Import twice
        result1 = self.service.import_yaml(yaml_content, self.test_user)
        result2 = self.service.import_yaml(yaml_content, self.test_user)
        
        # Second import should reuse existing categories
        assert result1['categories_created'] == 2
        assert result2['categories_created'] == 2  # Categories exist, but we count them
        
        # Verify only one set of categories exists
        categories = phrasebook_category_repo.get_all(self.test_user)
        paths = [cat.path for cat in categories]
        assert paths.count('test') == 1
        assert paths.count('test.subcategory') == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])