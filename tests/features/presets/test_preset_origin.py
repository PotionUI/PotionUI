from pathlib import Path
from types import SimpleNamespace

from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.loader import PresetTemplateLoader, plugin_preset_root_owners
from src.features.presets.templates import GenerationMode, ModeTemplate, PresetTemplate


def _write_preset(root: Path, rel: str, preset_id: str, modes=("txt2img",)) -> None:
    preset_dir = root / rel
    preset_dir.mkdir(parents=True)
    mode_lines = "".join(f"  - {m}\n" for m in modes)
    (preset_dir / "preset.yml").write_text(
        f'schema: 1\nid: "{preset_id}"\nname: "Same Name"\nversion: "1.0.0"\n'
        f'category: "image"\nengine: "native"\nmodes:\n{mode_lines}'
    )
    for mode in modes:
        (preset_dir / "modes" / mode).mkdir(parents=True)
        (preset_dir / "modes" / mode / "pipeline.yml").write_text("pipeline: []\n")


def _infos(loader):
    return {info["id"]: info for info in FilePresetRepository(loader).list_all_presets()}


def test_origin_kind_and_path_come_from_the_scanned_root(tmp_path):
    marketplace = tmp_path / "presets" / "marketplace"
    local = tmp_path / "presets" / "local"
    _write_preset(marketplace, "Krea2", "01SHIPPEDORIGINAAAAAAAAAA")
    _write_preset(local, "QwenImage/imported", "01LOCALORIGINAAAAAAAAAAA")

    infos = _infos(PresetTemplateLoader([str(marketplace), str(local)]))

    assert infos["01SHIPPEDORIGINAAAAAAAAAA"]["origin"] == {"kind": "marketplace", "plugin_id": None, "path": "Krea2"}
    assert infos["01LOCALORIGINAAAAAAAAAAA"]["origin"] == {
        "kind": "local",
        "plugin_id": None,
        "path": "QwenImage/imported",
    }


def test_plugin_root_presets_carry_the_owning_plugin_id(tmp_path):
    marketplace = tmp_path / "presets" / "marketplace"
    marketplace.mkdir(parents=True)
    plugin_dir = tmp_path / "plugins" / "comfy"
    _write_preset(plugin_dir / "presets", "Krea-2", "01PLUGINORIGINAAAAAAAAAAA")
    manifest = SimpleNamespace(id="comfy-backend", presets=[{"path": "presets"}], plugin_dir=plugin_dir)
    registry = SimpleNamespace(get_enabled_plugins=lambda: [manifest])

    infos = _infos(PresetTemplateLoader([str(marketplace)], plugin_registry=registry))

    assert infos["01PLUGINORIGINAAAAAAAAAAA"]["origin"] == {
        "kind": "plugin",
        "plugin_id": "comfy-backend",
        "path": "Krea-2",
    }


def test_root_owners_pair_each_root_with_its_plugin(tmp_path):
    manifest = SimpleNamespace(id="p1", presets=[{"path": "a"}, {"path": "b"}], plugin_dir=tmp_path)
    owners = plugin_preset_root_owners([manifest, SimpleNamespace(id="p2", presets=[], plugin_dir=tmp_path)])

    assert owners == [(tmp_path.resolve() / "a", "p1"), (tmp_path.resolve() / "b", "p1")]


def test_modes_list_every_mode_of_the_preset(tmp_path):
    marketplace = tmp_path / "presets" / "marketplace"
    _write_preset(marketplace, "Multi", "01MODESAAAAAAAAAAAAAAAAAAA", modes=("txt2img", "edit"))

    infos = _infos(PresetTemplateLoader([str(marketplace)]))

    assert sorted(infos["01MODESAAAAAAAAAAAAAAAAAAA"]["modes"]) == ["edit", "txt2img"]


def test_a_template_without_origin_reports_none():
    template = PresetTemplate(
        id="x",
        name="X",
        version="1.0.0",
        path="/p",
        modes={GenerationMode.TXT2IMG: ModeTemplate(forms=[], pipes=[])},
    )

    info = FilePresetRepository(PresetTemplateLoader([])).preset_to_info(template)

    assert info.origin is None
    assert info.modes == ["txt2img"]
