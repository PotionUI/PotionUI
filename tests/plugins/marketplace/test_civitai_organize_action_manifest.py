import shutil
from pathlib import Path

import yaml

from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.organize import OrganizeRegistry
from src.platform.plugins.registry import PluginRegistry

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "civitai-provider"


def test_the_manifest_declares_an_instance_action_that_waits_for_the_digest():
    manifest = yaml.safe_load((PLUGIN_ROOT / "manifest.yml").read_text())

    schema = PluginManifestSchema.model_validate(manifest)

    action = schema.organize_actions[0]
    assert action.key == "civitai-provider.fetch_info"
    assert (action.scope, action.subjects, action.needs, action.paced) == ("instance", ["model"], ["digest"], True)
    assert action.undo_handler is None
    assert any("SHA-256" in note for note in action.notes)
    assert action.done_label == "Fetched CivitAI data"


def test_enabling_the_plugin_registers_the_action_for_the_instance_scope(tmp_path):
    marketplace = tmp_path / "marketplace"
    (tmp_path / "local").mkdir()
    marketplace.mkdir()
    shutil.copytree(PLUGIN_ROOT, marketplace / "civitai-provider", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    organize = OrganizeRegistry()
    registry = PluginRegistry(str(marketplace), str(tmp_path / "local"), organize_registry=organize)
    registry.discover_plugins()

    assert registry.enable_plugin("civitai-provider") is True

    definition = organize.action("civitai-provider.fetch_info")
    assert definition.scopes == ("instance",)
    assert definition.needs == ("digest",)
    assert definition.paced is True
    assert definition.undo is None
    assert definition.source == "civitai-provider"
    registry.disable_plugin("civitai-provider")
    assert organize.action("civitai-provider.fetch_info") is None
