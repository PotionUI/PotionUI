from types import SimpleNamespace
from unittest.mock import patch

from src.features.forms.binding import bind_form
from src.features.models.form_refs import make_model_ref, resolve_installed_model_filename
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _model_field(name, model_type, default=None):
    return FieldTemplate(
        type="model", name=name, required=True, default=default, configuration={"model_type": model_type}
    )


def _preset(fields):
    forms = [FormTemplate(name="main", fields=fields, default=True, order=0)]
    return PresetTemplate(
        id="p", name="P", version="1", path="/p", modes={"edit": ModeTemplate(forms=forms, pipes=[])}
    )


def _catalog(*rows):
    def lookup(filename):
        return [row for row in rows if row.filename == filename]

    return patch("src.features.models.repository.model_repo", SimpleNamespace(get_by_filename=lookup))


def _row(model_id, filename, model_type, available=True):
    return SimpleNamespace(id=model_id, filename=filename, model_type=model_type, is_available=available)


class TestBindFormModelResolver:
    def test_filename_value_becomes_the_ref_the_resolver_returns(self):
        preset = _preset([_model_field("vae", "vae")])
        seen = []

        def resolver(value, types):
            seen.append((value, types))
            return make_model_ref("M1")

        bound = bind_form(preset, "edit", None, {"vae": "qwen_image_vae.safetensors"}, "u", model_resolver=resolver)

        assert bound.values["vae"] == make_model_ref("M1")
        assert seen == [("qwen_image_vae.safetensors", ["vae"])]

    def test_admin_default_filename_is_resolved_when_the_wire_omits_the_field(self):
        preset = _preset([_model_field("vae", "vae")])
        overrides = {"vae": {"default": "qwen_image_vae.safetensors"}}

        bound = bind_form(
            preset, "edit", None, {}, "u", field_overrides=overrides, model_resolver=lambda v, t: make_model_ref("M1")
        )

        assert bound.values["vae"] == make_model_ref("M1")

    def test_unresolved_filename_and_refs_pass_through(self):
        preset = _preset([_model_field("vae", "vae"), _model_field("text_encoder", "text_encoder")])
        calls = []

        def resolver(value, types):
            calls.append(value)
            return None

        bound = bind_form(
            preset, "edit", None,
            {"vae": "gone.safetensors", "text_encoder": make_model_ref("T1")}, "u", model_resolver=resolver,
        )

        assert bound.values["vae"] == "gone.safetensors"
        assert bound.values["text_encoder"] == make_model_ref("T1")
        assert calls == ["gone.safetensors"]

    def test_no_resolver_leaves_filenames_untouched(self):
        preset = _preset([_model_field("vae", "vae")])
        bound = bind_form(preset, "edit", None, {"vae": "qwen_image_vae.safetensors"}, "u")
        assert bound.values["vae"] == "qwen_image_vae.safetensors"


class TestResolveInstalledModelFilename:
    def test_unique_installed_match_of_the_field_type_resolves(self):
        with _catalog(_row("M1", "qwen_image_vae.safetensors", "vae")):
            assert resolve_installed_model_filename("qwen_image_vae.safetensors", ["vae"]) == make_model_ref("M1")

    def test_path_values_match_on_their_filename(self):
        with _catalog(_row("M1", "qwen_image_vae.safetensors", "vae")):
            assert resolve_installed_model_filename("vae/qwen_image_vae.safetensors", ["vae"]) == make_model_ref("M1")

    def test_other_type_unavailable_and_ambiguous_rows_do_not_resolve(self):
        with _catalog(_row("M1", "x.safetensors", "lora")):
            assert resolve_installed_model_filename("x.safetensors", ["vae"]) is None
        with _catalog(_row("M1", "x.safetensors", "vae", available=False)):
            assert resolve_installed_model_filename("x.safetensors", ["vae"]) is None
        with _catalog(_row("M1", "x.safetensors", "vae"), _row("M2", "x.safetensors", "vae")):
            assert resolve_installed_model_filename("x.safetensors", ["vae"]) is None

    def test_untyped_field_accepts_any_single_match(self):
        with _catalog(_row("M1", "x.safetensors", "checkpoint")):
            assert resolve_installed_model_filename("x.safetensors", []) == make_model_ref("M1")


class TestOrchestratorBind:
    def _orchestrator(self, resolver):
        from unittest.mock import Mock

        from src.features.generation.orchestrator import GenerationOrchestrator

        return GenerationOrchestrator(
            pipeline_builder=Mock(), backend_registry=Mock(), connection_hub=Mock(), settings=Mock(),
            output_processor=Mock(), preset_template_loader=Mock(), model_filename_resolver=resolver,
        )

    def _bound(self, engine, resolver):
        preset = _preset([_model_field("vae", "vae")])
        preset.engine = engine
        orchestrator = self._orchestrator(resolver)
        return orchestrator._bind(preset, "edit", None, {"vae": "qwen_image_vae.safetensors"}, "u", None, None)

    def test_native_presets_resolve_filenames_through_the_shared_bind(self):
        bound = self._bound("native", lambda value, types: make_model_ref("M1"))
        assert bound.values["vae"] == make_model_ref("M1")

    def test_other_engines_keep_their_bare_names(self):
        bound = self._bound("comfyui", lambda value, types: make_model_ref("M1"))
        assert bound.values["vae"] == "qwen_image_vae.safetensors"
