"""Tests for the builtin @resource providers."""

import pytest
from datetime import datetime
from unittest.mock import Mock

from src.platform.resources.base import ResourceContext
from src.platform.resources.builtin import (
    PhrasebookResourceProvider,
    FormResourceProvider,
    GenerationsResourceProvider,
    ModelsResourceProvider,
    PresetsResourceProvider,
    register_builtin_resource_providers,
)
from src.platform.resources import ResourceRegistry


def _ctx(**kwargs) -> ResourceContext:
    return ResourceContext(user_id="user-1", mode_id="generation", **kwargs)


class TestRegisterBuiltins:
    def test_registers_all_namespaces(self):
        registry = ResourceRegistry()
        register_builtin_resource_providers(registry)
        namespaces = sorted(p.namespace for p in registry.get_all())
        assert namespaces == ["form", "generations", "models", "phrasebook", "presets"]


class TestModelsProvider:
    def setup_method(self):
        self.provider = ModelsResourceProvider()
        self.repo = Mock()
        self.manager = Mock()
        self.manager.model_repo = self.repo

    def _model(self, filename="detailer.safetensors", model_type="lora",
               triggers=None, provider_tags=None, description=None,
               prompting_guidance=None):
        model = Mock()
        model.id = "model-1"
        model.filename = filename
        model.model_type = model_type
        model.description = description
        model.prompting_guidance = prompting_guidance
        model.model_metadata = {"triggers": triggers or []}
        info = Mock()
        info.name = "Detailer XL"
        info.provider = "civitai-provider"
        info.tags = provider_tags or []
        info.description = "Provider description"
        model.providers = [info]
        return model

    @pytest.mark.asyncio
    async def test_suggest_types_at_root(self):
        suggestions = await self.provider.suggest([], "lo", _ctx(model_index_manager=self.manager))
        assert [s.uri for s in suggestions] == ["models.lora"]
        assert suggestions[0].has_children is True

    @pytest.mark.asyncio
    async def test_suggest_accepts_plural_alias(self):
        suggestions = await self.provider.suggest([], "loras", _ctx(model_index_manager=self.manager))
        assert [s.uri for s in suggestions] == ["models.lora"]

    @pytest.mark.asyncio
    async def test_suggest_models_of_type(self):
        self.repo.get_all.return_value = [self._model()]
        suggestions = await self.provider.suggest(["lora"], "det", _ctx(model_index_manager=self.manager))
        assert suggestions[0].uri == "models.lora.detailer"
        assert suggestions[0].has_children is False
        kwargs = self.repo.get_all.call_args.kwargs
        assert kwargs["model_type"] == "lora"
        assert kwargs["search"] == "det"

    @pytest.mark.asyncio
    async def test_resolve_surfaces_trigger_words(self):
        self.repo.get_all.return_value = [self._model(
            triggers=["add detail"], provider_tags=["detail_slider", "add detail"],
            description="Use weight 0.5-1.0",
        )]
        resolved = await self.provider.resolve(["loras", "detailer"], _ctx(model_index_manager=self.manager))
        assert resolved is not None
        assert resolved.kind == "lora"
        assert "add detail" in resolved.content
        assert "detail_slider" in resolved.content
        assert "Use weight 0.5-1.0" in resolved.content
        assert resolved.metadata["model_id"] == "model-1"

    @pytest.mark.asyncio
    async def test_resolve_surfaces_prompting_guidance(self):
        self.repo.get_all.return_value = [self._model(
            prompting_guidance="Prefer short tag-style prompts.",
        )]
        resolved = await self.provider.resolve(["loras", "detailer"], _ctx(model_index_manager=self.manager))
        assert "### Prompting guidance" in resolved.content
        assert "Prefer short tag-style prompts." in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_name_with_dots_rejoined(self):
        model = self._model(filename="qwen_2.5_vl.safetensors")
        self.repo.get_all.return_value = [model]
        resolved = await self.provider.resolve(["lora", "qwen_2", "5_vl"], _ctx(model_index_manager=self.manager))
        assert resolved is not None
        assert self.repo.get_all.call_args.kwargs["search"] == "qwen_2.5_vl"
        assert resolved.title == "qwen_2.5_vl"

    @pytest.mark.asyncio
    async def test_resolve_unknown_returns_none(self):
        self.repo.get_all.return_value = []
        resolved = await self.provider.resolve(["lora", "nope"], _ctx(model_index_manager=self.manager))
        assert resolved is None

    @pytest.mark.asyncio
    async def test_no_manager_returns_empty(self):
        assert await self.provider.suggest([], "", _ctx()) == []
        assert await self.provider.resolve(["lora", "x"], _ctx()) is None


