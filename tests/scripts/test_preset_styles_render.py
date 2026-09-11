"""Tests for scripts/preset_styles_render.py's per-style rendering loop,
model resolution, and CLI exit code. `render_styles`/`resolve_model_form_data`
are exercised directly against fakes (duck-typed to the same
`can_run`/`run_case`/`resolve` contracts `HeadlessGenerationClient`/
`ModelResolver` expose) so no container/GPU/live-DB access is involved;
`main` is exercised with `_boot_client` and `build_live_resolver` both
monkeypatched to those fakes.
"""
from pathlib import Path

import pytest
import yaml
from PIL import Image

import scripts.preset_styles_render as psr
from src.features.preset_suite.models import CaseOutcome
from src.features.preset_suite.resolver import ResolveResult
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _preset(preset_dir: Path, styles, extra_fields=(), styles_preview=None) -> PresetTemplate:
    fields = [FieldTemplate(type="seed", name="seed")]
    fields += [FieldTemplate(type="slider", name=name) for name in extra_fields]
    form = FormTemplate(name="custom", fields=fields, default=True)
    mode = ModeTemplate(forms=[form], pipes=[])
    kwargs = {}
    if styles_preview is not None:
        kwargs["styles_preview"] = styles_preview
    return PresetTemplate(
        id="anima", name="Anima", version="1.0.0", path=str(preset_dir), modes={"txt2img": mode}, styles=styles,
        **kwargs,
    )


def _style(**overrides):
    style = {
        "id": "retro-90s-cel",
        "name": "Retro 90s Anime Cel",
        "prepend": "old, ",
        "append": ", retro.",
        "negative": "3d",
        "example_prompt": "a cat",
    }
    style.update(overrides)
    return style


def _write_styles_yml(preset_dir: Path, style: dict) -> None:
    (preset_dir / "styles.yml").write_text(
        f"""styles:
  - id: "{style['id']}"
    name: "{style['name']}"
    example_prompt: "{style['example_prompt']}"
"""
    )


def _write_tests_yml(preset_dir: Path, models: dict) -> None:
    """`models`: field name -> sha256. Writes one case ("case-1") whose
    `models:` map is exactly this, the shape `resolve_model_form_data` reads."""
    data = {
        "schema": 1,
        "cases": [
            {
                "name": "case-1",
                "mode": "txt2img",
                "seed": 1,
                "models": {field: {"sha256": sha} for field, sha in models.items()},
            }
        ],
    }
    (preset_dir / "tests.yml").write_text(yaml.safe_dump(data, sort_keys=False))


class FakeClient:
    """Duck-types `can_run`/`run_case`; outcomes are consumed in call order."""

    def __init__(self, outcomes=None, can_run=(True, "")):
        self._outcomes = list(outcomes or [])
        self._can_run = can_run
        self.run_case_calls = []

    def can_run(self, preset_id, engine):
        return self._can_run

    def run_case(self, preset_id, mode, form_data, *, prompt=None, negative_prompt=None, max_seconds=None):
        self.run_case_calls.append(
            {"preset_id": preset_id, "mode": mode, "form_data": form_data, "prompt": prompt, "negative_prompt": negative_prompt}
        )
        return self._outcomes.pop(0)


class FakeResolver:
    """Duck-types `ModelResolver.resolve`, keyed by the ref's sha256."""

    def __init__(self, by_sha: dict = None):
        self.by_sha = by_sha or {}
        self.calls = []

    def resolve(self, ref, *, model_type=None):
        self.calls.append({"sha256": ref.sha256, "model_type": model_type})
        path = self.by_sha.get(ref.sha256)
        if path is not None:
            return ResolveResult(path, source="fake")
        return ResolveResult(None, reason=f"model sha256={ref.sha256[:12]}… not found in fake resolver")


def _completed_outcome(size=(200, 100)) -> CaseOutcome:
    return CaseOutcome(status="completed", images=[Image.new("RGB", size, "blue")])


