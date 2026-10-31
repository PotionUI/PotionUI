from pathlib import Path
from types import SimpleNamespace

import yaml

from src.features.recipes.catalog import RecipeCatalog
from src.features.recipes.derived import build_derived_recipes, recommendation_providers
from src.features.recipes.lint import explain_recipe, lint_recipe
from src.features.recipes.schema import parse_recipe, validate_recipe_dict
from tests.features.recipes.mode_fixtures import FakeLoader, mode, model_field, preset, video_preset


class Plugins:
    def __init__(self, manifests=()):
        self.manifests = {m.id: m for m in manifests}

    def get_plugin(self, plugin_id):
        return self.manifests.get(plugin_id)

    def get_enabled_plugins(self):
        return []


HF = SimpleNamespace(id="huggingface-provider", capabilities=["marketplace-provider", "model-lookup"])


def test_one_recipe_per_native_preset_with_downloadable_recommendations():
    comfy = preset({"m": mode([model_field("m", "checkpoint", [{"name": "x.safetensors", "link": "https://x/x.safetensors"}])])}, preset_id="C", name="Comfy", engine="comfyui")
    bare = preset({"m": mode([model_field("m", "checkpoint", [])])}, preset_id="B", name="Bare")

    recipes, errors = build_derived_recipes([video_preset(), comfy, bare], Plugins([HF]))

    assert errors == {}
    assert list(recipes) == ["modes-vid-1-0"]
    recipe = recipes["modes-vid-1-0"]
    assert recipe.source == "derived"
    assert recipe.name == "Vid-1.0 modes"
    assert [s.kind for s in recipe.steps] == [
        "plugins.ensure", "backend.ensure", "modes.plan", "artifacts.fetch", "models.index",
        "preset.ensure", "preset.defaults", "pipeline.render",
    ]
    assert recipe.get_step("plugins.ensure").params == {"plugin_ids": ["huggingface-provider"]}
    assert recipe.get_step("modes.plan").params["selected"] == []


def test_provider_plugins_come_from_manifests_never_hardcoded():
    recipes, _ = build_derived_recipes([video_preset()], Plugins([SimpleNamespace(id="huggingface-provider", capabilities=["other"])]))
    assert recipes["modes-vid-1-0"].get_step("plugins.ensure") is None
    assert recommendation_providers(video_preset()) == ["huggingface"]


def test_a_colliding_id_gets_a_preset_suffix():
    recipes, _ = build_derived_recipes([video_preset()], None, taken_ids={"modes-vid-1-0"})
    assert list(recipes) == ["modes-vid-1-0-vid"]


def _write(root: Path, recipe_id: str, preset_id: str):
    data = {
        "schema_version": 1, "id": recipe_id, "version": 1, "name": recipe_id.title(), "engine": "native", "category": "video",
        "presets": [{"preset_id": preset_id}],
        "steps": [{"key": "backend.ensure", "kind": "backend.ensure", "title": "Backend", "params": {"engine": "native"}}],
    }
    (root / "marketplace").mkdir(parents=True, exist_ok=True)
    (root / "marketplace" / f"{recipe_id}.yml").write_text(yaml.safe_dump(data))


def test_catalog_serves_derived_recipes_next_to_file_recipes(tmp_path):
    _write(tmp_path, "vid-starter", "VID")
    catalog = RecipeCatalog(str(tmp_path), plugin_registry=Plugins([HF]))
    loader = FakeLoader(video_preset())
    catalog.attach_presets(loader)

    assert [r.id for r in catalog.list_recipes()] == ["modes-vid-1-0", "vid-starter"]
    assert catalog.get_recipe("modes-vid-1-0", 1).source == "derived"
    assert catalog.install_recipe_for("VID").id == "modes-vid-1-0"
    assert [r.id for r in catalog.recipes_for_preset("VID")] == ["vid-starter", "modes-vid-1-0"]
    assert [r.id for r in catalog.preset_recipe_index()["VID"]] == ["vid-starter", "modes-vid-1-0"]

    loader.presets = []
    assert catalog.get_recipe("modes-vid-1-0") is None
    assert catalog.install_recipe_for("VID") is None


def test_catalog_without_presets_attached_has_no_derived_recipes(tmp_path):
    _write(tmp_path, "vid-starter", "VID")
    assert [r.id for r in RecipeCatalog(str(tmp_path)).list_recipes()] == ["vid-starter"]