class TestPhrasebookProvider:
    def setup_method(self):
        self.provider = PhrasebookResourceProvider()
        self.manager = Mock()

    def _value(self, label, value):
        v = Mock()
        v.id = f"val-{label}"
        v.label = label
        v.value = value
        return v

    @pytest.mark.asyncio
    async def test_suggest_maps_search_result(self):
        self.manager.search_phrasebook.return_value = {
            "current_category": {"path": "camera", "name": "Camera"},
            "child_categories": [{"path": "camera.angles", "name": "Angles", "description": "Camera angles"}],
            "values": [{"label": "Dutch", "value": "dutch angle"}],
        }
        suggestions = await self.provider.suggest(["camera"], "", _ctx(phrasebook_manager=self.manager))
        uris = [s.uri for s in suggestions]
        assert "phrasebook.camera.angles" in uris
        assert "phrasebook.camera.Dutch" in uris
        category = next(s for s in suggestions if s.uri == "phrasebook.camera.angles")
        assert category.has_children is True

    @pytest.mark.asyncio
    async def test_suggest_offers_the_typed_category_itself_as_attachable(self):
        """Typing the exact path of a category must surface the category
        itself as a selectable, attachable entry — not just its children and
        values — otherwise there's no way to attach the whole category."""
        self.manager.search_phrasebook.return_value = {
            "current_category": {"path": "camera", "name": "Camera"},
            "child_categories": [{"path": "camera.angles", "name": "Angles", "description": "Camera angles"}],
            "values": [{"label": "Dutch", "value": "dutch angle"}],
        }
        suggestions = await self.provider.suggest(["camera"], "", _ctx(phrasebook_manager=self.manager))

        self_entry = next(s for s in suggestions if s.uri == "phrasebook.camera")
        assert self_entry.kind == "category"
        assert self_entry.has_children is True
        assert self_entry.attachable is True
        assert suggestions[0] is self_entry  # listed first, the obvious default

        # NOT attachable: a subcategory being browsed (not yet the typed
        # path) must still navigate on click/Enter, or a user drilling
        # toward a nested value could never get past the first level —
        # every category along the way would attach instead of descending.
        child = next(s for s in suggestions if s.uri == "phrasebook.camera.angles")
        assert child.attachable is False

        value = next(s for s in suggestions if s.uri == "phrasebook.camera.Dutch")
        assert value.attachable is False  # unset default; values are leaves, always attachable

    @pytest.mark.asyncio
    async def test_suggest_root_level_categories_are_not_attachable(self):
        """Root-level browsing (no exact match yet) must not make categories
        attachable either — clicking them still has to navigate deeper, or
        the whole tree becomes unreachable by click past the first level."""
        self.manager.search_phrasebook.return_value = {
            "current_category": None,
            "child_categories": [{"path": "camera", "name": "Camera", "description": None}],
            "values": [],
        }
        suggestions = await self.provider.suggest([], "", _ctx(phrasebook_manager=self.manager))
        assert suggestions[0].attachable is False

    @pytest.mark.asyncio
    async def test_suggest_leaf_category_with_no_children_is_still_attachable(self):
        """A category with values but no subcategories must still offer
        itself as attachable — this was the exact case that used to leave
        users staring at a bare value list with no way to attach the whole
        category."""
        self.manager.search_phrasebook.return_value = {
            "current_category": {"path": "camera.angles", "name": "Angles"},
            "child_categories": [],
            "values": [{"label": "Dutch", "value": "dutch angle"}],
        }
        suggestions = await self.provider.suggest(["camera", "angles"], "", _ctx(phrasebook_manager=self.manager))

        self_entry = next(s for s in suggestions if s.uri == "phrasebook.camera.angles")
        assert self_entry.attachable is True

    @pytest.mark.asyncio
    async def test_suggest_no_self_entry_when_browsing_root(self):
        self.manager.search_phrasebook.return_value = {
            "current_category": None,
            "child_categories": [{"path": "camera", "name": "Camera", "description": None}],
            "values": [],
        }
        suggestions = await self.provider.suggest([], "", _ctx(phrasebook_manager=self.manager))
        assert len(suggestions) == 1
        assert suggestions[0].uri == "phrasebook.camera"

    @pytest.mark.asyncio
    async def test_resolve_category_lists_values(self):
        category = Mock()
        category.id = "cat-1"
        category.name = "Angles"
        category.description = "Camera angles"
        category.is_active = True
        self.manager.categories.get_by_path.return_value = category
        self.manager.categories.get_children.return_value = []
        self.manager.values.get_by_category.return_value = [
            self._value("Dutch", "dutch angle"), self._value("Low", "low angle"),
        ]
        resolved = await self.provider.resolve(["camera", "angles"], _ctx(phrasebook_manager=self.manager))
        assert resolved.kind == "category"
        assert "Dutch: dutch angle" in resolved.content
        assert "id=val-Dutch" in resolved.content
        assert "State: active" in resolved.content
        assert "Total values: 2" in resolved.content
        assert resolved.metadata["value_count"] == 2

    @pytest.mark.asyncio
    async def test_resolve_category_marks_inactive_values(self):
        category = Mock()
        category.id = "cat-1"
        category.name = "Angles"
        category.description = None
        category.is_active = True
        value = self._value("Dutch", "dutch angle")
        value.is_active = False
        self.manager.categories.get_by_path.return_value = category
        self.manager.categories.get_children.return_value = []
        self.manager.values.get_by_category.return_value = [value]
        resolved = await self.provider.resolve(["camera", "angles"], _ctx(phrasebook_manager=self.manager))
        assert "(inactive)" in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_category_sample_is_capped_and_never_dumps_everything(self):
        category = Mock()
        category.id = "cat-1"
        category.name = "Angles"
        category.description = None
        category.is_active = True
        values = [self._value(f"Angle{i}", f"angle {i}") for i in range(50)]
        self.manager.categories.get_by_path.return_value = category
        self.manager.categories.get_children.return_value = []
        self.manager.values.get_by_category.return_value = values
        resolved = await self.provider.resolve(["camera", "angles"], _ctx(phrasebook_manager=self.manager))
        assert resolved.content.count("- id=val-Angle") == 20
        assert resolved.metadata["sample_count"] == 20
        assert resolved.metadata["value_count"] == 50
        assert "sample, not the full list" in resolved.content
        assert "list_phrasebook_values" in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_category_includes_subcategory_counts(self):
        category = Mock()
        category.id = "cat-1"
        category.name = "Camera"
        category.description = None
        category.is_active = True
        child = Mock()
        child.id = "cat-2"
        child.name = "Angles"
        child.path = "camera.angles"
        self.manager.categories.get_by_path.return_value = category
        self.manager.categories.get_children.return_value = [child]
        self.manager.values.get_by_category.side_effect = [
            [self._value("Wide", "wide shot")],  # values for the requested category
            [self._value("Dutch", "dutch angle"), self._value("Low", "low angle")],  # child's values
        ]
        resolved = await self.provider.resolve(["camera"], _ctx(phrasebook_manager=self.manager))
        assert "Subcategories (1)" in resolved.content
        assert "Angles (camera.angles): 2 values" in resolved.content
        assert resolved.metadata["subcategory_count"] == 1

    @pytest.mark.asyncio
    async def test_resolve_cross_user_isolation_category_not_found(self):
        """resolve() passes ctx.user_id straight through to the manager; a
        category owned by another user must resolve as unknown, never as
        that user's values."""
        def get_by_path(path, user_id):
            return None if user_id != "owner" else Mock(id="cat-1", name="Angles", description=None, is_active=True)

        self.manager.categories.get_by_path.side_effect = get_by_path
        ctx = _ctx(phrasebook_manager=self.manager)
        ctx.user_id = "intruder"
        resolved = await self.provider.resolve(["camera", "angles"], ctx)
        assert resolved is None
        self.manager.values.get_by_category.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_single_value(self):
        # First lookup (full path as category) misses; parent lookup hits.
        category = Mock()
        category.id = "cat-1"
        category.name = "Angles"
        category.description = None
        self.manager.categories.get_by_path.side_effect = [None, category]
        self.manager.values.get_by_category.return_value = [self._value("Dutch", "dutch angle")]
        resolved = await self.provider.resolve(["camera", "angles", "Dutch"], _ctx(phrasebook_manager=self.manager))
        assert resolved.kind == "value"
        assert "Dutch: dutch angle" in resolved.content
        assert "id=val-Dutch" in resolved.content
        assert resolved.metadata["value_id"] == "val-Dutch"

    @pytest.mark.asyncio
    async def test_resolve_unknown_returns_none(self):
        self.manager.categories.get_by_path.return_value = None
        resolved = await self.provider.resolve(["nope"], _ctx(phrasebook_manager=self.manager))
        assert resolved is None


