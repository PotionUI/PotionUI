from types import SimpleNamespace

import pytest

from src.platform.plugins.llm_providers import LLMProviderRegistry
from src.platform.plugins.phrasebook_ops import PhrasebookOperationRegistry
from src.platform.plugins.prompt_importers import PromptImporterRegistry
from src.platform.plugins.recipe_steps import RecipeStepKindRegistry
from src.platform.plugins.registry import _BACKEND_SECTIONS, PluginRegistry
from src.platform.plugins.requirement_checkers import RequirementCheckerRegistry

SECTIONS = {
    "prompt_importers": ("id", "prompt importer", "prompt importer backend"),
    "phrasebook_ops": ("id", "phrasebook batch operation", "phrasebook batch operation backend"),
    "requirement_checkers": ("type", "requirement checker", "requirement checker backend"),
    "recipe_steps": ("kind", "recipe step kind", "recipe step executor backend"),
    "llm_providers": ("type", "LLM provider", "LLM provider backend"),
}


class Backend:
    pass


class Exploding:
    def __init__(self):
        raise RuntimeError("boom")


def make_registry(tmp_path, **overrides):
    wired = {
        "prompt_importer_registry": PromptImporterRegistry(),
        "phrasebook_operation_registry": PhrasebookOperationRegistry(),
        "requirement_checker_registry": RequirementCheckerRegistry(),
        "recipe_step_kind_registry": RecipeStepKindRegistry(),
        "llm_provider_registry": LLMProviderRegistry(),
    }
    wired.update(overrides)
    return PluginRegistry(marketplace_dir=str(tmp_path), local_dir=str(tmp_path), **wired)


def manifest_with(section, entries, plugin_id="p"):
    empty = {name: [] for name in SECTIONS}
    empty[section] = entries
    return SimpleNamespace(id=plugin_id, **empty)


def run_section(registry, manifest, section):
    spec = next(s for s in registry_sections() if s.section == section)
    return registry._register_backend_section(spec, manifest)


def registry_sections():
    return _BACKEND_SECTIONS


@pytest.fixture(params=list(SECTIONS))
def section(request):
    return request.param


def test_section_table_covers_exactly_these_sections():
    assert {s.section for s in registry_sections()} == set(SECTIONS)


def test_entry_without_its_key_names_the_missing_field(tmp_path, section):
    key_field, _, _ = SECTIONS[section]
    registry = make_registry(tmp_path)

    error = run_section(registry, manifest_with(section, [{}]), section)

    assert error == f"{section} entry missing '{key_field}'"


def test_entry_without_a_backend_names_the_key(tmp_path, section):
    key_field, _, _ = SECTIONS[section]
    registry = make_registry(tmp_path)

    error = run_section(registry, manifest_with(section, [{key_field: "x"}]), section)

    assert error == f"{section} entry 'x' missing 'backend'"


def test_unwired_registry_is_reported_only_when_the_section_is_declared(tmp_path, section):
    _, registry_label, _ = SECTIONS[section]
    attr = next(s.registry_attr for s in registry_sections() if s.section == section)
    registry = make_registry(tmp_path, **{attr: None})

    assert run_section(registry, manifest_with(section, []), section) is None
    assert (
        run_section(registry, manifest_with(section, [{"x": 1}]), section)
        == f"Plugin declares {section} but no {registry_label} registry is available"
    )


def test_backend_that_will_not_load(tmp_path, section):
    key_field, _, backend_label = SECTIONS[section]
    registry = make_registry(tmp_path)
    registry.loader.load_class = lambda manifest, ref: None

    error = run_section(registry, manifest_with(section, [{key_field: "x", "backend": "mod.Cls"}]), section)

    assert error == f"Failed to load {backend_label}: mod.Cls"


def test_backend_that_will_not_construct(tmp_path, section):
    key_field, _, backend_label = SECTIONS[section]
    registry = make_registry(tmp_path)
    registry.loader.load_class = lambda manifest, ref: Exploding

    error = run_section(registry, manifest_with(section, [{key_field: "x", "backend": "mod.Cls"}]), section)

    assert error == f"Failed to instantiate {backend_label} 'mod.Cls': boom"


