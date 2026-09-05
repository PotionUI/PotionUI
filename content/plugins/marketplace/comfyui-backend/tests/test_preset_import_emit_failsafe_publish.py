"""`emit_preset`'s failure-safe publication: a serialization, write, or
rename failure while writing the replacement preset must never destroy a
previously-working preset (an overwrite) and must never leave a partial
directory a later catalogue scan (`rglob("preset.yml")`,
`src/features/presets/loader.py`) could discover (a first import). See
`_publish_preset_dir` in `backend/preset_import/emit.py` for the stage/swap/
rollback sequence this exercises.
"""

import json
import os
from pathlib import Path

import pytest

from backend import api
from backend.preset_import import emit
from backend.preset_import.emit import PresetEmitError, emit_preset
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.suggest import suggest_fields

from ._form_helpers import form_from_roles

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _checkpoint_form():
    workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
    analysis = suggest_fields(workflow)
    return workflow, form_from_roles(analysis, {"checkpoint"})


def _emit(dest_root, *, model_family, variant="v1", **kwargs):
    workflow, form = _checkpoint_form()
    return emit_preset(
        workflow, form, [], model_family=model_family, variant=variant,
        display_name="X", dest_root=dest_root, **kwargs
    )


def _snapshot(directory: Path) -> dict:
    """(relative path -> bytes) for every file under `directory` - a
    before/after content comparison that also catches a stray leftover or
    missing file, not just changed bytes."""
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _fail_on_call(original, call_number, message="injected failure"):
    """Wraps `original` so the `call_number`-th call raises instead of
    running - every earlier and later call passes through unchanged. Used to
    monkeypatch one of `emit`'s three publication seams (`_dump_yaml`/
    `_write_file`/`_replace_dir`) at an exact point in the sequence."""
    state = {"n": 0}

    def wrapper(*args, **kwargs):
        state["n"] += 1
        if state["n"] == call_number:
            raise RuntimeError(message)
        return original(*args, **kwargs)

    return wrapper


