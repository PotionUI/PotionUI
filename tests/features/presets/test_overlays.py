from src.features.presets.overlays import (
    MIRROR_ID_COLLISION,
    MIRROR_MODES_MISSING,
    MIRROR_OK,
    MIRROR_SOURCE_MISSING,
    PresetOverlays,
    RowOverlay,
    apply_overlays,
)
from src.features.presets.templates import FormTemplate, ModeTemplate, PresetTemplate, files_preset_id


def _mode(source_plugin=None):
    return ModeTemplate(forms=[FormTemplate(name="default", fields=[])], pipes=[], source_plugin=source_plugin)


def _krea(**changes):
    values = dict(
        id="krea2",
        name="Krea-2",
        version="1",
        path="/presets/krea2",
        description="The Krea-2 model.",
        modes={"txt2img": _mode(), "img2img": _mode(), "edit": _mode()},
        media={"cover": "public/cover.webp", "gallery": [{"src": "public/one.webp"}]},
        requirements=[
            {"type": "binary", "name": "ffmpeg"},
            {"type": "model", "field": "model", "mode": "txt2img"},
            {"type": "model", "field": "model", "mode": "edit"},
        ],
        file_name="Krea-2",
    )
    values.update(changes)
    return PresetTemplate(**values)


def _overlays(*rows, tombstones=None):
    return PresetOverlays(rows={row.preset_id: row for row in rows}, tombstones=dict(tombstones or {}))


def _mirror(preset_id="M1", **changes):
    values = dict(preset_id=preset_id, source_preset_id="krea2", display_name="Anime")
    values.update(changes)
    return RowOverlay(**values)


def _by_id(result):
    return {template.id: template for template in result.presets}


def test_a_display_override_renames_a_preset_file_and_keeps_its_gallery():
    row = RowOverlay(preset_id="krea2", display_name="Realistic", display_cover="display/abc.webp")

    result = apply_overlays([_krea()], _overlays(row), {})

    shown = _by_id(result)["krea2"]
    assert shown.name == "Realistic"
    assert shown.description == "The Krea-2 model."
    assert shown.media == {"cover": "display/abc.webp", "gallery": [{"src": "public/one.webp"}]}
    assert shown.file_name == "Krea-2"


def test_a_preset_without_overrides_is_the_scanned_template():
    scanned = _krea()

    result = apply_overlays([scanned], _overlays(RowOverlay(preset_id="krea2")), {})

    assert result.presets == [scanned]
    assert result.presets[0] is scanned


def test_a_mirror_is_a_copy_of_its_source_files_under_its_own_id():
    source = _krea()

    result = apply_overlays([source], _overlays(_mirror(display_description="Anime covers")), {})

    mirror = _by_id(result)["M1"]
    assert result.mirror_states == {"M1": MIRROR_OK}
    assert mirror.name == "Anime"
    assert mirror.description == "Anime covers"
    assert mirror.path == source.path
    assert mirror.source_preset_id == "krea2"
    assert files_preset_id(mirror) == "krea2"
    assert mirror.modes["txt2img"] is source.modes["txt2img"]
    assert mirror.media == source.media
    assert source.id == "krea2" and source.name == "Krea-2"


def test_a_mirror_takes_its_source_file_values_not_the_source_display_overrides():
    renamed = RowOverlay(preset_id="krea2", display_name="Realistic", display_cover="display/src.webp")

    result = apply_overlays([_krea()], _overlays(renamed, _mirror()), {})

    mirror = _by_id(result)["M1"]
    assert mirror.media["cover"] == "public/cover.webp"
    assert mirror.description == "The Krea-2 model."


def test_offered_modes_filter_the_mirror_and_the_first_mode_opens_first():
    result = apply_overlays([_krea()], _overlays(_mirror(offered_modes=("edit", "txt2img"), first_mode="edit")), {})

    mirror = _by_id(result)["M1"]
    assert list(mirror.modes) == ["edit", "txt2img"]
    assert [r.get("mode") for r in mirror.requirements] == [None, "txt2img", "edit"]


def test_without_a_first_mode_the_offered_modes_keep_the_source_order():
    result = apply_overlays([_krea()], _overlays(_mirror(offered_modes=("edit", "txt2img"))), {})

    assert list(_by_id(result)["M1"].modes) == ["txt2img", "edit"]


def test_requirements_of_modes_a_mirror_does_not_offer_are_dropped():
    result = apply_overlays([_krea()], _overlays(_mirror(offered_modes=("img2img",))), {})

    assert _by_id(result)["M1"].requirements == [{"type": "binary", "name": "ffmpeg"}]


def test_a_mirror_whose_source_is_gone_is_hidden_and_keeps_its_name():
    result = apply_overlays([], _overlays(_mirror()), {})

    assert result.presets == []
    assert result.mirror_states == {"M1": MIRROR_SOURCE_MISSING}
    assert result.retired_names == {"M1": "Anime"}


def test_a_mirror_whose_offered_modes_all_vanished_is_hidden():
    result = apply_overlays([_krea()], _overlays(_mirror(offered_modes=("plugin_mode",))), {})

    assert "M1" not in _by_id(result)
    assert result.mirror_states == {"M1": MIRROR_MODES_MISSING}
    assert result.retired_names == {"M1": "Anime"}


def test_a_preset_file_that_claims_a_mirror_id_wins_and_the_mirror_is_reported():
    squatter = _krea(id="M1", name="Squatter")
    errors = {}

    result = apply_overlays([_krea(), squatter], _overlays(_mirror()), errors)

    assert _by_id(result)["M1"].name == "Squatter"
    assert result.mirror_states == {"M1": MIRROR_ID_COLLISION}
    assert list(errors) == ["mirror:M1"]


def test_a_plugin_mode_on_the_source_is_inherited_and_drops_out_when_the_plugin_goes():
    with_plugin = _krea(modes={"txt2img": _mode(), "relight": _mode(source_plugin="relight-plugin")})
    row = _mirror(offered_modes=("relight",))

    present = apply_overlays([with_plugin], _overlays(row), {})
    gone = apply_overlays([_krea()], _overlays(row), {})

    assert list(_by_id(present)["M1"].modes) == ["relight"]
    assert _by_id(present)["M1"].modes["relight"].source_plugin == "relight-plugin"
    assert gone.mirror_states == {"M1": MIRROR_MODES_MISSING}


def test_tombstones_name_deleted_mirrors():
    result = apply_overlays([_krea()], _overlays(tombstones={"OLD": "Film Covers"}), {})

    assert result.retired_names == {"OLD": "Film Covers"}


def test_a_mirror_of_a_mirror_never_appears():
    first = _mirror("M1")
    nested = RowOverlay(preset_id="M2", source_preset_id="M1", display_name="Nested")

    result = apply_overlays([_krea()], _overlays(first, nested), {})

    assert "M2" not in _by_id(result)
    assert result.mirror_states["M2"] == MIRROR_SOURCE_MISSING
