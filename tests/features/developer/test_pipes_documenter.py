"""Tests for pipes documenter."""
import pytest
from unittest.mock import Mock, MagicMock
from src.features.developer.pipes_documenter import PipesDocumenter
from src.pipelines.contracts import IOType, PipeInputSpec, PipeOutputSpec, PipeStatus


class TestPipesDocumenter:
    """Test suite for PipesDocumenter."""

    @pytest.fixture
    def mock_pipe_catalog(self):
        """Create a mock pipe registry."""
        registry = Mock()
        registry.pipes = {}
        registry.get_pipe_status = Mock(return_value=PipeStatus.INSTALLED)
        registry.discover_pipes = Mock()
        return registry

    @pytest.fixture
    def mock_pipe_class(self):
        """Create a mock pipe class with all required attributes."""
        pipe_class = Mock()
        pipe_class.__doc__ = "Test pipe documentation"
        pipe_class.description = "Test pipe description"
        pipe_class.get_default_config = Mock(return_value={'key': 'value'})

        # Mock inputs
        input_spec = PipeInputSpec(
            name='input_image',
            io_type=IOType.IMAGE,
            required=True,
            description='Input image',
            is_array=False
        )
        pipe_class.inputs = Mock(return_value=[input_spec])

        # Mock outputs
        output_spec = PipeOutputSpec(
            name='output_image',
            io_type=IOType.IMAGE,
            description='Output image',
            is_array=False
        )
        pipe_class.outputs = Mock(return_value=[output_spec])

        # Mock configuration
        config_spec = Mock()
        config_spec.name = 'steps'
        config_spec.param_type = int
        config_spec.default = 25
        config_spec.description = 'Number of steps'
        config_spec.required = True
        config_spec.choices = None
        config_spec.min_value = 1
        config_spec.max_value = 100
        pipe_class.configuration = Mock(return_value=[config_spec])

        # Mock requirements
        pipe_class.get_requirements = Mock(return_value={'gpu': True})

        return pipe_class

    def test_initialization(self, mock_pipe_catalog):
        """Test documenter can be initialized."""
        documenter = PipesDocumenter(mock_pipe_catalog)
        assert documenter is not None
        assert documenter.pipe_catalog == mock_pipe_catalog

    def test_document_pipe_basic_info(self, mock_pipe_catalog, mock_pipe_class):
        """Test documenting a pipe returns basic info."""
        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter._document_pipe('test_pipe', mock_pipe_class)

        assert 'name' in result
        assert 'description' in result
        assert 'status' in result
        assert 'default_config' in result

        assert result['name'] == 'test_pipe'
        assert result['description'] == 'Test pipe description'
        assert result['default_config'] == {'key': 'value'}

    def test_document_pipe_inputs(self, mock_pipe_catalog, mock_pipe_class):
        """Test documenting pipe inputs."""
        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter._document_pipe('test_pipe', mock_pipe_class)

        assert 'inputs' in result
        assert len(result['inputs']) == 1

        input_doc = result['inputs'][0]
        assert input_doc['name'] == 'input_image'
        assert input_doc['io_type'] == 'IMAGE'
        assert input_doc['required'] is True
        assert input_doc['description'] == 'Input image'
        assert input_doc['is_array'] is False

    def test_document_pipe_outputs(self, mock_pipe_catalog, mock_pipe_class):
        """Test documenting pipe outputs."""
        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter._document_pipe('test_pipe', mock_pipe_class)

        assert 'outputs' in result
        assert len(result['outputs']) == 1

        output_doc = result['outputs'][0]
        assert output_doc['name'] == 'output_image'
        assert output_doc['io_type'] == 'IMAGE'
        assert output_doc['description'] == 'Output image'
        assert output_doc['is_array'] is False

    def test_document_pipe_configuration(self, mock_pipe_catalog, mock_pipe_class):
        """Test documenting pipe configuration."""
        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter._document_pipe('test_pipe', mock_pipe_class)

        assert 'configuration' in result
        assert len(result['configuration']) == 1

        config_doc = result['configuration'][0]
        assert config_doc['name'] == 'steps'
        assert config_doc['param_type'] == 'int'
        assert config_doc['default'] == 25
        assert config_doc['description'] == 'Number of steps'
        assert config_doc['required'] is True
        assert config_doc['min_value'] == 1
        assert config_doc['max_value'] == 100

    def test_document_pipe_requirements(self, mock_pipe_catalog, mock_pipe_class):
        """Test documenting pipe requirements."""
        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter._document_pipe('test_pipe', mock_pipe_class)

        assert 'requirements' in result
        assert result['requirements'] == {'gpu': True}

    def test_document_pipe_without_optional_attributes(self, mock_pipe_catalog):
        """Test documenting a pipe without optional attributes."""
        # Create a proper minimal pipe class
        class MinimalPipe:
            __doc__ = "Minimal pipe"

            @staticmethod
            def get_default_config():
                return {}

        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter._document_pipe('minimal_pipe', MinimalPipe)

        assert result['name'] == 'minimal_pipe'
        assert result['description'] == 'Minimal pipe'
        assert result['inputs'] == []
        assert result['outputs'] == []
        assert result['configuration'] == []
        assert result['requirements'] == {}

    def test_generate_documentation_empty_registry(self, mock_pipe_catalog):
        """Test generating documentation with empty pipe registry."""
        mock_pipe_catalog.pipes = {}

        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter.generate_documentation()

        assert 'pipes' in result
        assert 'total' in result
        assert result['pipes'] == []
        assert result['total'] == 0

        # Should trigger discovery
        mock_pipe_catalog.discover_pipes.assert_called_once()

    def test_generate_documentation_with_pipes(self, mock_pipe_catalog, mock_pipe_class):
        """Test generating documentation with pipes."""
        mock_pipe_catalog.pipes = {
            'test_pipe_1': mock_pipe_class,
            'test_pipe_2': mock_pipe_class
        }

        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter.generate_documentation()

        assert 'pipes' in result
        assert 'total' in result
        assert len(result['pipes']) == 2
        assert result['total'] == 2

    def test_generate_documentation_handles_errors(self, mock_pipe_catalog):
        """Test generating documentation handles pipe documentation errors gracefully."""
        # Create a pipe that will raise an error when documented
        error_pipe = Mock()
        error_pipe.get_default_config = Mock(side_effect=Exception("Test error"))

        mock_pipe_catalog.pipes = {'error_pipe': error_pipe}

        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter.generate_documentation()

        # Should still return a result
        assert 'pipes' in result
        assert len(result['pipes']) == 1

        # Error pipe should have error status
        error_doc = result['pipes'][0]
        assert error_doc['name'] == 'error_pipe'
        assert 'Error documenting pipe' in error_doc['description']
        assert error_doc['status'] == 'error'

    def test_document_pipe_without_description(self, mock_pipe_catalog):
        """Test documenting a pipe without description attribute."""
        # Create a proper minimal pipe class without description
        class NoDescPipe:
            __doc__ = None

            @staticmethod
            def get_default_config():
                return {}

        documenter = PipesDocumenter(mock_pipe_catalog)
        result = documenter._document_pipe('no_desc_pipe', NoDescPipe)

        assert result['description'] == 'No description available'


