import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    PipeStatus,
)
from src.pipelines.installer import PipeInstaller, requirements_satisfied


class MockPipe(BasePipe):
    """A pipe with no requirements at all."""

    name = "mock_pipe"
    description = "A mock pipe for testing"

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        return PipeOutput(output={"result": "processed"})

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {"param1": "default1"}

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec(name="input1", io_type=IOType.IMAGE)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec(name="result", io_type=IOType.IMAGE)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return []


class TestRequirementsSatisfied(unittest.TestCase):
    """Test cases for the requirements check the catalog uses to set pipe status"""

    @patch('src.pipelines.installer.importlib.import_module')
    def test_pip_requirements(self, mock_import):
        """A pip requirement is satisfied only if the package imports"""
        class SimplePipe(MockPipe):
            @classmethod
            def get_requirements(cls):
                return {
                    'pip': ['test-package'],
                    'git': [],
                    'models': []
                }

        # Test when requirements are met
        mock_import.return_value = Mock()
        self.assertTrue(requirements_satisfied(SimplePipe))

        # Test when requirements are not met
        mock_import.side_effect = ImportError()
        self.assertFalse(requirements_satisfied(SimplePipe))

    def test_git_requirements(self):
        """A git requirement is satisfied only if its checkout exists"""
        class PipeWithGitReq(MockPipe):
            @classmethod
            def get_requirements(cls):
                return {
                    'pip': [],
                    'git': [{'path': '/nonexistent/path'}],
                    'models': []
                }

        self.assertFalse(requirements_satisfied(PipeWithGitReq))

    def test_model_requirements(self):
        """A model requirement is satisfied only if the weights exist"""
        class PipeWithModelReq(MockPipe):
            @classmethod
            def get_requirements(cls):
                return {
                    'pip': [],
                    'git': [],
                    'models': [{'path': '/nonexistent/model.bin'}]
                }

        self.assertFalse(requirements_satisfied(PipeWithModelReq))


class TestPipeInstaller:
    """Test cases for the PipeInstaller class"""

    def setup_method(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.core_pipes_path = os.path.join(self.temp_dir, 'core_pipes')
        self.custom_pipes_path = os.path.join(self.temp_dir, 'custom_pipes')

        os.makedirs(self.core_pipes_path, exist_ok=True)
        os.makedirs(self.custom_pipes_path, exist_ok=True)

        self.catalog = PipeCatalog(self.core_pipes_path, self.custom_pipes_path)
        self.installer = PipeInstaller(self.catalog)

    def teardown_method(self):
        """Clean up test environment"""
        shutil.rmtree(self.temp_dir)

    @pytest.mark.asyncio
    async def test_install_pipe_success(self):
        """Test successful pipe installation"""
        self.catalog.pipes['mock_pipe'] = MockPipe

        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            mock_process = Mock()
            mock_process.communicate = AsyncMock(return_value=(b'', b''))
            mock_subprocess.return_value = mock_process

            result = await self.installer.install_pipe('mock_pipe')

        assert result
        assert self.catalog.pipe_status['mock_pipe'] == PipeStatus.INSTALLED

    @pytest.mark.asyncio
    async def test_install_pipe_not_found(self):
        """Test installing non-existent pipe"""
        result = await self.installer.install_pipe('nonexistent')
        assert not result

    @pytest.mark.asyncio
    async def test_install_pipe_error(self):
        """Test pipe installation error handling"""
        class PipeWithPipReq(MockPipe):
            @classmethod
            def get_requirements(cls):
                return {'pip': ['test-package'], 'git': [], 'models': []}

        self.catalog.pipes['mock_pipe'] = PipeWithPipReq

        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            mock_subprocess.side_effect = Exception("Installation failed")

            result = await self.installer.install_pipe('mock_pipe')

        assert not result
        assert self.catalog.pipe_status['mock_pipe'] == PipeStatus.ERROR

    @pytest.mark.asyncio
    async def test_uninstall_pipe_success(self):
        """Test successful pipe uninstallation"""
        self.catalog.pipes['mock_pipe'] = MockPipe

        with patch('asyncio.to_thread') as mock_to_thread:
            mock_to_thread.return_value = AsyncMock()()

            result = await self.installer.uninstall_pipe('mock_pipe')

        assert result
        assert self.catalog.pipe_status['mock_pipe'] == PipeStatus.NOT_INSTALLED

    @pytest.mark.asyncio
    async def test_uninstall_pipe_not_found(self):
        """Test uninstalling non-existent pipe"""
        result = await self.installer.uninstall_pipe('nonexistent')
        assert not result

    @pytest.mark.asyncio
    async def test_uninstall_pipe_error(self):
        """Test pipe uninstallation error handling"""
        class PipeWithGitReq(MockPipe):
            @classmethod
            def get_requirements(cls):
                return {'pip': [], 'git': [{'path': '/tmp'}], 'models': []}

        self.catalog.pipes['mock_pipe'] = PipeWithGitReq

        with patch('asyncio.to_thread') as mock_to_thread:
            mock_to_thread.side_effect = Exception("Uninstallation failed")

            result = await self.installer.uninstall_pipe('mock_pipe')

        assert not result

    def test_remove_directory(self):
        """Test directory removal utility"""
        test_dir = os.path.join(self.temp_dir, 'test_remove')
        os.makedirs(test_dir)

        test_file = os.path.join(test_dir, 'test.txt')
        with open(test_file, 'w') as f:
            f.write('test')

        assert os.path.exists(test_dir)
        self.installer._remove_directory(test_dir)
        assert not os.path.exists(test_dir)


@pytest.mark.asyncio
async def test_install_pipe_pip_nonzero_exit_is_failure():
    """A pip subprocess that exits non-zero (e.g. package not found) must not be
    reported as a successful install just because it didn't raise.

    Written as a plain pytest coroutine rather than a TestPipeInstaller method:
    pytest does not await async test methods on a plain unittest.TestCase, so a
    method there would pass unconditionally regardless of the assertions inside.
    """
    temp_dir = tempfile.mkdtemp()
    try:
        core_pipes_path = os.path.join(temp_dir, 'core_pipes')
        custom_pipes_path = os.path.join(temp_dir, 'custom_pipes')
        os.makedirs(core_pipes_path, exist_ok=True)
        os.makedirs(custom_pipes_path, exist_ok=True)

        catalog = PipeCatalog(core_pipes_path, custom_pipes_path)
        installer = PipeInstaller(catalog)

        class PipeWithPipReq(MockPipe):
            @classmethod
            def get_requirements(cls):
                return {'pip': ['nonexistent-package-xyz'], 'git': [], 'models': []}

        catalog.pipes['mock_pipe'] = PipeWithPipReq

        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            mock_process = Mock()
            mock_process.communicate = AsyncMock(return_value=(b'', b'ERROR: No matching distribution found'))
            mock_process.returncode = 1
            mock_subprocess.return_value = mock_process

            result = await installer.install_pipe('mock_pipe')

        assert result is False
        assert catalog.pipe_status['mock_pipe'] == PipeStatus.ERROR
    finally:
        shutil.rmtree(temp_dir)


if __name__ == '__main__':
    unittest.main()
