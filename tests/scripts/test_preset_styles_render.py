"""Tests for scripts/preset_styles_render.py's per-style rendering loop and
CLI exit code. `render_styles` is exercised directly against a fake
`HeadlessGenerationClient`-shaped client (duck-typed to `can_run`/`run_case`,
same contract `src.features.preset_suite.runner.GenerationClient` declares) so
no container/GPU boot is involved; `main` is exercised with `_boot_client`
monkeypatched to the same fake.
"""
from pathlib import Path

import pytest
from PIL import Image

import scripts.preset_styles_render as psr
from src.features.preset_suite.models import CaseOutcome
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _preset(preset_dir: Path, styles) -> PresetTemplate:
    form = FormTemplate(name="custom", fields=[FieldTemplate(type="seed", name="seed")], default=True)
    mode = ModeTemplate(forms=[form], pipes=[])
    return PresetTemplate(
        id="anima", name="Anima", version="1.0.0", path=str(preset_dir), modes={"txt2img": mode}, styles=styles
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


def _completed_outcome(size=(200, 100)) -> CaseOutcome:
    return CaseOutcome(status="completed", images=[Image.new("RGB", size, "blue")])


class TestRenderStyles:
    def test_renders_and_reports_ok(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False)

        assert len(lines) == 1
        assert lines[0].startswith("ok retro-90s-cel ")
        assert (preset_dir / "public" / "styles" / "retro-90s-cel.webp").exists()
        call = client.run_case_calls[0]
        assert call["prompt"] == "old, a cat, retro."
        assert call["negative_prompt"] == "3d"

    def test_fills_preview_when_unset(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])

        psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False)

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

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False)

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

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=True)

        assert lines[0].startswith("ok retro-90s-cel ")
        assert len(client.run_case_calls) == 1

    def test_failed_generation_is_reported_not_raised(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[CaseOutcome(status="failed", error="backend down")])

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False)

        assert lines == ["FAILED retro-90s-cel: backend down"]

    def test_unrunnable_preset_fails_every_target(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(can_run=(False, "no configured backend for engine 'comfyui'"))

        lines = psr.render_styles(client, preset, None, long_edge=64, seed=1, force=False)

        assert lines == ["FAILED retro-90s-cel: no configured backend for engine 'comfyui'"]
        assert client.run_case_calls == []

    def test_unknown_style_id_raises(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient()

        with pytest.raises(ValueError, match="Unknown style id"):
            psr.render_styles(client, preset, ["bogus"], long_edge=64, seed=1, force=False)


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


class TestMain:
    def test_exits_nonzero_when_a_style_fails(self, tmp_path, monkeypatch):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[CaseOutcome(status="failed", error="boom")])
        container = type("Container", (), {
            "preset_template_loader": type("Loader", (), {
                "load_preset_by_id": staticmethod(lambda ref: preset if ref == str(preset_dir) else None),
                "presets": [preset],
            })()
        })()
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (client, container))

        code = psr.main([str(preset_dir)])

        assert code == 1

    def test_exits_zero_when_all_styles_render(self, tmp_path, monkeypatch):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, [_style()])
        client = FakeClient(outcomes=[_completed_outcome()])
        container = type("Container", (), {
            "preset_template_loader": type("Loader", (), {
                "load_preset_by_id": staticmethod(lambda ref: preset if ref == str(preset_dir) else None),
                "presets": [preset],
            })()
        })()
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (client, container))

        code = psr.main([str(preset_dir)])

        assert code == 0

    def test_unknown_preset_exits_nonzero(self, tmp_path, monkeypatch):
        container = type("Container", (), {
            "preset_template_loader": type("Loader", (), {
                "load_preset_by_id": staticmethod(lambda ref: None),
                "presets": [],
            })()
        })()
        monkeypatch.setattr(psr, "_boot_client", lambda run_dir: (FakeClient(), container))

        code = psr.main(["does-not-exist"])

        assert code == 1
