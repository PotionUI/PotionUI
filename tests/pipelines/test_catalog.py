import unittest
import asyncio
import tempfile
import shutil
import os
import sys
import pytest
from unittest.mock import Mock, patch, MagicMock, call, AsyncMock
from typing import Dict, Any, List

from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    PipeStatus,
    PipeInput,
    PipeOutput,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
    IOType,
)


class TestBasePipe(unittest.TestCase):
    """Test cases for the BasePipe abstract class"""
    
    def test_cannot_instantiate_base_pipe(self):
        """Test that BasePipe cannot be instantiated directly"""
        with self.assertRaises(TypeError):
            BasePipe({})
    
    def test_base_pipe_has_required_abstract_methods(self):
        """Test that BasePipe defines all required abstract methods.

        `name`/`description` are declared as `ClassVar[str]`, not
        `@property @abstractmethod`: every real pipe sets them as plain class
        attributes and `PipeCatalog` reads them off the class, so an
        abstract *property* would register a class following that contract
        literally under a `property` object instead of a string key.
        """
        abstract_methods = BasePipe.__abstractmethods__
        expected_methods = {
            'process', 'get_default_config',
            'inputs', 'outputs', 'configuration'
        }
        self.assertEqual(abstract_methods, expected_methods)
        self.assertNotIn('name', abstract_methods)
        self.assertNotIn('description', abstract_methods)


class MockPipe(BasePipe):
    """Mock pipe implementation for testing"""
    
    name = 'mock_pipe'  # Class-level name attribute
    description = 'A mock pipe for testing'  # Class-level description attribute
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
    
    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        return PipeOutput(output={'result': 'test_output'})
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {'param1': 'default_value', 'param2': 42}
    
    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec(name='input1', io_type=IOType.TEXT, required=True),
            PipeInputSpec(name='input2', io_type=IOType.INT, required=False)
        ]
    
    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec(name='result', io_type=IOType.TEXT)
        ]
    
    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(name='param1', param_type=str, default='default_value'),
            PipeConfigSpec(name='param2', param_type=int, default=42, min_value=0, max_value=100)
        ]
    
    @classmethod
    def get_requirements(cls) -> Dict[str, Any]:
        return {
            'pip': ['test-package==1.0.0'],
            'git': [{'url': 'https://github.com/test/repo.git', 'path': '/tmp/test-repo'}],
            'models': [{'path': '/models/test-model.bin'}]
        }


class MockPipeWithError(BasePipe):
    """Mock pipe that raises error during loading"""
    
    def __init__(self, config: Dict[str, Any]):
        raise Exception("Test error during pipe initialization")
    
    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        pass
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {}
    
    @property
    def name(self) -> str:
        return 'error_pipe'
    
    @property
    def description(self) -> str:
        return 'A pipe that errors'
    
    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return []
    
    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return []
    
    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return []