class TestRenderStyles:
    def test_renders_and_reports_ok(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False, model_form_data={})

        assert len(lines) == 1
        assert lines[0].startswith("ok retro-90s-cel ")
        assert (preset_dir / "public" / "styles" / "retro-90s-cel.webp").exists()
        call = client.run_case_calls[0]
        assert call["prompt"] == "old, a cat, retro."
        assert call["negative_prompt"] == "3d"

    def test_model_form_data_lands_in_submitted_form(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])

        psr.render_styles(
            client, preset, None, long_edge=64, seed=1, force=False,
            model_form_data={"diffusion_model": "/models/dit.safetensors", "text_encoder": "/models/te.safetensors"},
        )

        submitted = client.run_case_calls[0]["form_data"]
        assert submitted["diffusion_model"] == "/models/dit.safetensors"
        assert submitted["text_encoder"] == "/models/te.safetensors"
        assert submitted["seed"] == 1

    def test_styles_preview_defaults_applied_to_prompt_and_negative(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(
            preset_dir, [_style()],
            styles_preview={"prompt_prefix": "masterpiece, best quality, ", "negative": "worst quality"},
        )
        client = FakeClient(outcomes=[_completed_outcome()])

        psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False, model_form_data={})

        call = client.run_case_calls[0]
        assert call["prompt"] == "masterpiece, best quality, old, a cat, retro."
        assert call["negative_prompt"] == "worst quality, 3d"

    def test_steps_and_resolution_applied_when_fields_present(self, tmp_path, capsys):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()], extra_fields=["steps", "resolution"])
        client = FakeClient(outcomes=[_completed_outcome()])

        psr.render_styles(
            client, preset, None, long_edge=64, seed=1, force=False, model_form_data={},
            steps=30, resolution="1024x1024",
        )

        submitted = client.run_case_calls[0]["form_data"]
        assert submitted["steps"] == 30
        assert submitted["resolution"] == "1024x1024"
        assert "steps: 30; resolution: 1024x1024" in capsys.readouterr().out

    def test_steps_and_resolution_ignored_when_fields_absent(self, tmp_path, capsys):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])  # no steps/resolution fields
        client = FakeClient(outcomes=[_completed_outcome()])

        psr.render_styles(
            client, preset, None, long_edge=64, seed=1, force=False, model_form_data={},
            steps=30, resolution="1024x1024",
        )

        submitted = client.run_case_calls[0]["form_data"]
        assert "steps" not in submitted
        assert "resolution" not in submitted
        out = capsys.readouterr().out
        assert "--steps ignored" in out
        assert "--resolution ignored" in out

    def test_steps_and_resolution_report_preset_default_when_omitted(self, tmp_path, capsys):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()], extra_fields=["steps", "resolution"])
        client = FakeClient(outcomes=[_completed_outcome()])

        psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False, model_form_data={})

        assert "steps: preset default; resolution: preset default" in capsys.readouterr().out

    def test_fills_preview_when_unset(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])

        psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False, model_form_data={})

        text = (preset_dir / "styles.yml").read_text()
        assert 'preview: "public/styles/retro-90s-cel.webp"' in text

    def test_skips_existing_preview_without_force(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        dest = preset_dir / "public" / "styles" / "retro-90s-cel.webp"
        dest.parent.mkdir(parents=True)
        Image.new("RGB", (10, 10), "green").save(dest, format="WEBP")
        client = FakeClient(outcomes=[])

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False, model_form_data={})

        assert lines == ["skip retro-90s-cel (preview exists)"]
        assert client.run_case_calls == []

    def test_force_re_renders_existing_preview(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        dest = preset_dir / "public" / "styles" / "retro-90s-cel.webp"
        dest.parent.mkdir(parents=True)
        Image.new("RGB", (10, 10), "green").save(dest, format="WEBP")
        client = FakeClient(outcomes=[_completed_outcome()])

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=True, model_form_data={})

        assert lines[0].startswith("ok retro-90s-cel ")
        assert len(client.run_case_calls) == 1

    def test_failed_generation_is_reported_not_raised(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[CaseOutcome(status="failed", error="backend down")])

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False, model_form_data={})

        assert lines == ["FAILED retro-90s-cel: backend down"]

    def test_unrunnable_preset_fails_every_target(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(can_run=(False, "no configured backend for engine 'comfyui'"))

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False, model_form_data={})

        assert lines == ["FAILED retro-90s-cel: no configured backend for engine 'comfyui'"]
        assert client.run_case_calls == []

    def test_unknown_style_id_raises(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient()

        with pytest.raises(ValueError, match="Unknown style id"):
            psr.render_styles(client, preset, ["bogus"], long_edge=64, seed=1, force=False, model_form_data={})


class TestResolveModelFormData:
    def test_resolves_first_case_models(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        sha_dit, sha_te = "a" * 64, "b" * 64
        _write_tests_yml(preset_dir, {"diffusion_model": sha_dit, "text_encoder": sha_te})
        preset = _preset(preset_dir, [_style()])
        resolver = FakeResolver({sha_dit: "/models/dit.safetensors", sha_te: "/models/te.safetensors"})

        result = psr.resolve_model_form_data(preset, resolver)

        assert result == {"diffusion_model": "/models/dit.safetensors", "text_encoder": "/models/te.safetensors"}
        assert {c["sha256"] for c in resolver.calls} == {sha_dit, sha_te}

    def test_no_tests_yml_reports_and_returns_none(self, tmp_path, capsys):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        preset = _preset(preset_dir, [_style()])

        result = psr.resolve_model_form_data(preset, FakeResolver())

        assert result is None
        assert "has no tests.yml" in capsys.readouterr().out

    def test_first_case_with_no_models_reports_and_returns_none(self, tmp_path, capsys):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_tests_yml(preset_dir, {})
        preset = _preset(preset_dir, [_style()])

        result = psr.resolve_model_form_data(preset, FakeResolver())

        assert result is None
        assert "declares no models" in capsys.readouterr().out

    def test_unresolved_ref_reports_and_returns_none(self, tmp_path, capsys):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        sha_dit = "a" * 64
        _write_tests_yml(preset_dir, {"diffusion_model": sha_dit})
        preset = _preset(preset_dir, [_style()])
        resolver = FakeResolver({})  # nothing resolves

        result = psr.resolve_model_form_data(preset, resolver)

        assert result is None
        out = capsys.readouterr().out
        assert "error: model 'diffusion_model':" in out


class TestResolvePreset:
    def test_resolves_by_id(self, tmp_path):
        preset = _preset(tmp_path, [])
        loader = type("Loader", (), {"load_preset_by_id": staticmethod(lambda ref: preset if ref == "anima" else None), "presets": []})()

        assert psr._resolve_preset(loader, "anima") is preset

    def test_resolves_by_directory(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        preset = _preset(preset_dir, [])
        loader = type("Loader", (), {"load_preset_by_id": staticmethod(lambda ref: None), "presets": [preset]})()

        assert psr._resolve_preset(loader, str(preset_dir)) is preset

    def test_unknown_ref_returns_none(self, tmp_path):
        loader = type("Loader", (), {"load_preset_by_id": staticmethod(lambda ref: None), "presets": []})()

        assert psr._resolve_preset(loader, "bogus") is None


def _container_for(preset_dir: Path, preset) -> object:
    return type("Container", (), {
        "preset_template_loader": type("Loader", (), {
            "load_preset_by_id": staticmethod(lambda ref: preset if ref == str(preset_dir) else None),
            "presets": [preset],
        })()
    })()


class TestMain:
    def test_exits_nonzero_when_a_style_fails(self, tmp_path, monkeypatch):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        sha = "a" * 64
        _write_tests_yml(preset_dir, {"diffusion_model": sha})
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[CaseOutcome(status="failed", error="boom")])
        resolver = FakeResolver({sha: "/models/dit.safetensors"})
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (client, _container_for(preset_dir, preset)))
        monkeypatch.setattr(psr, "build_live_resolver", lambda allow_download=False: resolver)

        code = psr.main([str(preset_dir)])

        assert code == 1

    def test_exits_zero_when_all_styles_render(self, tmp_path, monkeypatch):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        sha = "a" * 64
        _write_tests_yml(preset_dir, {"diffusion_model": sha})
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])
        resolver = FakeResolver({sha: "/models/dit.safetensors"})
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (client, _container_for(preset_dir, preset)))
        monkeypatch.setattr(psr, "build_live_resolver", lambda allow_download=False: resolver)

        code = psr.main([str(preset_dir)])

        assert code == 0
        assert client.run_case_calls[0]["form_data"]["diffusion_model"] == "/models/dit.safetensors"

    def test_steps_and_resolution_flags_reach_the_submitted_form(self, tmp_path, monkeypatch):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        sha = "a" * 64
        _write_tests_yml(preset_dir, {"diffusion_model": sha})
        preset = _preset(preset_dir, [_style()], extra_fields=["steps", "resolution"])
        client = FakeClient(outcomes=[_completed_outcome()])
        resolver = FakeResolver({sha: "/models/dit.safetensors"})
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (client, _container_for(preset_dir, preset)))
        monkeypatch.setattr(psr, "build_live_resolver", lambda allow_download=False: resolver)

        code = psr.main([str(preset_dir), "--steps", "30", "--resolution", "1024x1024"])

        assert code == 0
        submitted = client.run_case_calls[0]["form_data"]
        assert submitted["steps"] == 30
        assert submitted["resolution"] == "1024x1024"

    def test_completed_run_removes_its_run_dir(self, tmp_path, monkeypatch):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        sha = "a" * 64
        _write_tests_yml(preset_dir, {"diffusion_model": sha})
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])
        resolver = FakeResolver({sha: "/models/dit.safetensors"})
        run_dir = tmp_path / "ephemeral-run"
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (client, _container_for(preset_dir, preset)))
        monkeypatch.setattr(psr, "build_live_resolver", lambda allow_download=False: resolver)
        monkeypatch.setattr(psr.tempfile, "mkdtemp", lambda prefix=None: str(run_dir))

        code = psr.main([str(preset_dir)])

        assert code == 0
        assert not run_dir.exists()

    def test_unresolved_model_exits_nonzero_before_any_generation(self, tmp_path, monkeypatch):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        _write_tests_yml(preset_dir, {"diffusion_model": "a" * 64})
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])
        resolver = FakeResolver({})  # nothing resolves
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (client, _container_for(preset_dir, preset)))
        monkeypatch.setattr(psr, "build_live_resolver", lambda allow_download=False: resolver)

        code = psr.main([str(preset_dir)])

        assert code == 1
        assert client.run_case_calls == []

    def test_missing_tests_yml_exits_nonzero_before_any_generation(self, tmp_path, monkeypatch):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (client, _container_for(preset_dir, preset)))
        monkeypatch.setattr(psr, "build_live_resolver", lambda allow_download=False: FakeResolver())

        code = psr.main([str(preset_dir)])

        assert code == 1
        assert client.run_case_calls == []

    def test_unknown_preset_exits_nonzero(self, tmp_path, monkeypatch):
        container = type("Container", (), {
            "preset_template_loader": type("Loader", (), {
                "load_preset_by_id": staticmethod(lambda ref: None),
                "presets": [],
            })()
        })()
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (FakeClient(), container))
        monkeypatch.setattr(psr, "build_live_resolver", lambda allow_download=False: FakeResolver())

        code = psr.main(["does-not-exist"])

        assert code == 1