class StrictKind:
    def validate_params(self, params, recipe, lint):
        return [] if params.get("ok") else [f"needs ok (presets known: {sorted(lint.preset_index)})"]


def test_catalog_rejects_plugin_step_params_that_fail_their_own_check(tmp_path):
    data = {
        "schema_version": 1, "id": "plugged", "version": 1, "name": "P", "engine": "native", "category": "video",
        "presets": [{"preset_id": "VID"}],
        "steps": [{"key": "c", "kind": "acme.collect", "title": "C", "params": {}}],
    }
    (tmp_path / "marketplace").mkdir(parents=True)
    (tmp_path / "marketplace" / "plugged.yml").write_text(yaml.safe_dump(data))
    registration = SimpleNamespace(kind="acme.collect", executor=StrictKind(), source="acme")
    kinds = SimpleNamespace(all=lambda: [registration], get=lambda kind: registration if kind == "acme.collect" else None)
    catalog = RecipeCatalog(str(tmp_path), step_kind_registry=kinds)
    catalog.attach_presets(FakeLoader(video_preset()))
    catalog.reload()

    assert catalog.get_recipe("plugged") is None
    assert "needs ok (presets known: ['VID'])" in " ".join(next(iter(catalog.load_errors.values())))


def _starter(**plan_params):
    params = {"preset_id": "VID", "modes": "all", "selected": ["video"], "locked": ["video"]}
    params.update(plan_params)
    data = {
        "schema_version": 1, "id": "vid-starter", "version": 2, "name": "Vid", "engine": "native", "category": "video",
        "artifacts": [
            {"id": "dup", "kind": "lora", "model_type": "lora", "filename": "cine.safetensors"},
            {"id": "orphan", "kind": "lora", "model_type": "lora", "filename": "orphan.safetensors"},
        ],
        "presets": [{"preset_id": "VID"}],
        "smoke": {"preset_id": "VID", "mode": "cinemagraph"},
        "steps": [
            {"key": "modes.plan", "kind": "modes.plan", "title": "Plan", "params": params},
            {"key": "artifacts.fetch", "kind": "artifacts.fetch", "title": "Fetch", "params": {"plan": "modes.plan"}},
        ],
    }
    assert validate_recipe_dict(data) == []
    return parse_recipe(data)


def test_lint_rules_r1_to_r6():
    index = {"VID": video_preset()}
    errors, warnings = lint_recipe(_starter(selected=["video", "motion", "ghost"], locked=["video"], preselect_extras=["model"]), index)

    joined = " | ".join(errors + warnings)
    assert "'ghost', which is not a mode" in joined
    assert "needs 'Own Upscaler'" in joined
    assert "preselect_extras names 'model'" in joined
    assert "artifact 'dup'" in joined and "(R4)" in joined
    assert "artifact 'orphan'" in joined and "(R5)" in joined
    assert "smoke: mode 'cinemagraph' must be in" in joined
    assert any("(R3)" in w for w in warnings)

    locked_manual, _ = lint_recipe(_starter(selected=["video", "motion"], locked=["video", "motion"]), index)
    assert any("Own Upscaler" in e and "(R3)" in e for e in locked_manual)
    missing, _ = lint_recipe(_starter(), {})
    assert any("does not exist" in e for e in missing)


def test_lint_r7_runs_plugin_param_checks():
    data = {
        "schema_version": 1, "id": "p", "version": 1, "name": "P", "engine": "native", "category": "video",
        "presets": [{"preset_id": "VID"}],
        "steps": [{"key": "c", "kind": "acme.collect", "title": "C", "params": {}}],
    }
    recipe = parse_recipe(data)
    errors, _ = lint_recipe(recipe, {}, executor_for=lambda kind: StrictKind())
    assert errors == ["step 'c' (acme.collect): needs ok (presets known: []) (R7)"]


def test_explain_lists_modes_slots_and_files():
    lines = explain_recipe(_starter(), {"VID": video_preset()})
    text = "\n".join(lines)

    assert "mode video [locked] - Video" in text
    assert "mode cinemagraph [offered] - Cinemagraph" in text
    assert "* dit-int8 [int8] dit-int8.safetensors 20.00 GB via huggingface" in text
    assert "own_upscaler (required, upscaler): manual" in text
