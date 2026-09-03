"""A plugin's `requirement_checkers:` manifest entries are wired into a
`RequirementCheckerRegistry` on enable, and removed on disable - mirrors
`tests/features/plugins/test_prompt_importer_registration.py`.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.platform.plugins.registry import PluginRegistry
from src.platform.plugins.requirement_checkers import RequirementCheckerRegistry


class TestPluginRequirementCheckerRegistration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.checker_registry = RequirementCheckerRegistry()
        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            requirement_checker_registry=self.checker_registry,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, requirement_checkers: list) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()

        manifest_data = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': 'Test plugin',
            'author': 'Test Author',
            'type': 'full-stack',
            'requirement_checkers': requirement_checkers,
        }
        with open(plugin_dir / "manifest.yml", 'w') as f:
            yaml.dump(manifest_data, f)

        (plugin_dir / "checkers.py").write_text(
            "from src.plugin_api.presets import RequirementChecker, RequirementResult\n\n"
            "class FixtureChecker:\n"
            "    type = 'fixture'\n"
            "    schema = None\n\n"
            "    async def check(self, spec, ctx):\n"
            "        return RequirementResult(status='ok', detail='fixture ok')\n"
        )

        return plugin_dir

    def test_enable_registers_requirement_checker(self):
        self._create_plugin('fixture-checker-plugin', [
            {'type': 'fixture', 'backend': 'checkers:FixtureChecker'},
        ])

        success = self.registry.enable_plugin('fixture-checker-plugin')
        self.assertTrue(success)

        registration = self.checker_registry.get('fixture')
        self.assertIsNotNone(registration)
        self.assertEqual(registration.source, 'fixture-checker-plugin')
        self.assertEqual(registration.checker.__class__.__name__, 'FixtureChecker')

    def test_disable_unregisters_requirement_checker(self):
        self._create_plugin('fixture-checker-plugin-2', [
            {'type': 'fixture-2', 'backend': 'checkers:FixtureChecker'},
        ])

        self.assertTrue(self.registry.enable_plugin('fixture-checker-plugin-2'))
        self.assertIsNotNone(self.checker_registry.get('fixture-2'))

        self.assertTrue(self.registry.disable_plugin('fixture-checker-plugin-2'))
        self.assertIsNone(self.checker_registry.get('fixture-2'))

    def test_enable_fails_on_type_collision(self):
        self._create_plugin('first-plugin', [
            {'type': 'shared-type', 'backend': 'checkers:FixtureChecker'},
        ])
        self._create_plugin('second-plugin', [
            {'type': 'shared-type', 'backend': 'checkers:FixtureChecker'},
        ])

        self.assertTrue(self.registry.enable_plugin('first-plugin'))
        success = self.registry.enable_plugin('second-plugin')

        self.assertFalse(success)
        registration = self.checker_registry.get('shared-type')
        self.assertEqual(registration.source, 'first-plugin')


if __name__ == "__main__":
    unittest.main()
