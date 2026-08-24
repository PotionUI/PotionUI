"""Tests for plugin-provided LLM chat extensions (chat_modes / tools / resources)."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.features.chat.modes.registry import ChatModeRegistry
from src.features.llm.tools.registry import ToolRegistry
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.resources.registry import ResourceRegistry

TOOL_MODULE = """
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult

class PluginTool(BaseTool):
    @property
    def name(self):
        return "plugin_tool"

    @property
    def description(self):
        return "A plugin tool"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        return ToolResult(success=True, data="ok")
"""

PROVIDER_MODULE = """
from src.platform.resources.base import BaseResourceProvider, ResolvedResource, ResourceSuggestion

class DatasetProvider(BaseResourceProvider):
    @property
    def namespace(self):
        return "datasets"

    async def resolve(self, path, ctx):
        return ResolvedResource(
            uri="datasets." + ".".join(path), namespace="datasets",
            kind="dataset", title="ds", content="dataset content",
        )

    async def suggest(self, path, partial, ctx, limit=15):
        return [ResourceSuggestion(uri="datasets.demo", label="demo")]
"""

CONTRIBUTOR_MODULE = """
def contribute(context_metadata, session, user_id):
    return "plugin context block"
"""


class TestPluginChatExtensionRegistration(unittest.TestCase):
    """A plugin's `chat_modes:`/`tools:`/`resources:` manifest sections are
    wired into their registries on enable and removed on disable."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.tool_registry = ToolRegistry()
        self.chat_mode_registry = ChatModeRegistry()
        self.resource_registry = ResourceRegistry()
        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            tool_registry=self.tool_registry,
            chat_mode_registry=self.chat_mode_registry,
            resource_registry=self.resource_registry,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, prompt_file: bool = False, **sections) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()

        manifest_data = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': 'Test plugin',
            'author': 'Test Author',
            'type': 'full-stack',
            **sections,
        }
        with open(plugin_dir / "manifest.yml", 'w') as f:
            yaml.dump(manifest_data, f)

        (plugin_dir / "chat_tools.py").write_text(TOOL_MODULE)
        (plugin_dir / "chat_resources.py").write_text(PROVIDER_MODULE)
        (plugin_dir / "chat_hooks.py").write_text(CONTRIBUTOR_MODULE)
        if prompt_file:
            (plugin_dir / "prompts").mkdir()
            (plugin_dir / "prompts" / "mode.md").write_text("Prompt from file. {{TOOL_HINTS}}")

        return plugin_dir

    def _full_sections(self, mode_id='dataset', prompt_source=None):
        prompt_source = prompt_source or {'system_prompt': 'You build datasets. {{TOOL_HINTS}}'}
        return dict(
            chat_modes=[{
                'id': mode_id,
                'name': 'Dataset Mode',
                'description': 'Builds datasets',
                'icon': 'database',
                'tools': ['list_models'],
                'default_route_prefixes': [f'/plugins/{mode_id}'],
                'context_contributor': 'chat_hooks.contribute',
                'llm_options': {'think': False},
                **prompt_source,
            }],
            tools=[{'class': 'chat_tools:PluginTool', 'modes': [mode_id]}],
            resources=[{'namespace': 'datasets', 'provider': 'chat_resources:DatasetProvider', 'modes': [mode_id]}],
        )

    def test_enable_registers_all_chat_extensions(self):
        self._create_plugin('chat-plugin', **self._full_sections())

        self.assertTrue(self.registry.enable_plugin('chat-plugin'))

        tool = self.tool_registry.get('plugin_tool')
        self.assertIsNotNone(tool)
        self.assertEqual(tool.modes, ['dataset'])

        mode = self.chat_mode_registry.get('dataset')
        self.assertIsNotNone(mode)
        self.assertEqual(mode.source, 'chat-plugin')
        self.assertEqual(mode.tool_names, ['list_models'])
        self.assertEqual(mode.llm_options, {'think': False})
        self.assertIn('{{TOOL_HINTS}}', mode.resolve_prompt_template())
        self.assertIsNotNone(mode.context_contributor)
        self.assertEqual(mode.context_contributor({}, None, 'u1'), 'plugin context block')

        provider = self.resource_registry.get('datasets')
        self.assertIsNotNone(provider)
        self.assertEqual(provider.modes, ['dataset'])

    def test_disable_unregisters_all_chat_extensions(self):
        self._create_plugin('chat-plugin-2', **self._full_sections(mode_id='dataset2'))

        self.assertTrue(self.registry.enable_plugin('chat-plugin-2'))
        self.assertTrue(self.registry.disable_plugin('chat-plugin-2'))

        self.assertIsNone(self.tool_registry.get('plugin_tool'))
        self.assertIsNone(self.chat_mode_registry.get('dataset2'))
        self.assertIsNone(self.resource_registry.get('datasets'))

    def test_system_prompt_file_is_loaded(self):
        self._create_plugin(
            'prompt-file-plugin',
            prompt_file=True,
            **self._full_sections(
                mode_id='filemode',
                prompt_source={'system_prompt_file': 'prompts/mode.md'},
            ),
        )

        self.assertTrue(self.registry.enable_plugin('prompt-file-plugin'))
        mode = self.chat_mode_registry.get('filemode')
        self.assertIn('Prompt from file.', mode.resolve_prompt_template())

    def test_bad_tool_class_fails_enable_and_rolls_back(self):
        sections = self._full_sections(mode_id='rollback')
        sections['tools'] = [{'class': 'chat_tools:MissingTool'}]
        # tools register before chat_modes/resources, so nothing should stick
        self._create_plugin('broken-plugin', **sections)

        self.assertFalse(self.registry.enable_plugin('broken-plugin'))
        self.assertEqual(self.registry.get_plugin_state('broken-plugin'), PluginState.ERROR)
        self.assertIn('MissingTool', self.registry.get_plugin_error('broken-plugin'))
        self.assertIsNone(self.chat_mode_registry.get('rollback'))
        self.assertIsNone(self.resource_registry.get('datasets'))

    def test_bad_resource_provider_rolls_back_earlier_registrations(self):
        sections = self._full_sections(mode_id='rollback2')
        sections['resources'] = [{'namespace': 'datasets', 'provider': 'chat_resources:Nope'}]
        self._create_plugin('broken-plugin-2', **sections)

        self.assertFalse(self.registry.enable_plugin('broken-plugin-2'))
        # tool and mode registered before the failing provider must be gone
        self.assertIsNone(self.tool_registry.get('plugin_tool'))
        self.assertIsNone(self.chat_mode_registry.get('rollback2'))

    def test_tool_name_collision_fails_enable(self):
        class ExistingTool:
            name = 'plugin_tool'

        self.tool_registry.register(ExistingTool(), source='builtin')
        self._create_plugin('colliding-plugin', tools=[{'class': 'chat_tools:PluginTool'}])

        self.assertFalse(self.registry.enable_plugin('colliding-plugin'))
        self.assertIn('collision', self.registry.get_plugin_error('colliding-plugin'))
        # the builtin registration must be untouched
        self.assertIsNotNone(self.tool_registry.get('plugin_tool'))

    def test_namespace_mismatch_fails_enable(self):
        self._create_plugin('mismatch-plugin', resources=[
            {'namespace': 'wrong_ns', 'provider': 'chat_resources:DatasetProvider'}
        ])

        self.assertFalse(self.registry.enable_plugin('mismatch-plugin'))
        self.assertIn('wrong_ns', self.registry.get_plugin_error('mismatch-plugin'))
        self.assertIsNone(self.resource_registry.get('datasets'))

    def test_prompt_file_escape_is_rejected(self):
        self._create_plugin(
            'escape-plugin',
            **self._full_sections(
                mode_id='escape',
                prompt_source={'system_prompt_file': '../../outside.md'},
            ),
        )

        self.assertFalse(self.registry.enable_plugin('escape-plugin'))
        self.assertIsNone(self.chat_mode_registry.get('escape'))

    def test_sections_without_registries_fail_gracefully(self):
        bare_registry = PluginRegistry(str(self.marketplace_dir), str(self.local_dir))
        self._create_plugin('needs-registries', tools=[{'class': 'chat_tools:PluginTool'}])
        bare_registry.discover_plugins()

        self.assertFalse(bare_registry.enable_plugin('needs-registries'))
        self.assertIn('no tool registry', bare_registry.get_plugin_error('needs-registries'))


if __name__ == '__main__':
    unittest.main()
