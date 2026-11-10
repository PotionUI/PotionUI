from pathlib import Path

import pytest

from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.name_resolver import PresetNameResolver
from src.features.presets.overlays import MIRROR_OK, MIRROR_SOURCE_MISSING, PresetOverlays, RowOverlay


def _write_preset(root: Path, preset_id: str, name: str, modes=("txt2img", "img2img")) -> Path:
    preset_dir = root / name / "std"
    preset_dir.mkdir(parents=True)
    mode_list = "\n".join(f"  - {mode}" for mode in modes)
    (preset_dir / "preset.yml").write_text(
        f'schema: 1\nid: "{preset_id}"\nname: "{name}"\nversion: "1.0.0"\ncategory: "image"\n'
        f'engine: "native"\nmodes:\n{mode_list}\n'
    )
    for mode in modes:
        (preset_dir / "modes" / mode).mkdir(parents=True)
        (preset_dir / "modes" / mode / "pipeline.yml").write_text("pipeline: []\n")
    return preset_dir


class Overlays:
    def __init__(self):
        self.rows = {}
        self.tombstones = {}
        self.reads = 0
        self.broken = False

    def __call__(self):
        self.reads += 1
        if self.broken:
            raise RuntimeError("database is locked")
        return PresetOverlays(rows=dict(self.rows), tombstones=dict(self.tombstones))

    def add(self, row: RowOverlay):
        self.rows[row.preset_id] = row


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "presets"
    root.mkdir()
    _write_preset(root, "krea2", "Krea-2")
    overlays = Overlays()
    loader = PresetTemplateLoader([str(root)], overlay_source=overlays)
    scans = []
    original = loader._scan_presets

    def counted():
        scans.append(1)
        return original()

    loader._scan_presets = counted
    return loader, overlays, scans, root


def test_a_rename_reaches_every_reader_after_a_refresh_without_rescanning(world):
    loader, overlays, scans, _ = world
    assert loader.get("krea2").name == "Krea-2"

    overlays.add(RowOverlay(preset_id="krea2", display_name="Realistic"))
    loader.refresh_overlays()

    assert loader.get("krea2").name == "Realistic"
    assert [p.name for p in loader.all()] == ["Realistic"]
    assert loader.file_template("krea2").name == "Krea-2"
    assert len(scans) == 1


def test_a_new_mirror_appears_under_its_own_id_and_points_at_its_source_files(world):
    loader, overlays, _, _ = world
    loader.all()

    overlays.add(RowOverlay(preset_id="MIRROR", source_preset_id="krea2", display_name="Anime", offered_modes=("img2img",)))
    loader.refresh_overlays()

    mirror = loader.get("MIRROR")
    assert mirror.name == "Anime"
    assert list(mirror.modes) == ["img2img"]
    assert mirror.path == loader.get("krea2").path
    assert loader.files_preset_id("MIRROR") == "krea2"
    assert loader.files_preset_id("krea2") == "krea2"
    assert loader.mirror_state("MIRROR") == MIRROR_OK


def test_a_reload_rescans_the_files_and_keeps_the_overlays(world):
    loader, overlays, scans, _ = world
    overlays.add(RowOverlay(preset_id="krea2", display_name="Realistic"))
    overlays.add(RowOverlay(preset_id="MIRROR", source_preset_id="krea2", display_name="Anime"))
    loader.all()

    loader.reload()

    assert len(scans) == 2
    assert loader.get("krea2").name == "Realistic"
    assert loader.get("MIRROR").name == "Anime"


def test_a_failing_overlay_source_still_loads_the_preset_files(world):
    loader, overlays, _, _ = world
    overlays.broken = True

    assert [p.id for p in loader.all()] == ["krea2"]
    assert loader.get("krea2").name == "Krea-2"


def test_clearing_the_cache_rescans_on_the_next_read(world):
    loader, overlays, scans, _ = world
    loader.all()

    loader.clear_cache()
    loader.refresh_overlays()

    assert len(scans) == 2
    assert loader.get("krea2") is not None


def test_a_mirror_whose_source_files_vanish_is_hidden_but_still_resolves_its_files_and_name(world):
    loader, overlays, _, root = world
    overlays.add(RowOverlay(preset_id="MIRROR", source_preset_id="krea2", display_name="Anime"))
    loader.all()
    (root / "Krea-2" / "std" / "preset.yml").unlink()

    loader.reload()

    assert loader.get("MIRROR") is None
    assert loader.mirror_state("MIRROR") == MIRROR_SOURCE_MISSING
    assert loader.files_preset_id("MIRROR") == "krea2"
    assert loader.files_preset_id("unknown") == "unknown"
    assert PresetNameResolver(loader).resolve("MIRROR") == "Anime"


def test_names_of_deleted_mirrors_resolve_from_their_tombstones(world):
    loader, overlays, _, _ = world
    overlays.tombstones["GONE"] = "Film Covers"
    loader.refresh_overlays()

    resolver = PresetNameResolver(loader)

    assert resolver.resolve("GONE") == "Film Covers"
    assert resolver.name_map()["GONE"] == "Film Covers"
    assert resolver.resolve("NEVER") == "NEVER"


def test_a_refresh_notifies_the_media_prerender(world):
    loader, overlays, _, _ = world
    loader.all()
    calls = []
    loader.on_presets_changed = calls.append

    overlays.add(RowOverlay(preset_id="MIRROR", source_preset_id="krea2", display_name="Anime"))
    loader.refresh_overlays()

    assert len(calls) == 1
    assert {p.id for p in calls[0]} == {"krea2", "MIRROR"}


def test_a_loader_without_an_overlay_source_shows_the_files_as_they_are(tmp_path):
    root = tmp_path / "presets"
    root.mkdir()
    _write_preset(root, "krea2", "Krea-2")

    loader = PresetTemplateLoader([str(root)])

    assert loader.get("krea2").name == "Krea-2"
    assert loader.get("krea2").file_name == "Krea-2"
    assert loader.mirror_states == {}


def test_a_failed_overlay_read_keeps_the_mirrors_and_renames_already_shown(world):
    loader, overlays, _, _ = world
    overlays.add(RowOverlay(preset_id="krea2", display_name="Realistic"))
    overlays.add(RowOverlay(preset_id="MIRROR", source_preset_id="krea2", display_name="Anime"))
    loader.all()

    overlays.broken = True
    loader.refresh_overlays()
    loader.reload()

    assert overlays.reads == 3
    assert loader.get("krea2").name == "Realistic"
    assert loader.get("MIRROR").name == "Anime"
    assert loader.mirror_state("MIRROR") == MIRROR_OK