class TestPipeCatalog(unittest.TestCase):
    """Test cases for the PipeCatalog class"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.core_pipes_path = os.path.join(self.temp_dir, 'core_pipes')
        self.custom_pipes_path = os.path.join(self.temp_dir, 'custom_pipes')
        
        os.makedirs(self.core_pipes_path, exist_ok=True)
        os.makedirs(self.custom_pipes_path, exist_ok=True)
        
        self.registry = PipeCatalog(self.core_pipes_path, self.custom_pipes_path)
    
    def tearDown(self):
        """Clean up test environment"""
        shutil.rmtree(self.temp_dir)
    
    def test_catalog_initialization(self):
        """Test PipeCatalog initialization"""
        self.assertEqual(self.registry.core_pipes_path, self.core_pipes_path)
        self.assertEqual(self.registry.custom_pipes_path, self.custom_pipes_path)
        self.assertEqual(self.registry.pipes, {})
        self.assertEqual(self.registry.pipe_status, {})
    
    def test_get_available_pipes_empty(self):
        """Test getting available pipes when none are loaded"""
        pipes = self.registry.get_available_pipes()
        self.assertEqual(pipes, [])
    
    def test_get_pipe_not_found(self):
        """Test getting a pipe that doesn't exist"""
        pipe = self.registry.get_pipe('nonexistent')
        self.assertIsNone(pipe)
    
    def test_get_pipe_status_not_installed(self):
        """Test getting status of non-existent pipe"""
        status = self.registry.get_pipe_status('nonexistent')
        self.assertEqual(status, PipeStatus.NOT_INSTALLED)
    
    def create_mock_pipe_file(self, path: str, pipe_class_name: str = 'MockPipe'):
        """Helper method to create a mock pipe file"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        pipe_content = f'''
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
    IOType,
)
from typing import Dict, Any, List

class {pipe_class_name}(BasePipe):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
    
    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        return PipeOutput(output={{'result': 'test'}})
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {{'param': 'value'}}
    
    @property
    def name(self) -> str:
        return '{pipe_class_name.lower()}'
    
    @property
    def description(self) -> str:
        return 'Test pipe'
    
    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return []
    
    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return []
    
    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return []
'''
        with open(path, 'w') as f:
            f.write(pipe_content)
    
    def test_discover_pipes_with_main_pipe(self):
        """Test discovering pipes with main.py files"""
        # Create pipe directory structure
        pipe_dir = os.path.join(self.core_pipes_path, 'test_pipe')
        main_file = os.path.join(pipe_dir, 'main.py')
        os.makedirs(pipe_dir, exist_ok=True)
        
        # Create a simple main.py file
        with open(main_file, 'w') as f:
            f.write('# dummy file')
        
        # Mock the _load_pipe_module method to return our MockPipe
        with patch.object(self.registry, '_load_pipe_module', return_value=MockPipe):
            with patch('src.pipelines.catalog.requirements_satisfied', return_value=True):
                self.registry.discover_pipes()
        
        # The pipe registry uses the pipe.name class attribute as the key
        pipe_name = MockPipe.name  # This should be 'mock_pipe'
        self.assertIn(pipe_name, self.registry.pipes)
        self.assertEqual(self.registry.pipes[pipe_name], MockPipe)

    def test_discover_pipes_class_attr_name_registers_under_string_key(self):
        """A class-attr `name`/`description` pipe, discovered through the real
        (unmocked) import path, registers under its plain string name -- the
        contract `ClassVar[str]` documents, unlike the `@property` style the
        old `@abstractmethod` declaration invited."""
        pipe_dir = os.path.join(self.core_pipes_path, 'string_key_pipe')
        main_file = os.path.join(pipe_dir, 'main.py')
        os.makedirs(pipe_dir, exist_ok=True)
        with open(main_file, 'w') as f:
            f.write('''
from typing import Any, Dict, List
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import PipeInputSpec, PipeOutputSpec, PipeConfigSpec, PipeInput, PipeOutput


class StringKeyPipe(BasePipe):
    name = "string_key_pipe"
    description = "Registers under a plain string key"

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        return PipeOutput(output={})

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {}

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return []

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return []

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return []
''')

        with patch('src.pipelines.catalog.requirements_satisfied', return_value=True):
            self.registry.discover_pipes()

        self.assertIn('string_key_pipe', self.registry.pipes)
        registered_key = next(k for k in self.registry.pipes if k == 'string_key_pipe')
        self.assertIsInstance(registered_key, str)
    
    def test_discover_pipes_with_variants(self):
        """Test discovering pipe variants"""
        # Create pipe directory structure with variant
        pipe_dir = os.path.join(self.core_pipes_path, 'test_pipe')
        variant_dir = os.path.join(pipe_dir, 'variant1')
        variant_main = os.path.join(variant_dir, 'main.py')
        os.makedirs(variant_dir, exist_ok=True)
        
        # Create a simple main.py file
        with open(variant_main, 'w') as f:
            f.write('# dummy variant file')
        
        # Mock the _load_pipe_module method to return our MockPipe
        with patch.object(self.registry, '_load_pipe_module', return_value=MockPipe):
            self.registry.discover_pipes()
        
        # Verify variant was discovered
        self.assertIn('test_pipe/variant1', self.registry.pipes)
    
    def test_discover_pipes_nonexistent_paths(self):
        """Test discovering pipes when paths don't exist"""
        registry = PipeCatalog('/nonexistent/core', '/nonexistent/custom')
        registry.discover_pipes()  # Should not raise exception
        self.assertEqual(len(registry.pipes), 0)
    
    @patch('src.pipelines.catalog.logger')
    def test_load_pipe_module_error_handling(self, mock_logger):
        """Test error handling in _load_pipe_module"""
        # Create a file that will cause import error
        pipe_file = os.path.join(self.core_pipes_path, 'bad_pipe', 'main.py')
        os.makedirs(os.path.dirname(pipe_file), exist_ok=True)
        
        with open(pipe_file, 'w') as f:
            f.write('invalid python syntax !!!')
        
        result = self.registry._load_pipe_module(pipe_file, 'bad_module')
        self.assertIsNone(result)
        mock_logger.error.assert_called_once()


class TestMockPipe(unittest.TestCase):
    """Test cases for the MockPipe implementation"""
    
    def test_mock_pipe_creation(self):
        """Test creating a MockPipe instance"""
        config = {'param1': 'test', 'param2': 100}
        pipe = MockPipe(config)
        self.assertEqual(pipe.config, config)
    
    def test_mock_pipe_process(self):
        """Test MockPipe process method"""
        pipe = MockPipe({})
        input_data = PipeInput(input={'test': 'data'})
        output = pipe.process(input_data, Mock())
        
        self.assertIsInstance(output, PipeOutput)
        self.assertEqual(output.output['result'], 'test_output')
    
    def test_mock_pipe_properties(self):
        """Test MockPipe properties"""
        self.assertEqual(MockPipe.get_default_config(), {'param1': 'default_value', 'param2': 42})
        
        pipe = MockPipe({})
        self.assertEqual(MockPipe.name, 'mock_pipe')  # Class attribute
        self.assertEqual(pipe.name, 'mock_pipe')      # Accessible as instance attribute too
        self.assertEqual(pipe.description, 'A mock pipe for testing')
    
    def test_mock_pipe_specifications(self):
        """Test MockPipe input/output/config specifications"""
        inputs = MockPipe.inputs()
        self.assertEqual(len(inputs), 2)
        self.assertEqual(inputs[0].name, 'input1')
        self.assertEqual(inputs[0].io_type, IOType.TEXT)
        self.assertTrue(inputs[0].required)
        
        outputs = MockPipe.outputs()
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].name, 'result')
        self.assertEqual(outputs[0].io_type, IOType.TEXT)
        
        config = MockPipe.configuration()
        self.assertEqual(len(config), 2)
        self.assertEqual(config[0].name, 'param1')
        self.assertEqual(config[0].param_type, str)
    
    def test_mock_pipe_requirements(self):
        """Test MockPipe requirements"""
        requirements = MockPipe.get_requirements()
        expected = {
            'pip': ['test-package==1.0.0'],
            'git': [{'url': 'https://github.com/test/repo.git', 'path': '/tmp/test-repo'}],
            'models': [{'path': '/models/test-model.bin'}]
        }
        self.assertEqual(requirements, expected)


if __name__ == '__main__':
    unittest.main()