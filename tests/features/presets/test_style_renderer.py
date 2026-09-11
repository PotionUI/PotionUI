"""Tests for PresetStyleRenderer.render_styles - the orchestrator is mocked
(mirrors PhrasebookPreviewGenerator's own test doubles): one fake generation
that immediately signals completion, and a monkeypatched
`file_repo.get_generation_files` standing in for the real DB-backed lookup.
"""
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from src.features.generation import file_repository as file_repository_module
from src.features.presets.style_renderer import PresetStyleRenderer, set_style_preview
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _preset(tmp_path: Path, styles, with_quantity_field=True) -> PresetTemplate:
    fields = [FieldTemplate(type="seed", name="seed")]
    if with_quantity_field:
        fields.append(FieldTemplate(type="slider", name="quantity"))
    form = FormTemplate(name="custom", fields=fields, default=True)
    mode = ModeTemplate(forms=[form], pipes=[])
    return PresetTemplate(
        id="anima",
        name="Anima",
        version="1.0.0",
        path=str(tmp_path),
        modes={"txt2img": mode},
        styles=styles,
    )


def _style(**overrides):
    style = {
        "id": "retro-90s-cel",
        "name": "Retro 90s Anime Cel",
        "category": "Anime",
        "prepend": "old, ",
        "append": ", retro.",
        "negative": "3d",
        "example_prompt": "a cat",
    }
    style.update(overrides)
    return style


def _write_styles_yml(preset_dir: Path, style: dict) -> None:
    preview_line = f'\n    preview: "{style["preview"]}"' if style.get("preview") else ""
    (preset_dir / "styles.yml").write_text(
        f"""styles:
  - id: "{style['id']}"
    name: "{style['name']}"
    category: "{style['category']}"
    prepend: "{style['prepend']}"
    append: "{style['append']}"
    negative: "{style['negative']}"
    example_prompt: "{style['example_prompt']}"{preview_line}
"""
    )


def _source_image(tmp_path: Path, size=(1000, 500)) -> Path:
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(exist_ok=True)
    rel_path = "generations/out.png"
    full_path = storage_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "blue").save(full_path)
    return storage_dir


def _fake_orchestrator(generation_id="gen-1"):
    orchestrator = Mock()

    async def start_generation(request, user_id, output_callback=None):
        if output_callback is not None:
            await output_callback(generation_id, None)
        return {"generation_id": generation_id}

    orchestrator.start_generation = start_generation
    return orchestrator


def _fake_settings(storage_dir: Path):
    settings = Mock()
    settings.get_file_storage_directory = Mock(return_value=str(storage_dir))
    return settings


@pytest.fixture
def fake_file(monkeypatch):
    """Monkeypatch the shared `file_repo` singleton exactly as
    `style_renderer._render_one` imports it, returning one output file."""
    record = Mock()
    record.file_path = "generations/out.png"
    monkeypatch.setattr(
        file_repository_module.file_repo, "get_generation_files", Mock(return_value=[record])
    )
    return record