class TestPresetsProvider:
    def setup_method(self):
        self.provider = PresetsResourceProvider()
        self.manager = Mock()
        self.manager.file_repo.list_all_presets.return_value = [
            {"id": "01ABC", "name": "SDXL Realistic", "description": "Photo preset"},
        ]
        self.manager.get_preset.return_value = {"preset": {
            "id": "01ABC",
            "name": "SDXL Realistic",
            "description": "Photo preset",
            "modes": {"txt2img": {}},
            "form": [
                {"name": "angles", "label": "Camera angle",
                 "options": [{"label": "Dutch", "value": "dutch angle"}]},
                {"name": "prompt", "label": "Prompt"},  # no options → not suggested
            ],
        }}

    @pytest.mark.asyncio
    async def test_suggest_presets(self):
        suggestions = await self.provider.suggest([], "real", _ctx(preset_manager=self.manager))
        assert suggestions[0].uri == "presets.01ABC"
        assert suggestions[0].has_children is True

    @pytest.mark.asyncio
    async def test_suggest_option_fields_only(self):
        suggestions = await self.provider.suggest(["01ABC"], "", _ctx(preset_manager=self.manager))
        assert [s.uri for s in suggestions] == ["presets.01ABC.angles"]

    @pytest.mark.asyncio
    async def test_resolve_field_options(self):
        resolved = await self.provider.resolve(["01ABC", "angles"], _ctx(preset_manager=self.manager))
        assert resolved.kind == "form_field"
        assert "Dutch: dutch angle" in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_preset_summary(self):
        resolved = await self.provider.resolve(["01ABC"], _ctx(preset_manager=self.manager))
        assert resolved.kind == "preset"
        assert "angles: 1 options" in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_missing_preset_returns_none(self):
        self.manager.get_preset.side_effect = Exception("not found")
        resolved = await self.provider.resolve(["nope"], _ctx(preset_manager=self.manager))
        assert resolved is None