def test_registered_entry_belongs_to_the_plugin_and_a_second_claim_fails(tmp_path, section):
    key_field, _, _ = SECTIONS[section]
    spec = next(s for s in registry_sections() if s.section == section)
    registry = make_registry(tmp_path)
    registry.loader.load_class = lambda manifest, ref: Backend
    entries = [{key_field: "x", "backend": "mod.Cls"}]

    assert run_section(registry, manifest_with(section, entries, "plugin-a"), section) is None
    stored = getattr(registry, spec.registry_attr).get("x")
    assert stored.source == "plugin-a"

    error = run_section(registry, manifest_with(section, entries, "plugin-b"), section)

    assert error is not None and "already registered" in error and "'x'" in error


def test_enable_stages_run_in_dependency_order(tmp_path, monkeypatch):
    registry = make_registry(tmp_path)
    calls = []
    for name in dir(PluginRegistry):
        if name.startswith("_register_plugin_"):
            monkeypatch.setattr(registry, name, lambda manifest, name=name: calls.append(name))
    monkeypatch.setattr(registry, "_register_backend_section", lambda spec, manifest: calls.append(spec.section))
    monkeypatch.setattr(registry.loader, "validate_dependencies", lambda manifest: (True, []))

    assert registry._run_enable_stages(SimpleNamespace(id="p")) is None

    assert calls == [
        "_register_plugin_hooks",
        "_register_plugin_field_types",
        "_register_plugin_model_metadata_fields",
        "_register_plugin_tools",
        "_register_plugin_media_tools",
        "_register_plugin_effects",
        "_register_plugin_chat_modes",
        "_register_plugin_resources",
        "_register_plugin_automation_nodes",
        "_register_plugin_automation_templates",
        "prompt_importers",
        "phrasebook_ops",
        "requirement_checkers",
        "recipe_steps",
        "llm_providers",
        "_register_plugin_prompt_resource_sources",
        "_register_plugin_samplers",
        "_register_plugin_schedules",
        "_register_plugin_model_classifiers",
        "_register_plugin_model_types",
        "_register_plugin_organize_facts",
        "_register_plugin_organize_actions",
        "_register_plugin_limit_kinds",
    ]


def test_first_failing_stage_stops_the_rest(tmp_path, monkeypatch):
    registry = make_registry(tmp_path)
    calls = []
    for name in dir(PluginRegistry):
        if name.startswith("_register_plugin_"):
            monkeypatch.setattr(registry, name, lambda manifest, name=name: calls.append(name))
    monkeypatch.setattr(registry, "_register_plugin_field_types", lambda manifest: "field boom")
    monkeypatch.setattr(registry.loader, "validate_dependencies", lambda manifest: (True, []))

    assert registry._run_enable_stages(SimpleNamespace(id="p")) == "field boom"
    assert calls == ["_register_plugin_hooks"]


@pytest.mark.parametrize("method, section, key, label, registry_kw", [
    ("_register_plugin_tools", "tools", "class", "tool class", "tool_registry"),
    ("_register_plugin_resources", "resources", "provider", "resource provider class", "resource_registry"),
])
class TestClassBackedSections:
    def _registry(self, tmp_path, registry_kw):
        return make_registry(tmp_path, **{registry_kw: object()})

    def _manifest(self, section, key):
        manifest = manifest_with("recipe_steps", [])
        manifest.tools = []
        manifest.resources = []
        setattr(manifest, section, [{key: "mod.Cls"}])
        return manifest

    def test_class_that_fails_to_load(self, tmp_path, method, section, key, label, registry_kw):
        registry = self._registry(tmp_path, registry_kw)
        registry.loader.load_class = lambda manifest, ref: None

        assert getattr(registry, method)(self._manifest(section, key)) == f"Failed to load {label}: mod.Cls"

    def test_class_that_raises_in_init(self, tmp_path, method, section, key, label, registry_kw):
        registry = self._registry(tmp_path, registry_kw)
        registry.loader.load_class = lambda manifest, ref: Exploding

        assert (
            getattr(registry, method)(self._manifest(section, key))
            == f"Failed to instantiate {label} 'mod.Cls': boom"
        )
