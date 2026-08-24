"""Tests for IO types documenter."""
import pytest
from src.features.developer.io_types_documenter import IoTypesDocumenter
from src.pipelines.contracts import IOType


class TestIoTypesDocumenter:
    """Test suite for IoTypesDocumenter."""

    def test_initialization(self):
        """Test documenter can be initialized."""
        documenter = IoTypesDocumenter()
        assert documenter is not None
        assert hasattr(documenter, 'IO_TYPE_DESCRIPTIONS')

    def test_get_description_known_type(self):
        """Test getting description for a known IO type."""
        documenter = IoTypesDocumenter()
        description = documenter.get_description(IOType.IMAGE)
        assert description == 'PIL Image object'
        assert description != 'No description available'

    def test_get_description_audio(self):
        """AUDIO is a real IOType (contracts.py) - a text-to-audio pipe
        declaring it should get a real description, not the 'no description'
        fallback."""
        documenter = IoTypesDocumenter()
        description = documenter.get_description(IOType.AUDIO)
        assert description == 'Audio data'
        assert description != 'No description available'

    def test_get_description_unknown_type(self):
        """Test getting description for an unknown IO type."""
        documenter = IoTypesDocumenter()

        # Create a mock IO type with unknown value
        class MockIOType:
            def __init__(self, value):
                self.value = value

        mock_type = MockIOType('UNKNOWN_TYPE')
        description = documenter.get_description(mock_type)
        assert description == 'No description available'

    def test_generate_documentation_structure(self):
        """Test generate_documentation returns correct structure."""
        documenter = IoTypesDocumenter()
        result = documenter.generate_documentation()

        # Check structure
        assert 'io_types' in result
        assert 'total' in result
        assert isinstance(result['io_types'], list)
        assert isinstance(result['total'], int)

    def test_generate_documentation_completeness(self):
        """Test all IO types are documented."""
        documenter = IoTypesDocumenter()
        result = documenter.generate_documentation()

        # Should have documentation for all IOType enum values
        assert result['total'] > 0
        assert len(result['io_types']) == result['total']

        # Check each documented type has required fields
        for io_type_doc in result['io_types']:
            assert 'name' in io_type_doc
            assert 'value' in io_type_doc
            assert 'description' in io_type_doc
            assert isinstance(io_type_doc['name'], str)
            assert isinstance(io_type_doc['value'], str)
            assert isinstance(io_type_doc['description'], str)

    def test_generate_documentation_specific_types(self):
        """Test specific IO types are documented correctly."""
        documenter = IoTypesDocumenter()
        result = documenter.generate_documentation()

        # Find specific types
        image_type = next(
            (t for t in result['io_types'] if t['value'] == 'IMAGE'),
            None
        )
        model_type = next(
            (t for t in result['io_types'] if t['value'] == 'MODEL'),
            None
        )

        assert image_type is not None
        assert image_type['description'] == 'PIL Image object'

        assert model_type is not None
        assert model_type['description'] == 'AI model (checkpoint)'

    def test_all_descriptions_have_content(self):
        """Test no descriptions are empty."""
        documenter = IoTypesDocumenter()
        result = documenter.generate_documentation()

        for io_type_doc in result['io_types']:
            assert io_type_doc['description']
            assert len(io_type_doc['description']) > 0

    def test_io_type_descriptions_coverage(self):
        """Test IO_TYPE_DESCRIPTIONS covers common types."""
        documenter = IoTypesDocumenter()

        # Common types that should be documented
        common_types = [
            'INT', 'FLOAT', 'IMAGE', 'MODEL', 'CLIP', 'VAE',
            'P_PROMPT', 'N_PROMPT', 'SEED', 'CFG', 'SAMPLER'
        ]

        for type_name in common_types:
            assert type_name in documenter.IO_TYPE_DESCRIPTIONS
            assert len(documenter.IO_TYPE_DESCRIPTIONS[type_name]) > 0
