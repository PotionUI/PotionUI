"""`GET /presets/imported` lists presets under content/presets/local this
plugin created, identified by `description.md`'s opening line (see
`preset_import.emit.emit_preset`'s `IMPORT_PROVENANCE_PREFIX`) - the only
provenance marker an imported preset carries, since nothing else records
that a given preset directory came from this importer.
"""

from dataclasses import dataclass
from typing import Any, Optional

import pytest
import yaml

from backend import api
from backend.preset_import.emit import IMPORT_PROVENANCE_PREFIX


@dataclass
class _FakeAPIResponse:
    """Duck-types `src.platform.http.base_controller.APIResponse` (a
    `src.platform` internal, not re-exported through `src.plugin_api`) - only
    the `success`/`data` fields `_peek_requirements_summary` reads."""

    success: bool
    data: Optional[Any] = None


def _write_preset(
    root, family, variant, *, preset_id, name, mode="txt2img", imported=True, ui_format=False
):
    variant_dir = root / family / variant
    workflows_dir = variant_dir / "modes" / mode / "files" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)

    (variant_dir / "preset.yml").write_text(
        yaml.safe_dump({"schema": 1, "id": preset_id, "name": name, "engine": "comfyui", "modes": [mode]})
    )
    description = (
        f"{IMPORT_PROVENANCE_PREFIX} (3 nodes). Imported preset."
        if imported
        else "Hand-authored preset, not from the importer."
    )
    (variant_dir / "description.md").write_text(description)

    (workflows_dir / f"{mode}.json").write_text("{}")
    if ui_format:
        (workflows_dir / f"{mode}.ui.json").write_text("{}")

    return variant_dir


class FakePresetController:
    def __init__(self, summaries):
        self._summaries = summaries

    async def get_preset(self, preset_id):
        summary = self._summaries.get(preset_id)
        if summary is None:
            return _FakeAPIResponse(success=False)
        return _FakeAPIResponse(success=True, data={"requirements_summary": summary})


class FakeContainer:
    def __init__(self, summaries):
        self.preset_controller = FakePresetController(summaries)


@pytest.fixture(autouse=True)
def _no_container_by_default(monkeypatch):
    """Every test below stands up its own FakeContainer via `_container`;
    this guards against a test that forgets to and would otherwise reach the
    real process container (and its real database) from a plugin unit test."""
    monkeypatch.setattr(api, "get_container", lambda: (_ for _ in ()).throw(AssertionError("no container configured for this test")))


def _container(monkeypatch, summaries=None):
    monkeypatch.setattr(api, "get_container", lambda: FakeContainer(summaries or {}))


class TestListImportedPresets:
    @pytest.mark.asyncio
    async def test_empty_root_returns_no_presets(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path / "does-not-exist")
        _container(monkeypatch)
        result = await api.list_imported_presets(current_user=None)
        assert result["presets"] == []

    @pytest.mark.asyncio
    async def test_only_imported_presets_are_listed(self, tmp_path, monkeypatch):
        _write_preset(tmp_path, "SDXL", "imported", preset_id="P1", name="My import", imported=True)
        _write_preset(tmp_path, "SDXL", "handmade", preset_id="P2", name="Hand-authored", imported=False)
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path)
        _container(monkeypatch)

        result = await api.list_imported_presets(current_user=None)

        assert [p["preset_id"] for p in result["presets"]] == ["P1"]
        assert result["presets"][0]["name"] == "My import"
        assert result["presets"][0]["family"] == "SDXL"
        assert result["presets"][0]["variant"] == "imported"

    @pytest.mark.asyncio
    async def test_format_is_ui_when_ui_json_present_else_api(self, tmp_path, monkeypatch):
        _write_preset(tmp_path, "SDXL", "ui-one", preset_id="P1", name="UI one", ui_format=True)
        _write_preset(tmp_path, "SDXL", "api-one", preset_id="P2", name="API one", ui_format=False)
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path)
        _container(monkeypatch)

        result = await api.list_imported_presets(current_user=None)
        by_id = {p["preset_id"]: p for p in result["presets"]}
        assert by_id["P1"]["format"] == "ui"
        assert by_id["P2"]["format"] == "api"

    @pytest.mark.asyncio
    async def test_requirements_summary_reused_from_the_presets_api(self, tmp_path, monkeypatch):
        _write_preset(tmp_path, "SDXL", "imported", preset_id="P1", name="My import")
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path)
        summary = {"ok": 5, "missing": 1, "unknown": 0, "optional_missing": 0}
        _container(monkeypatch, {"P1": summary})

        result = await api.list_imported_presets(current_user=None)
        assert result["presets"][0]["requirements_summary"] == summary

    @pytest.mark.asyncio
    async def test_requirements_summary_is_none_when_never_checked(self, tmp_path, monkeypatch):
        _write_preset(tmp_path, "SDXL", "imported", preset_id="P1", name="My import")
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path)
        _container(monkeypatch, {})  # no cached summary for P1

        result = await api.list_imported_presets(current_user=None)
        assert result["presets"][0]["requirements_summary"] is None

    @pytest.mark.asyncio
    async def test_a_directory_missing_preset_yml_or_description_is_skipped(self, tmp_path, monkeypatch):
        incomplete = tmp_path / "SDXL" / "broken"
        incomplete.mkdir(parents=True)
        (incomplete / "preset.yml").write_text(yaml.safe_dump({"id": "PBROKEN", "name": "Broken"}))
        # No description.md written at all.
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path)
        _container(monkeypatch)

        result = await api.list_imported_presets(current_user=None)
        assert result["presets"] == []

    @pytest.mark.asyncio
    async def test_sorted_newest_first(self, tmp_path, monkeypatch):
        import os
        import time

        older = _write_preset(tmp_path, "SDXL", "older", preset_id="OLD", name="Older")
        newer = _write_preset(tmp_path, "SDXL", "newer", preset_id="NEW", name="Newer")
        now = time.time()
        os.utime(older / "preset.yml", (now - 100, now - 100))
        os.utime(newer / "preset.yml", (now, now))
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path)
        _container(monkeypatch)

        result = await api.list_imported_presets(current_user=None)
        assert [p["preset_id"] for p in result["presets"]] == ["NEW", "OLD"]