class TestFormProvider:
    def setup_method(self):
        self.provider = FormResourceProvider()
        self.repo = Mock()
        self.manager = Mock()
        self.manager.model_repo = self.repo

    def _model(self, model_id="m-1", filename="dreamshaper.safetensors", display_name="DreamShaper XL",
               model_type="checkpoint", triggers=None, description=None, prompting_guidance=None):
        model = Mock()
        model.id = model_id
        model.filename = filename
        model.display_name = display_name
        model.model_type = model_type
        model.model_metadata = {"triggers": triggers or []}
        model.providers = []
        model.description = description
        model.prompting_guidance = prompting_guidance
        return model

    def _ctx(self, form_data, preset_manager=None):
        return _ctx(
            model_index_manager=self.manager,
            preset_manager=preset_manager,
            form_state={"preset": "01ABC", "mode": "txt2img", "form_data": form_data},
        )

    def _preset_manager(self, field_types):
        manager = Mock()
        manager.get_form_schema.return_value = {
            "form_schema": {
                "properties": {name: {"type": t} for name, t in field_types.items()}
            }
        }
        return manager

    @pytest.mark.asyncio
    async def test_no_form_state_returns_none(self):
        assert await self.provider.resolve(["model"], _ctx()) is None
        assert await self.provider.suggest([], "", _ctx()) == []

    @pytest.mark.asyncio
    async def test_resolve_scalar_field(self):
        resolved = await self.provider.resolve(["steps"], self._ctx({"steps": 30}))
        assert resolved is not None
        assert resolved.kind == "form_value"
        assert "steps: 30" in resolved.content
        assert resolved.metadata["field"] == "steps"

    @pytest.mark.asyncio
    async def test_resolve_model_field_attaches_rich_model_resource(self):
        self.repo.get_by_id.return_value = self._model(
            triggers=["dreamy"],
            prompting_guidance="Use natural-language prompts.",
        )
        resolved = await self.provider.resolve(["model"], self._ctx({"model": "model:m-1"}))
        assert "DreamShaper XL" in resolved.content
        assert "## Model: dreamshaper" in resolved.content
        assert "dreamy" in resolved.content
        assert "Use natural-language prompts." in resolved.content
        assert resolved.kind == "checkpoint"
        assert resolved.metadata["model_id"] == "m-1"
        self.repo.get_by_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_model_field_unknown_model_falls_back_to_raw_value(self):
        self.repo.get_by_id.return_value = None
        resolved = await self.provider.resolve(["model"], self._ctx({"model": "model:gone"}))
        assert resolved is not None
        assert resolved.kind == "form_value"
        assert "model:gone" in resolved.content
        assert "not in the model index" in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_model_typed_field_by_schema_without_ref_value(self):
        preset_manager = self._preset_manager({"model": "model"})
        self.repo.get_by_file_path.return_value = self._model()
        resolved = await self.provider.resolve(
            ["model"], self._ctx({"model": "checkpoints/dreamshaper.safetensors"}, preset_manager)
        )
        assert "## Model: dreamshaper" in resolved.content
        self.repo.get_by_file_path.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_lora_picker_lists_names_and_strengths(self):
        self.repo.get_by_id.return_value = self._model("l-1", "detail.safetensors", "Detail LoRA")
        resolved = await self.provider.resolve(
            ["loras"], self._ctx({"loras": [{"model": "model:l-1", "strength": 0.8}]})
        )
        assert "Detail LoRA @ 0.8" in resolved.content

    @pytest.mark.asyncio
    async def test_suggest_marks_lora_picker_field_browsable(self):
        preset_manager = self._preset_manager({"loras": "lora_picker", "steps": "slider"})
        suggestions = await self.provider.suggest(
            [], "",
            self._ctx({"loras": [{"model": "model:l-1", "strength": 0.8}], "steps": 30}, preset_manager),
        )
        lora = next(s for s in suggestions if s.uri == "form.loras")
        assert lora.has_children is True
        assert lora.attachable is True
        assert "1 LoRA selected" in lora.description
        steps = next(s for s in suggestions if s.uri == "form.steps")
        assert steps.has_children is False

    @pytest.mark.asyncio
    async def test_suggest_lora_rows_lists_selected_loras_with_weights(self):
        preset_manager = self._preset_manager({"loras": "lora_picker"})
        self.repo.get_by_id.side_effect = lambda model_id, **kw: {
            "l-1": self._model("l-1", "detail.safetensors", "Detail LoRA", model_type="lora"),
            "l-2": self._model("l-2", "grain.safetensors", "Film Grain", model_type="lora"),
        }.get(model_id)
        suggestions = await self.provider.suggest(
            ["loras"], "",
            self._ctx(
                {"loras": [
                    {"model": "model:l-1", "strength": 0.8},
                    {"model": "model:l-2", "strength": 1.0},
                ]},
                preset_manager,
            ),
        )
        assert suggestions[0].uri == "form.loras"
        assert suggestions[0].attachable is True
        row_labels = [s.label for s in suggestions[1:]]
        assert row_labels == ["Detail LoRA @ 0.8", "Film Grain @ 1"]
        assert [s.uri for s in suggestions[1:]] == ["form.loras.l-1", "form.loras.l-2"]
        assert all(s.has_children is False for s in suggestions[1:])

    @pytest.mark.asyncio
    async def test_suggest_non_lora_field_has_no_children(self):
        preset_manager = self._preset_manager({"steps": "slider"})
        assert await self.provider.suggest(["steps"], "", self._ctx({"steps": 30}, preset_manager)) == []

    @pytest.mark.asyncio
    async def test_resolve_lora_row_attaches_model_resource_with_weight(self):
        preset_manager = self._preset_manager({"loras": "lora_picker"})
        self.repo.get_by_id.return_value = self._model(
            "l-1", "detail.safetensors", "Detail LoRA", model_type="lora",
            triggers=["add detail"], prompting_guidance="Keep weight under 1.",
        )
        resolved = await self.provider.resolve(
            ["loras", "l-1"],
            self._ctx({"loras": [{"model": "model:l-1", "strength": 0.8}]}, preset_manager),
        )
        assert resolved is not None
        assert resolved.kind == "lora"
        assert "Detail LoRA at weight 0.8" in resolved.content
        assert "add detail" in resolved.content
        assert "Keep weight under 1." in resolved.content
        assert resolved.metadata["strength"] == 0.8
        assert resolved.metadata["model_id"] == "l-1"

    @pytest.mark.asyncio
    async def test_resolve_lora_row_by_index(self):
        preset_manager = self._preset_manager({"loras": "lora_picker"})
        self.repo.get_by_id.return_value = self._model("l-1", "detail.safetensors", "Detail LoRA", model_type="lora")
        resolved = await self.provider.resolve(
            ["loras", "0"],
            self._ctx({"loras": [{"model": "model:l-1", "strength": 0.5}]}, preset_manager),
        )
        assert resolved is not None
        assert "Detail LoRA at weight 0.5" in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_lora_row_unknown_selector_returns_none(self):
        preset_manager = self._preset_manager({"loras": "lora_picker"})
        resolved = await self.provider.resolve(
            ["loras", "nope"],
            self._ctx({"loras": [{"model": "model:l-1", "strength": 0.5}]}, preset_manager),
        )
        assert resolved is None

    @pytest.mark.asyncio
    async def test_resolve_lora_row_unindexed_model_falls_back(self):
        preset_manager = self._preset_manager({"loras": "lora_picker"})
        self.repo.get_by_id.return_value = None
        resolved = await self.provider.resolve(
            ["loras", "l-1"],
            self._ctx({"loras": [{"model": "model:l-1", "strength": 0.5}]}, preset_manager),
        )
        assert resolved is not None
        assert resolved.kind == "form_value"
        assert "Not found in the model index" in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_selector_on_non_lora_field_returns_none(self):
        preset_manager = self._preset_manager({"steps": "slider"})
        assert await self.provider.resolve(
            ["steps", "0"], self._ctx({"steps": 30}, preset_manager)
        ) is None

    @pytest.mark.asyncio
    async def test_resolve_case_insensitive_field(self):
        resolved = await self.provider.resolve(["Steps"], self._ctx({"steps": 30}))
        assert resolved is not None

    @pytest.mark.asyncio
    async def test_resolve_unknown_field_returns_none(self):
        assert await self.provider.resolve(["nope"], self._ctx({"steps": 30})) is None

    @pytest.mark.asyncio
    async def test_resolve_bare_form_dumps_scalars(self):
        resolved = await self.provider.resolve(
            [], self._ctx({"steps": 30, "prompt": "a cat", "empty": "", "segments": [{"x": 1}]})
        )
        assert resolved.kind == "form"
        assert "steps: 30" in resolved.content
        assert "prompt: a cat" in resolved.content
        # empty + complex non-model fields are skipped in the dump
        assert "empty" not in resolved.content
        assert "segments" not in resolved.content

    @pytest.mark.asyncio
    async def test_suggest_lists_fields_and_all(self):
        suggestions = await self.provider.suggest([], "", self._ctx({"steps": 30, "cfg": 7}))
        uris = [s.uri for s in suggestions]
        assert "form" in uris  # the "All form values" leaf
        assert "form.steps" in uris
        assert "form.cfg" in uris

    @pytest.mark.asyncio
    async def test_suggest_prefix_filters_fields(self):
        suggestions = await self.provider.suggest([], "st", self._ctx({"steps": 30, "cfg": 7}))
        uris = [s.uri for s in suggestions]
        assert "form.steps" in uris
        assert "form.cfg" not in uris

    def _ctx_with_variables(self, form_data, variables):
        return _ctx(
            model_index_manager=self.manager,
            form_state={
                "preset": "01ABC", "mode": "txt2img",
                "form_data": form_data, "variables": variables,
            },
        )

    @pytest.mark.asyncio
    async def test_bare_dump_appends_prompt_variables_section(self):
        ctx = self._ctx_with_variables(
            {"steps": 30},
            [{"name": "mood", "type": "choice", "options": ["noir", "sunlit"],
              "mode": "shuffle", "lastRoll": "sunlit"}],
        )
        resolved = await self.provider.resolve([], ctx)
        assert "## Prompt variables" in resolved.content
        assert "- mood: one of noir, sunlit — shuffles each generation; last roll: sunlit" in resolved.content
        assert "steps: 30" in resolved.content

    @pytest.mark.asyncio
    async def test_bare_dump_variables_only_when_no_fields(self):
        ctx = self._ctx_with_variables(
            {},
            [{"name": "subject", "type": "text", "value": "a fox"}],
        )
        resolved = await self.provider.resolve([], ctx)
        assert "## Prompt variables" in resolved.content
        assert "- subject: a fox" in resolved.content
        assert "no values set yet" not in resolved.content

    @pytest.mark.asyncio
    async def test_bare_dump_no_variables_key_unchanged(self):
        resolved = await self.provider.resolve([], self._ctx({"steps": 30}))
        assert "Prompt variables" not in resolved.content