class TestRenderStyles:
    @pytest.mark.asyncio
    async def test_renders_and_writes_downscaled_webp(self, tmp_path, fake_file):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        storage_dir = _source_image(tmp_path, size=(1000, 500))
        _write_styles_yml(preset_dir, _style())

        preset = _preset(preset_dir, styles=[_style()])
        loader = Mock()
        loader.load_preset_by_id = Mock(return_value=preset)

        renderer = PresetStyleRenderer(loader, _fake_orchestrator(), _fake_settings(storage_dir))

        result = await renderer.render_styles("anima", "user-1", long_edge=200, seed=1)

        assert result == {"rendered": ["retro-90s-cel"], "failed": []}

        preview_path = preset_dir / "public" / "styles" / "retro-90s-cel.webp"
        assert preview_path.exists()
        with Image.open(preview_path) as img:
            assert img.format == "WEBP"
            assert max(img.size) == 200
            assert img.size == (200, 100)  # aspect ratio preserved (1000x500 -> long edge 200)

        loader.reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_sets_preview_in_styles_yml_when_unset(self, tmp_path, fake_file):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        storage_dir = _source_image(tmp_path)
        _write_styles_yml(preset_dir, _style())

        preset = _preset(preset_dir, styles=[_style()])
        loader = Mock()
        loader.load_preset_by_id = Mock(return_value=preset)
        renderer = PresetStyleRenderer(loader, _fake_orchestrator(), _fake_settings(storage_dir))

        await renderer.render_styles("anima", "user-1")

        text = (preset_dir / "styles.yml").read_text()
        assert 'preview: "public/styles/retro-90s-cel.webp"' in text

    @pytest.mark.asyncio
    async def test_leaves_existing_preview_untouched(self, tmp_path, fake_file):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        storage_dir = _source_image(tmp_path)
        style = _style(preview="public/styles/custom-name.webp")
        _write_styles_yml(preset_dir, style)

        preset = _preset(preset_dir, styles=[style])
        loader = Mock()
        loader.load_preset_by_id = Mock(return_value=preset)
        renderer = PresetStyleRenderer(loader, _fake_orchestrator(), _fake_settings(storage_dir))

        await renderer.render_styles("anima", "user-1")

        text = (preset_dir / "styles.yml").read_text()
        assert 'preview: "public/styles/custom-name.webp"' in text
        # `preview:` in styles.yml is left alone, but the render always
        # writes to the canonical `public/styles/<id>.webp` path.
        assert (preset_dir / "public" / "styles" / "retro-90s-cel.webp").exists()

    @pytest.mark.asyncio
    async def test_forces_quantity_to_one(self, tmp_path, fake_file):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        storage_dir = _source_image(tmp_path)
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, styles=[_style()], with_quantity_field=True)
        loader = Mock()
        loader.load_preset_by_id = Mock(return_value=preset)

        captured = {}
        orchestrator = Mock()

        async def start_generation(request, user_id, output_callback=None):
            captured["form_data"] = request.form_data
            captured["prompt"] = request.prompts[0].positive
            captured["negative"] = request.prompts[0].negative
            if output_callback is not None:
                await output_callback("gen-1", None)
            return {"generation_id": "gen-1"}

        orchestrator.start_generation = start_generation
        renderer = PresetStyleRenderer(loader, orchestrator, _fake_settings(storage_dir))

        await renderer.render_styles("anima", "user-1", seed=42)

        assert captured["form_data"] == {"seed": 42, "quantity": 1}
        assert captured["prompt"] == "old, a cat, retro."
        assert captured["negative"] == "3d"

    @pytest.mark.asyncio
    async def test_unknown_style_id_raises(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, styles=[_style()])
        loader = Mock()
        loader.load_preset_by_id = Mock(return_value=preset)
        renderer = PresetStyleRenderer(loader, _fake_orchestrator(), Mock())

        with pytest.raises(ValueError, match="Unknown style id"):
            await renderer.render_styles("anima", "user-1", style_ids=["bogus"])

    @pytest.mark.asyncio
    async def test_preset_not_found_raises(self):
        loader = Mock()
        loader.load_preset_by_id = Mock(return_value=None)
        renderer = PresetStyleRenderer(loader, _fake_orchestrator(), Mock())

        with pytest.raises(ValueError, match="not found"):
            await renderer.render_styles("missing", "user-1")

    @pytest.mark.asyncio
    async def test_preset_with_no_styles_yml_raises(self, tmp_path):
        preset = _preset(tmp_path, styles=[])
        loader = Mock()
        loader.load_preset_by_id = Mock(return_value=preset)
        renderer = PresetStyleRenderer(loader, _fake_orchestrator(), Mock())

        with pytest.raises(ValueError, match="no styles.yml"):
            await renderer.render_styles("anima", "user-1")

    @pytest.mark.asyncio
    async def test_per_style_failure_is_reported_not_raised(self, tmp_path):
        preset_dir = tmp_path / "preset"
        preset_dir.mkdir()
        _write_styles_yml(preset_dir, _style())
        preset = _preset(preset_dir, styles=[_style()])
        loader = Mock()
        loader.load_preset_by_id = Mock(return_value=preset)

        orchestrator = Mock()
        orchestrator.start_generation = AsyncMock(side_effect=RuntimeError("backend down"))
        renderer = PresetStyleRenderer(loader, orchestrator, Mock())

        result = await renderer.render_styles("anima", "user-1")

        assert result["rendered"] == []
        assert result["failed"] == [{"id": "retro-90s-cel", "error": "backend down"}]
        loader.reload.assert_not_called()


class TestSetStylePreview:
    def test_inserts_preview_after_the_matching_entry(self, tmp_path):
        styles_path = tmp_path / "styles.yml"
        styles_path.write_text(
            """styles:
  - id: "a"
    name: "A"
    example_prompt: "x"
  - id: "b"
    name: "B"
    example_prompt: "y"
"""
        )

        set_style_preview(styles_path, "a", "public/styles/a.webp")

        text = styles_path.read_text()
        assert 'preview: "public/styles/a.webp"' in text
        # Entry "b" is untouched.
        assert text.count("id: \"b\"") == 1
        assert "b.webp" not in text

    def test_replaces_existing_preview_value(self, tmp_path):
        styles_path = tmp_path / "styles.yml"
        styles_path.write_text(
            """styles:
  - id: "a"
    name: "A"
    preview: "public/styles/old.webp"
    example_prompt: "x"
"""
        )

        set_style_preview(styles_path, "a", "public/styles/new.webp")

        text = styles_path.read_text()
        assert 'preview: "public/styles/new.webp"' in text
        assert "old.webp" not in text

    def test_unknown_id_raises(self, tmp_path):
        styles_path = tmp_path / "styles.yml"
        styles_path.write_text('styles:\n  - id: "a"\n    example_prompt: "x"\n')

        with pytest.raises(ValueError, match="not found"):
            set_style_preview(styles_path, "missing", "public/styles/missing.webp")