class TestFailedOverwritePreservesThePreviousPreset:
    """A re-emit (reload/modify) that fails partway through must leave the
    existing preset directory exactly as it was - same files, same bytes -
    and no stray staging/backup directory left for the catalogue to trip
    over."""

    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    @pytest.mark.parametrize(
        "seam, fail_at_call",
        [
            ("_dump_yaml", 2),  # serialization: form.yml, mid-sequence
            ("_write_file", 3),  # a middle staged file write: import.json
            ("_replace_dir", 2),  # publication: staging -> target, after target -> backup
        ],
    )
    def test_overwrite_failure_leaves_the_original_byte_for_byte(self, dest_root, monkeypatch, seam, fail_at_call):
        first = _emit(dest_root, model_family="Preserve")
        before = _snapshot(first.preset_dir)

        monkeypatch.setattr(emit, seam, _fail_on_call(getattr(emit, seam), fail_at_call))

        workflow, form = _checkpoint_form()
        with pytest.raises(PresetEmitError):
            emit_preset(
                workflow, form, [], model_family="Preserve", variant="v1",
                display_name="X renamed", dest_root=dest_root,
                overwrite=True, preset_id=first.preset_id,
            )

        assert _snapshot(first.preset_dir) == before
        # Nothing else sits alongside the restored preset directory - no
        # `.staging-*`/`.backup-*` leftover a scan could mistake for another
        # imported preset.
        assert list(first.preset_dir.parent.iterdir()) == [first.preset_dir]

    def test_retry_after_a_failed_overwrite_succeeds(self, dest_root, monkeypatch):
        first = _emit(dest_root, model_family="RetryOverwrite")
        original_write = emit._write_file
        monkeypatch.setattr(emit, "_write_file", _fail_on_call(original_write, 3))

        workflow, form = _checkpoint_form()
        with pytest.raises(PresetEmitError):
            emit_preset(
                workflow, form, [], model_family="RetryOverwrite", variant="v1",
                display_name="X renamed", dest_root=dest_root,
                overwrite=True, preset_id=first.preset_id,
            )

        monkeypatch.setattr(emit, "_write_file", original_write)

        workflow2, form2 = _checkpoint_form()
        result = emit_preset(
            workflow2, form2, [], model_family="RetryOverwrite", variant="v1",
            display_name="X renamed again", dest_root=dest_root,
            overwrite=True, preset_id=first.preset_id,
        )
        assert result.preset_id == first.preset_id
        preset_yml = (result.preset_dir / "preset.yml").read_text()
        assert "X renamed again" in preset_yml
        assert list(first.preset_dir.parent.iterdir()) == [first.preset_dir]

    def test_restore_failure_preserves_the_backup_and_names_it_in_the_error(self, dest_root, monkeypatch):
        """A pathological double failure (the publish rename AND the
        rollback rename both fail) must not delete the only intact copy of
        the previous preset - it is left on disk under a named backup path,
        and the raised error names that path."""
        first = _emit(dest_root, model_family="DoubleFail")
        before = _snapshot(first.preset_dir)

        calls = {"n": 0}

        def failing_replace(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                os.replace(src, dst)  # the real target -> backup rename succeeds
                return
            raise RuntimeError(f"injected failure #{calls['n']}")

        monkeypatch.setattr(emit, "_replace_dir", failing_replace)

        workflow, form = _checkpoint_form()
        with pytest.raises(PresetEmitError, match="restore"):
            emit_preset(
                workflow, form, [], model_family="DoubleFail", variant="v1",
                display_name="X renamed", dest_root=dest_root,
                overwrite=True, preset_id=first.preset_id,
            )

        assert not first.preset_dir.exists()
        backups = list(first.preset_dir.parent.glob(".backup-*"))
        assert len(backups) == 1
        assert _snapshot(backups[0]) == before


class TestFailedFirstImportLeavesNoPartialPreset:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    @pytest.mark.parametrize(
        "seam, fail_at_call",
        [
            ("_dump_yaml", 2),
            ("_write_file", 3),
            ("_replace_dir", 1),  # only one rename happens: staging -> target
        ],
    )
    def test_no_preset_yml_is_discoverable_after_a_failed_first_import(self, dest_root, monkeypatch, seam, fail_at_call):
        monkeypatch.setattr(emit, seam, _fail_on_call(getattr(emit, seam), fail_at_call))

        workflow, form = _checkpoint_form()
        with pytest.raises(PresetEmitError):
            emit_preset(
                workflow, form, [], model_family="FreshFail", variant="v1",
                display_name="X", dest_root=dest_root,
            )

        assert not list(dest_root.rglob("preset.yml"))
        family_dir = dest_root / "FreshFail"
        assert not family_dir.exists() or list(family_dir.iterdir()) == []

    def test_retry_after_a_failed_first_import_succeeds(self, dest_root, monkeypatch):
        original_write = emit._write_file
        monkeypatch.setattr(emit, "_write_file", _fail_on_call(original_write, 3))

        workflow, form = _checkpoint_form()
        with pytest.raises(PresetEmitError):
            emit_preset(
                workflow, form, [], model_family="RetryFresh", variant="v1",
                display_name="X", dest_root=dest_root,
            )

        monkeypatch.setattr(emit, "_write_file", original_write)

        workflow2, form2 = _checkpoint_form()
        result = emit_preset(
            workflow2, form2, [], model_family="RetryFresh", variant="v1",
            display_name="X", dest_root=dest_root,
        )
        assert (result.preset_dir / "preset.yml").exists()
        assert list(result.preset_dir.parent.iterdir()) == [result.preset_dir]


class _DummyLoader:
    def reload(self):
        pass


class _DummyContainer:
    preset_template_loader = _DummyLoader()


class TestApiSurfacesPublicationFailuresCleanly:
    """The real entry points (`api.import_workflow`, `api.reload_imported_preset`)
    joined through one failure fixture - a publication failure must come
    back as the same 400 `PresetEmitError` always maps to, must never reach
    the catalogue-reload call, and (for reload/modify) must leave the
    existing preset discoverable and readable afterward."""

    @pytest.fixture()
    def imported_root(self, tmp_path, monkeypatch):
        root = tmp_path / "presets"
        reload_calls = []
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", root)
        monkeypatch.setattr(api, "get_container", lambda: (reload_calls.append(1), _DummyContainer())[1])
        return root, reload_calls

    def _checkpoint_form_dict(self):
        _, form = _checkpoint_form()
        return form.model_dump(mode="json")

    @pytest.mark.asyncio
    async def test_import_workflow_returns_400_and_never_reloads_on_publish_failure(self, imported_root, monkeypatch):
        root, reload_calls = imported_root
        monkeypatch.setattr(emit, "_replace_dir", _fail_on_call(emit._replace_dir, 1))

        body = api.ImportWorkflowRequest(
            workflow=_load("sdxl_basic_api.json"),
            form=self._checkpoint_form_dict(),
            model_family="ApiFreshFail",
            variant="v1",
            display_name="X",
        )

        with pytest.raises(Exception) as exc_info:
            await api.import_workflow(body, current_user=None)

        assert exc_info.value.status_code == 400
        assert reload_calls == []
        assert not list(root.rglob("preset.yml"))

    @pytest.mark.asyncio
    async def test_reload_imported_preset_returns_400_never_reloads_and_keeps_the_existing_preset(self, imported_root, monkeypatch):
        root, reload_calls = imported_root
        result = _emit(root, model_family="ApiReloadFail")
        before = _snapshot(result.preset_dir)

        monkeypatch.setattr(emit, "_replace_dir", _fail_on_call(emit._replace_dir, 2))

        with pytest.raises(Exception) as exc_info:
            await api.reload_imported_preset(result.preset_id, current_user=None)

        assert exc_info.value.status_code == 400
        assert reload_calls == []
        assert _snapshot(result.preset_dir) == before
