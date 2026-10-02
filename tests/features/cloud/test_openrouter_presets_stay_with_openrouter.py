from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import re
import yaml
from pydantic import Field

from src.features.backends.backend_registry import BackendRegistry
from src.features.cloud.catalog import CloudCatalog
from src.features.cloud.repository import CloudCatalogRepository
from src.features.cloud.testing.fake import FakeCloudConfig, FakeCloudProvider
from src.features.models.availability import models_for_engine
from src.features.models.backend_indexer import BackendModelIndexer
from src.features.models.repository import model_repo
from src.features.presets import PresetTemplateLoader
from src.features.presets.linter import PresetLinter
from src.features.presets.routes import PresetController
from src.plugin_api.cloud import CLOUD_BLOCKS
from tests.features.cloud.conftest import ScriptedPluginRegistry

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "content" / "plugins" / "marketplace" / "openrouter-provider"
PRESETS = PLUGIN / "presets"


class OpenRouterLikeConfig(FakeCloudConfig):
    driver: str = Field(default="cloud.openrouter")


class OpenRouterLikeProvider(FakeCloudProvider):
    key = "openrouter"
    label = "OpenRouter lookalike"
    config_class = OpenRouterLikeConfig


@pytest.fixture
def templates():
    loader = PresetTemplateLoader([str(PRESETS)])
    loader.load_presets()
    return loader


def preset_named(loader, name):
    return next(template for template in loader.presets if template.name == name)


def declared_cover(variant_dir):
    preset = yaml.safe_load((variant_dir / "preset.yml").read_text(encoding="utf-8"))
    return variant_dir / preset["media"]["cover"]


def test_the_plugin_ships_an_image_preset_and_a_video_preset(templates):
    assert sorted(template.name for template in templates.presets) == ["OpenRouter Images", "OpenRouter Video"]
    video = preset_named(templates, "OpenRouter Video")
    assert video.category == "video" and video.driver == "cloud.openrouter"
    assert set(video.modes) == {"txt2video", "img2video"}
    assert declared_cover(PRESETS / "VideoGeneration" / "standard").is_file()


def test_the_plugin_ships_one_image_preset_with_a_text_to_image_and_an_edit_mode(templates):
    template = preset_named(templates, "OpenRouter Images")

    assert template.engine == "cloud" and template.driver == "cloud.openrouter"
    assert template.name == "OpenRouter Images"
    assert set(template.modes) == {"edit", "txt2img"}
    assert declared_cover(PRESETS / "ImageGeneration" / "standard").is_file()


def test_the_plugin_presets_lint_clean():
    issues = [issue for issue in PresetLinter([str(PRESETS)]).lint() if "tests.yml" not in issue.message]

    assert issues == []


def test_the_plugin_presets_reference_only_real_shared_blocks():
    referenced = set()
    for form in PRESETS.rglob("form.yml"):
        referenced.update(
            re.findall(r"paths\._shared \}\}/cloud/([\w/]+\.yml)", form.read_text(encoding="utf-8"))
        )

    assert referenced and referenced <= set(CLOUD_BLOCKS)


def test_the_manifest_registers_the_provider_and_the_preset_root():
    manifest = yaml.safe_load((PLUGIN / "manifest.yml").read_text(encoding="utf-8"))

    assert manifest["presets"] == [{"path": "presets"}]
    assert manifest["hooks"]["backend"][0]["hook"] == "backend.register"
    assert manifest["type"] == "backend-only"


@pytest.fixture
async def both(mock_db):
    registry = BackendRegistry(
        generation_engine_factory=lambda: None,
        plugin_registry=ScriptedPluginRegistry(FakeCloudProvider, OpenRouterLikeProvider),
    )
    catalog = CloudCatalog(
        backend_registry=registry,
        repository=CloudCatalogRepository(),
        model_repository=model_repo,
        backend_indexer=BackendModelIndexer(),
    )
    await registry.add_backend(FakeCloudConfig(id="cloud-other", name="Another provider"))
    await registry.add_backend(OpenRouterLikeConfig(id="cloud-or", name="OpenRouter"))
    for backend_id in ("cloud-other", "cloud-or"):
        await catalog.refresh(backend_id)
        await catalog.set_enabled(backend_id, list(catalog.repository.provider_ids(backend_id)), True)
    return registry


async def test_the_plugin_presets_never_list_another_providers_models(templates, both):
    template = preset_named(templates, "OpenRouter Images")
    loader = Mock()
    loader.load_preset_by_id.return_value = template
    controller = PresetController(SimpleNamespace(preset_loader=loader), both)

    response = await controller.get_preset_models(template.id, model_type="cloud", admin=True)

    models = response.data["models"]
    assert models and all(entry["filename"].startswith("openrouter~") for entry in models)
    assert all(entry["backend_ids"] == ["cloud-or"] for entry in models)
    assert not any(entry["filename"].startswith("fake~") for entry in models)


async def test_the_plugin_presets_listing_ignores_a_backend_of_another_driver_even_with_a_task_filter(templates, both):
    template = preset_named(templates, "OpenRouter Images")

    listed = models_for_engine("cloud", both, model_type="cloud", driver=template.driver, tasks=["txt2img"])

    assert listed and all(entry["filename"].startswith("openrouter~") for entry in listed)


def test_the_manifest_passes_the_plugin_schema():
    from src.platform.plugins.manifest import PluginManifestSchema

    data = yaml.safe_load((PLUGIN / "manifest.yml").read_text(encoding="utf-8"))

    parsed = PluginManifestSchema(**data)

    assert parsed.id == "openrouter-provider"


async def test_the_video_preset_lists_only_openrouter_video_models(templates, both):
    template = preset_named(templates, "OpenRouter Video")
    loader = Mock()
    loader.load_preset_by_id.return_value = template
    controller = PresetController(SimpleNamespace(preset_loader=loader), both)

    for tasks in ("txt2video", "img2video"):
        response = await controller.get_preset_models(template.id, model_type="cloud", tasks=tasks, admin=True)
        models = response.data["models"]
        assert [entry["filename"] for entry in models] == ["openrouter~fake~video-1"]
        assert all(entry["backend_ids"] == ["cloud-or"] for entry in models)


async def test_the_video_preset_never_offers_image_only_models(templates, both):
    template = preset_named(templates, "OpenRouter Video")

    listed = models_for_engine("cloud", both, model_type="cloud", driver=template.driver, tasks=["txt2video", "img2video"])

    assert listed and not any("image" in entry["filename"] for entry in listed)