class TestGenerationsProvider:
    def setup_method(self):
        self.provider = GenerationsResourceProvider()
        self.repo = Mock()
        self.model_repo = Mock()

    def _generation(self, gen_id="GEN1"):
        gen = Mock()
        gen.id = gen_id
        gen.status = "completed"
        gen.preset_id = "01ABC"
        gen.created_at = datetime(2026, 7, 1, 12, 0)
        gen.form_data = {"prompt": "a cat", "steps": 30}
        return gen

    @pytest.mark.asyncio
    async def test_suggest_lists_recent_plus_ids(self):
        self.repo.get_all.return_value = [self._generation()]
        suggestions = await self.provider.suggest([], "", _ctx(generation_repository=self.repo))
        assert suggestions[0].uri == "generations.recent"
        assert any(s.uri == "generations.GEN1" for s in suggestions)

    @pytest.mark.asyncio
    async def test_resolve_by_id_scopes_to_user(self):
        gen = self._generation()
        self.repo.get_by_id.return_value = gen
        model = Mock()
        model.filename = "sdxl.safetensors"
        model.model_type = "checkpoint"
        self.model_repo.get_by_generation.return_value = [model]
        resolved = await self.provider.resolve(
            ["GEN1"],
            _ctx(generation_repository=self.repo, generation_model_repository=self.model_repo),
        )
        self.repo.get_by_id.assert_called_once_with("GEN1", user_id="user-1")
        assert "a cat" in resolved.content
        assert "sdxl.safetensors (checkpoint)" in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_recent(self):
        self.repo.get_all.return_value = [self._generation("GEN1"), self._generation("GEN2")]
        resolved = await self.provider.resolve(["recent"], _ctx(generation_repository=self.repo))
        assert resolved.kind == "listing"
        assert "GEN1" in resolved.content and "GEN2" in resolved.content

    @pytest.mark.asyncio
    async def test_resolve_unknown_id_returns_none(self):
        self.repo.get_by_id.return_value = None
        resolved = await self.provider.resolve(["NOPE"], _ctx(generation_repository=self.repo))
        assert resolved is None