class TestManualInstallIsCarried:
    """The reference is where a NOT_INSTALLED pipe is read, so it is where a
    pipe that cannot be installed automatically has to say so.

    Real `BasePipe` subclasses rather than Mocks: a Mock returns whatever it is
    told to, which would prove only that this test configured it.
    """

    def _catalog(self, pipe_class, name):
        catalog = Mock()
        catalog.pipes = {name: pipe_class}
        catalog.get_pipe_status = Mock(return_value=PipeStatus.NOT_INSTALLED)
        catalog.discover_pipes = Mock()
        return catalog

    def test_a_pipe_built_from_source_publishes_its_commands(self):
        from src.pipelines.contracts import BasePipe

        class SourceBuiltPipe(BasePipe):
            name = 'source_built'
            description = 'Needs a compiler'

            def process(self, pipe_input, generation_outputs):
                return None

            @classmethod
            def get_default_config(cls):
                return {}

            @classmethod
            def inputs(cls):
                return []

            @classmethod
            def outputs(cls):
                return []

            @classmethod
            def configuration(cls):
                return []

            @classmethod
            def manual_install_instructions(cls):
                return 'Build it: . ./setup.sh --cumesh'

        documenter = PipesDocumenter(self._catalog(SourceBuiltPipe, 'source_built'))
        docs = documenter.generate_documentation()

        assert docs['pipes'][0]['manual_install'] == 'Build it: . ./setup.sh --cumesh'

    def test_an_ordinary_pipe_publishes_no_instructions(self):
        from src.pipelines.contracts import BasePipe

        class OrdinaryPipe(BasePipe):
            name = 'ordinary'
            description = 'pip can handle this one'

            def process(self, pipe_input, generation_outputs):
                return None

            @classmethod
            def get_default_config(cls):
                return {}

            @classmethod
            def inputs(cls):
                return []

            @classmethod
            def outputs(cls):
                return []

            @classmethod
            def configuration(cls):
                return []

        documenter = PipesDocumenter(self._catalog(OrdinaryPipe, 'ordinary'))
        docs = documenter.generate_documentation()

        assert docs['pipes'][0]['manual_install'] is None
