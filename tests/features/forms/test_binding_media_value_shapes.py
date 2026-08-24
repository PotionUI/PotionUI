"""String media values are validate-only, exactly like dict media refs.

Containment used to REWRITE a string value to its resolved absolute path.
Two things followed:

  * That absolute path is what lands in `generations.form_data` and what
    history reuse replays, pinning the storage root of the day. A dict media
    ref, checked by the same boundary, stayed relative - the two shapes
    behaved asymmetrically for no reason a caller could see.
  * Re-rooting is not even safe: an upload value is CWD-relative and already
    carries the storage prefix ('storage/uploads/x.png'), so joining it onto
    the storage root DOUBLE-PREFIXES it - and the result still passes the
    guard's own `relative_to` check, because it is inside the root twice over.

`media_loader` resolves both relative conventions (and leaves absolutes alone,
so rows already carrying one keep replaying), so the boundary has no reason to
rewrite anything.
"""

import os
from pathlib import Path

import pytest

from src.features.forms.binding import bind_form, FormBindingError
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _field(name, type_="image", configuration=None):
    return FieldTemplate(type=type_, name=name, configuration=configuration)


def _preset(fields):
    return PresetTemplate(
        id="preset_media_shapes",
        name="Media Shapes",
        version="1.0.0",
        path="/presets/preset_media_shapes",
        modes={"txt2img": ModeTemplate(
            forms=[FormTemplate(name="custom", fields=fields, default=True, order=0)],
            pipes=[],
        )},
    )


class TestStringMediaValuesAreValidateOnly:
    def test_storage_root_relative_string_is_not_made_absolute(self, tmp_path):
        storage = tmp_path / "storage"
        (storage / "generations" / "2026-08-12" / "gen").mkdir(parents=True)
        (storage / "generations" / "2026-08-12" / "gen" / "1.mp4").write_bytes(b"v")

        value = "generations/2026-08-12/gen/1.mp4"
        bound = bind_form(
            _preset([_field("source_video", "video")]), "txt2img", None,
            {"source_video": value}, "user_1", storage_dir=str(storage),
        )

        assert bound.values["source_video"] == value
        assert not os.path.isabs(bound.values["source_video"]), (
            "an absolute path here is persisted into generations.form_data and "
            "replayed by history reuse, pinning today's storage root"
        )

    def test_cwd_relative_upload_string_is_not_double_prefixed(self, tmp_path, monkeypatch):
        """The trap: 'storage/uploads/x.png' joined onto the storage root
        resolves INSIDE the root twice over, so the guard passes and hands back
        a path that does not exist."""
        monkeypatch.chdir(tmp_path)
        uploads = tmp_path / "storage" / "uploads"
        uploads.mkdir(parents=True)
        (uploads / "x.png").write_bytes(b"i")

        value = "storage/uploads/x.png"
        bound = bind_form(
            _preset([_field("source_image", "image")]), "txt2img", None,
            {"source_image": value}, "user_1", storage_dir="storage",
        )

        resolved = Path(bound.values["source_image"])
        if not resolved.is_absolute():
            resolved = Path.cwd() / resolved
        assert resolved.resolve().exists(), (
            f"bound value {bound.values['source_image']!r} resolves to "
            f"{resolved.resolve()}, which does not exist"
        )
        assert bound.values["source_image"] == value

    def test_string_and_dict_shapes_agree(self, tmp_path):
        """Same path, two wire shapes, same treatment: unmodified."""
        storage = tmp_path / "storage"
        storage.mkdir()
        rel = "uploads/x.png"

        preset = _preset([_field("a", "image"), _field("b", "image")])
        bound = bind_form(
            preset, "txt2img", None,
            {"a": rel, "b": {"path": rel, "relative_path": rel, "type": "image"}},
            "user_1", storage_dir=str(storage),
        )

        assert bound.values["a"] == rel
        assert bound.values["b"]["path"] == rel

    def test_multi_item_string_values_stay_relative(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir()
        values = ["uploads/a.png", "uploads/b.png"]

        bound = bind_form(
            _preset([_field("refs", "image", configuration={"multi": True})]),
            "txt2img", None, {"refs": values}, "user_1", storage_dir=str(storage),
        )

        assert bound.values["refs"] == values

    def test_absolute_string_inside_the_root_is_still_accepted_unchanged(self, tmp_path):
        """Rows persisted before this change carry absolute paths; replaying one
        must not start failing binding."""
        storage = tmp_path / "storage"
        (storage / "uploads").mkdir(parents=True)
        absolute = str(storage / "uploads" / "x.png")

        bound = bind_form(
            _preset([_field("source_image", "image")]), "txt2img", None,
            {"source_image": absolute}, "user_1", storage_dir=str(storage),
        )

        assert bound.values["source_image"] == absolute


class TestStringMediaValuesAreStillContained:
    def test_traversal_is_still_rejected(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir()

        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                _preset([_field("source_image", "image")]), "txt2img", None,
                {"source_image": "../outside.png"}, "user_1", storage_dir=str(storage),
            )
        assert any(
            "escapes the user's storage directory" in m
            for m in excinfo.value.field_errors["source_image"]
        )

    def test_absolute_path_outside_the_root_is_still_rejected(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir()

        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                _preset([_field("source_image", "image")]), "txt2img", None,
                {"source_image": "/etc/passwd"}, "user_1", storage_dir=str(storage),
            )
        assert any(
            "escapes the user's storage directory" in m
            for m in excinfo.value.field_errors["source_image"]
        )

    def test_multi_item_traversal_is_still_rejected(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir()

        with pytest.raises(FormBindingError) as excinfo:
            bind_form(
                _preset([_field("refs", "image", configuration={"multi": True})]),
                "txt2img", None, {"refs": ["uploads/a.png", "../../etc/passwd"]},
                "user_1", storage_dir=str(storage),
            )
        assert any(
            "escapes the user's storage directory" in m
            for m in excinfo.value.field_errors["refs"]
        )
