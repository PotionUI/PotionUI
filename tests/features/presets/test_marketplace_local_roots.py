"""The `content/presets/marketplace` (shipped) + `content/presets/local`
(.gitignored, user-owned) root split - mirrors `content/plugins/marketplace`
vs `content/plugins/local`. See
`src/bootstrap/container.py`'s `PresetTemplateLoader` construction and
`docs/presets.md` "Canonical layout".
"""

from pathlib import Path

from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.loader import PresetTemplateLoader


def _write_preset(root: Path, model: str, preset_id: str) -> Path:
    preset_dir = root / model / "std"
    preset_dir.mkdir(parents=True)
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{preset_id}"
name: "Test Preset"
version: "1.0.0"
category: "image"
engine: "native"
modes:
  - txt2img
"""
    )
    (preset_dir / "modes" / "txt2img").mkdir(parents=True)
    (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text("pipeline: []\n")
    return preset_dir


class TestMarketplaceLocalRootSplit:
    def test_local_root_content_is_discovered_alongside_marketplace(self, tmp_path):
        marketplace = tmp_path / "presets" / "marketplace"
        local = tmp_path / "presets" / "local"
        marketplace.mkdir(parents=True)
        local.mkdir(parents=True)
        _write_preset(marketplace, "Shipped", "01SHIPPEDAAAAAAAAAAAAAAAAAA")
        _write_preset(local, "Mine", "01LOCALAAAAAAAAAAAAAAAAAAAA")

        loader = PresetTemplateLoader([str(marketplace), str(local)])
        loader.load_presets()

        assert {p.id for p in loader.presets} == {
            "01SHIPPEDAAAAAAAAAAAAAAAAAA",
            "01LOCALAAAAAAAAAAAAAAAAAAAA",
        }

    def test_local_root_presets_are_labelled_custom_source(self, tmp_path):
        marketplace = tmp_path / "presets" / "marketplace"
        local = tmp_path / "presets" / "local"
        marketplace.mkdir(parents=True)
        local.mkdir(parents=True)
        _write_preset(marketplace, "Shipped", "01SHIPPEDBBBBBBBBBBBBBBBBBB")
        _write_preset(local, "Mine", "01LOCALBBBBBBBBBBBBBBBBBBBB")

        loader = PresetTemplateLoader([str(marketplace), str(local)])
        repo = FilePresetRepository(loader)

        by_id = {info["id"]: info for info in repo.list_all_presets()}
        assert by_id["01SHIPPEDBBBBBBBBBBBBBBBBBB"]["source"] == "official"
        assert by_id["01LOCALBBBBBBBBBBBBBBBBBBBB"]["source"] == "custom"

    def test_absent_local_root_does_not_crash(self, tmp_path):
        marketplace = tmp_path / "presets" / "marketplace"
        marketplace.mkdir(parents=True)
        _write_preset(marketplace, "Shipped", "01SHIPPEDCCCCCCCCCCCCCCCCCC")
        missing_local = tmp_path / "presets" / "local"
        assert not missing_local.exists()

        loader = PresetTemplateLoader([str(marketplace), str(missing_local)])
        loader.load_presets()

        assert {p.id for p in loader.presets} == {"01SHIPPEDCCCCCCCCCCCCCCCCCC"}

    def test_shared_path_does_not_depend_on_which_root_scans_first(self, tmp_path):
        # `paths._shared` resolves against the fixed core `content/presets/_shared`
        # tree regardless of root order - unlike `preset_files_path` (still
        # "first of preset_files_paths", kept for its other callers).
        local = tmp_path / "presets" / "local"
        marketplace = tmp_path / "presets" / "marketplace"
        loader = PresetTemplateLoader([str(local), str(marketplace)])
        assert str(loader.shared_path) == "content/presets/_shared"
