import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest
from unittest.mock import patch

from src.features.models.indexer import ModelScanner
from src.features.models.repository import model_repo
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import ModelRoot, ModelRootResolver, TypeDir, _Snapshot, root_path_key
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY

_POSITION_OFFSET = 1000


class FakeProbe:
    def __init__(self, states: Optional[Dict[str, Tuple[str, Optional[str]]]] = None):
        self._states = states if states is not None else {}

    def state(self, root: ModelRoot) -> Tuple[str, Optional[str]]:
        return self._states.get(root.id, ("online", None))


def _resolver(roots: List[ModelRoot], bindings: List[TypeDir], states: Optional[dict] = None) -> ModelRootResolver:
    resolver = ModelRootResolver(repository=None, probe=FakeProbe(states), base_dir=Path.cwd())
    roots_by_id = {r.id: r for r in roots}
    by_type: Dict[str, List[TypeDir]] = {}
    for binding in bindings:
        by_type.setdefault(binding.model_type, []).append(binding)
    type_dirs_by_type = {
        model_type: tuple(sorted(entries, key=lambda e: e.position))
        for model_type, entries in by_type.items()
    }
    resolver._snapshot = _Snapshot(roots=tuple(roots), roots_by_id=roots_by_id, type_dirs_by_type=type_dirs_by_type)
    return resolver


def _root(root_id: str, path, *, case_insensitive: bool = False, state: str = "online") -> ModelRoot:
    assert root_id != "home", "use a different id: migration 035 already seeds 'home'"
    now = now_iso()
    key = root_path_key(str(path), case_insensitive=case_insensitive)
    ModelRootRepository().insert_root(root_id, root_id, str(path), key, "library", False, case_insensitive, now)
    return ModelRoot(
        id=root_id, label=root_id, path=Path(path), kind="library", read_only=False,
        case_insensitive=case_insensitive, state=state, state_reason=None, raw_path=str(path),
    )


def _binding(root_id: str, model_type: str, path, position: int, *, is_write: bool = False, subdir: Optional[str] = None) -> TypeDir:
    if subdir is None:
        subdir = MODEL_TYPE_TO_DIRECTORY[model_type]
    real_position = position + _POSITION_OFFSET
    ModelRootRepository().insert_binding(root_id, model_type, subdir, real_position, is_write)
    return TypeDir(root_id=root_id, model_type=model_type, path=Path(path), position=real_position, is_write=is_write, subdir=subdir)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _is_case_insensitive_fs(tmp_path: Path) -> bool:
    probe = tmp_path / "case_probe.tmp"
    probe.write_bytes(b"x")
    try:
        return (tmp_path / "CASE_PROBE.tmp").exists()
    finally:
        probe.unlink()


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _models() -> list:
    return model_repo.get_all(include_providers=False, include_tags=False)


def test_two_roots_with_a_same_bytes_copy_makes_one_model_two_locations(tmp_path, mock_db):
    content = b"identical weights"
    home = tmp_path / "home"
    lib = tmp_path / "lib"
    _write(home / "loras" / "x.safetensors", content)
    _write(lib / "loras" / "x.safetensors", content)

    resolver = _resolver(
        [_root("r_home", home), _root("r_lib", lib)],
        [_binding("r_home", "lora", home / "loras", 0), _binding("r_lib", "lora", lib / "loras", 1)],
    )
    scanner = ModelScanner(resolver)

    result = scanner.index_models(max_workers=1)

    assert result["indexed"] == 2
    models = _models()
    assert len(models) == 1
    model = models[0]
    locations = scanner.locations.list_for_model(model.id)
    assert len(locations) == 2
    assert {loc["root_id"] for loc in locations} == {"r_home", "r_lib"}
    assert all(loc["status"] == "present" for loc in locations)
    assert model.is_available is True


def test_same_name_different_bytes_across_roots_is_flagged_conflict(tmp_path, mock_db):
    home = tmp_path / "home"
    lib = tmp_path / "lib"
    _write(home / "loras" / "x.safetensors", b"AAAA")
    _write(lib / "loras" / "x.safetensors", b"BBBB")

    resolver = _resolver(
        [_root("r_home", home), _root("r_lib", lib)],
        [_binding("r_home", "lora", home / "loras", 0), _binding("r_lib", "lora", lib / "loras", 1)],
    )
    scanner = ModelScanner(resolver)

    scanner.index_models(max_workers=1)

    models = _models()
    assert len(models) == 1
    locations = scanner.locations.list_for_model(models[0].id)
    assert len(locations) == 2
    assert sorted(loc["status"] for loc in locations) == ["conflict", "present"]


def test_same_bytes_renamed_file_updates_the_existing_model(tmp_path, mock_db):
    content = b"same-bytes"
    home = tmp_path / "home"
    original = home / "loras" / "a.safetensors"
    _write(original, content)
    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "lora", home / "loras", 0)])
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)
    model_before = _models()[0]

    original.unlink()
    renamed = home / "loras" / "b.safetensors"
    _write(renamed, content)

    scanner.index_models(max_workers=1)

    models = _models()
    assert len(models) == 1
    assert models[0].id == model_before.id
    assert models[0].filename == "b.safetensors"
    present = [loc for loc in scanner.locations.list_for_model(models[0].id) if loc["status"] == "present"]
    assert len(present) == 1
    assert present[0]["rel_path"] == "b.safetensors"


def test_same_bytes_different_name_with_the_old_copy_still_present_is_a_duplicate(tmp_path, mock_db):
    content = b"same-bytes"
    home = tmp_path / "home"
    _write(home / "loras" / "a.safetensors", content)
    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "lora", home / "loras", 0)])
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)

    _write(home / "loras" / "b.safetensors", content)

    result = scanner.index_models(max_workers=1)

    assert result["indexed"] == 0
    models = _models()
    assert len(models) == 1
    assert models[0].filename == "a.safetensors"
    assert len(scanner.locations.list_for_model(models[0].id)) == 1


def test_a_file_whose_mtime_changed_is_rehashed_in_place_not_treated_as_a_conflict(tmp_path, mock_db):
    home = tmp_path / "home"
    path = home / "checkpoints" / "m.safetensors"
    _write(path, b"v1")
    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "checkpoint", home / "checkpoints", 0)])
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)
    model_before = _models()[0]

    new_content = b"v2-longer-content"
    path.write_bytes(new_content)
    new_mtime = path.stat().st_mtime_ns + 2_000_000_000
    os.utime(path, ns=(new_mtime, new_mtime))

    scanner.index_models(max_workers=1)

    models = _models()
    assert len(models) == 1
    assert models[0].id == model_before.id
    assert models[0].sha256 == _sha(new_content)
    locations = scanner.locations.list_for_model(models[0].id)
    assert len(locations) == 1
    assert locations[0]["status"] == "present"


def test_offline_root_is_skipped_and_its_locations_are_never_pruned(tmp_path, mock_db):
    home = tmp_path / "home"
    usb = tmp_path / "usb"
    _write(home / "checkpoints" / "keep.safetensors", b"K")
    _write(usb / "checkpoints" / "on_usb.safetensors", b"U")

    states: Dict[str, Tuple[str, Optional[str]]] = {}
    resolver = _resolver(
        [_root("r_home", home), _root("r_usb", usb)],
        [_binding("r_home", "checkpoint", home / "checkpoints", 0),
         _binding("r_usb", "checkpoint", usb / "checkpoints", 1)],
        states=states,
    )
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)

    usb_location = scanner.locations.list_for_root_type("r_usb", "checkpoint")[0]
    assert usb_location["status"] == "present"

    states["r_usb"] = ("offline", "unplugged")
    scanner.index_models(max_workers=1)

    usb_location_after = scanner.locations.get("r_usb", "checkpoint", usb_location["rel_key"])
    assert usb_location_after is not None
    assert usb_location_after["status"] == "present"
    assert any(m.filename == "keep.safetensors" for m in _models())


def test_case_insensitive_root_folds_the_rel_key(tmp_path, mock_db):
    home = tmp_path / "home"
    _write(home / "loras" / "Style.safetensors", b"x")
    resolver = _resolver(
        [_root("r_home", home, case_insensitive=True)],
        [_binding("r_home", "lora", home / "loras", 0)],
    )
    scanner = ModelScanner(resolver)

    scanner.index_models(max_workers=1)

    loc = scanner.locations.get("r_home", "lora", "style.safetensors")
    assert loc is not None
    assert loc["rel_path"] == "Style.safetensors"


def test_case_sensitive_root_keeps_two_locations_for_differently_cased_names(tmp_path, mock_db):
    if _is_case_insensitive_fs(tmp_path):
        pytest.skip("filesystem is case-insensitive; A.safetensors and a.safetensors collide")
    home = tmp_path / "home"
    _write(home / "loras" / "A.safetensors", b"upper")
    _write(home / "loras" / "a.safetensors", b"lower")
    resolver = _resolver(
        [_root("r_home", home, case_insensitive=False)],
        [_binding("r_home", "lora", home / "loras", 0)],
    )
    scanner = ModelScanner(resolver)

    scanner.index_models(max_workers=1)

    assert scanner.locations.get("r_home", "lora", "A.safetensors") is not None
    assert scanner.locations.get("r_home", "lora", "a.safetensors") is not None


def test_nested_rel_path_is_stored_posix(tmp_path, mock_db):
    home = tmp_path / "home"
    _write(home / "loras" / "characters" / "sub" / "x.safetensors", b"n")
    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "lora", home / "loras", 0)])
    scanner = ModelScanner(resolver)

    scanner.index_models(max_workers=1)

    model = _models()[0]
    location = scanner.locations.list_for_model(model.id)[0]
    assert location["rel_path"] == "characters/sub/x.safetensors"
    assert "\\" not in location["rel_path"]


def _can_create_symlinks() -> bool:
    probe_dir = tempfile.mkdtemp()
    try:
        target = Path(probe_dir) / "target"
        target.mkdir()
        os.symlink(target, Path(probe_dir) / "link", target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


SYMLINKS_SUPPORTED = _can_create_symlinks()


@pytest.mark.skipif(not SYMLINKS_SUPPORTED, reason="Creating symlinks is not permitted in this environment")
def test_symlink_cycle_terminates_and_does_not_duplicate_the_real_file(tmp_path, mock_db):
    home = tmp_path / "home"
    loras = home / "loras"
    loras.mkdir(parents=True)
    (loras / "real.safetensors").write_bytes(b"content")
    os.symlink(loras, loras / "loop", target_is_directory=True)

    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "lora", loras, 0)])
    scanner = ModelScanner(resolver)

    found = scanner.scan_roots()

    assert len(found) == 1
    assert found[0].rel_path == "real.safetensors"


@pytest.mark.skipif(not SYMLINKS_SUPPORTED, reason="Creating symlinks is not permitted in this environment")
def test_symlink_into_another_root_is_indexed_once(tmp_path, mock_db):
    home = tmp_path / "home"
    lib = tmp_path / "lib"
    lib_loras = lib / "loras"
    lib_loras.mkdir(parents=True)
    (lib_loras / "shared.safetensors").write_bytes(b"shared")
    home_loras = home / "loras"
    home_loras.mkdir(parents=True)
    os.symlink(lib_loras / "shared.safetensors", home_loras / "shared.safetensors")

    resolver = _resolver(
        [_root("r_home", home), _root("r_lib", lib)],
        [_binding("r_home", "lora", home_loras, 0), _binding("r_lib", "lora", lib_loras, 1)],
    )
    scanner = ModelScanner(resolver)

    found = scanner.scan_roots()

    assert len(found) == 1
    assert found[0].root_id == "r_home"


def _copy_scanner(tmp_path):
    content = b"same-bytes"
    home = tmp_path / "home"
    _write(home / "checkpoints" / "3361846.safetensors", content)
    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "checkpoint", home / "checkpoints", 0)])
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)
    _write(home / "checkpoints" / "copy of 3361846.safetensors", content)
    return scanner, home, content


def test_a_same_bytes_copy_with_the_original_kept_is_reported_as_a_skipped_duplicate(tmp_path, mock_db):
    scanner, home, _content = _copy_scanner(tmp_path)

    result = scanner.index_models(max_workers=1)

    assert result["failed"] == 0
    assert result["failed_files"] == []
    assert result["failed_by_root"] == {}
    assert len(result["skipped_duplicates"]) == 1
    entry = result["skipped_duplicates"][0]
    assert entry["path"] == (home / "checkpoints" / "copy of 3361846.safetensors").as_posix()
    assert entry["root"] == "r_home"
    assert entry["same_as"] == {
        "model_id": _models()[0].id,
        "model_type": "checkpoint",
        "root": "r_home",
        "path": (home / "checkpoints" / "3361846.safetensors").as_posix(),
    }
    assert len(_models()) == 1


def test_a_known_duplicate_is_not_rehashed_on_the_next_scan_but_is_still_reported(tmp_path, mock_db):
    scanner, _home, _content = _copy_scanner(tmp_path)
    scanner.index_models(max_workers=1)

    with patch.object(scanner, "calculate_sha256", side_effect=AssertionError("must not rehash")):
        result = scanner.index_models(max_workers=1)

    assert result["failed"] == 0
    assert len(result["skipped_duplicates"]) == 1


def test_a_file_moved_to_another_type_folder_retypes_the_existing_model(tmp_path, mock_db):
    content = b"moved-bytes"
    home = tmp_path / "home"
    original = home / "loras" / "a.safetensors"
    _write(original, content)
    resolver = _resolver(
        [_root("r_home", home)],
        [
            _binding("r_home", "lora", home / "loras", 0),
            _binding("r_home", "checkpoint", home / "checkpoints", 0),
        ],
    )
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)
    model_before = _models()[0]

    original.unlink()
    _write(home / "checkpoints" / "a.safetensors", content)
    result = scanner.index_models(max_workers=1)

    assert result["failed"] == 0
    assert result["skipped_duplicates"] == []
    models = _models()
    assert len(models) == 1
    assert models[0].id == model_before.id
    assert models[0].model_type == "checkpoint"
    present = [loc for loc in scanner.locations.list_for_model(models[0].id) if loc["status"] == "present"]
    assert [(loc["model_type"], loc["rel_path"]) for loc in present] == [("checkpoint", "a.safetensors")]


def test_a_file_moved_into_a_subfolder_of_the_same_type_keeps_the_model(tmp_path, mock_db):
    content = b"nested-bytes"
    home = tmp_path / "home"
    original = home / "loras" / "a.safetensors"
    _write(original, content)
    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "lora", home / "loras", 0)])
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)
    model_before = _models()[0]

    original.unlink()
    _write(home / "loras" / "sub" / "a.safetensors", content)
    result = scanner.index_models(max_workers=1)

    assert result["failed"] == 0
    assert result["skipped_duplicates"] == []
    models = _models()
    assert len(models) == 1
    assert models[0].id == model_before.id
    present = [loc for loc in scanner.locations.list_for_model(models[0].id) if loc["status"] == "present"]
    assert [loc["rel_path"] for loc in present] == ["sub/a.safetensors"]


def test_a_new_file_in_a_library_root_is_indexed_under_that_root(tmp_path, mock_db):
    home = tmp_path / "home"
    lib = tmp_path / "lib"
    _write(lib / "checkpoints" / "fresh.safetensors", b"fresh-bytes")
    resolver = _resolver(
        [_root("r_home", home), _root("r_lib", lib)],
        [
            _binding("r_home", "checkpoint", home / "checkpoints", 0),
            _binding("r_lib", "checkpoint", lib / "checkpoints", 1),
        ],
    )
    scanner = ModelScanner(resolver)

    result = scanner.index_models(max_workers=1)

    assert result["indexed"] == 1
    assert result["failed"] == 0
    models = _models()
    assert [m.filename for m in models] == ["fresh.safetensors"]
    locations = scanner.locations.list_for_model(models[0].id)
    assert [(loc["root_id"], loc["status"]) for loc in locations] == [("r_lib", "present")]


def _library_original_scanner(tmp_path):
    content = b"library-bytes"
    home = tmp_path / "home"
    lib = tmp_path / "lib"
    original = lib / "Stable-diffusion" / "nested" / "a.safetensors"
    _write(original, content)
    resolver = _resolver(
        [_root("r_home", home), _root("r_lib", lib)],
        [
            _binding("r_home", "checkpoint", home / "checkpoints", 0),
            _binding("r_lib", "checkpoint", lib / "Stable-diffusion", 1, subdir="Stable-diffusion"),
        ],
    )
    scanner = ModelScanner(resolver)
    scanner.index_models(max_workers=1)
    _write(home / "checkpoints" / "b.safetensors", content)
    return scanner, resolver, original


def test_the_duplicate_report_points_at_the_real_path_of_the_surviving_copy(tmp_path, mock_db):
    scanner, _resolver_, original = _library_original_scanner(tmp_path)

    result = scanner.index_models(max_workers=1)

    same_as = result["skipped_duplicates"][0]["same_as"]
    assert same_as["path"] == original.as_posix()
    assert same_as["root"] == "r_lib"
    assert Path(same_as["path"]).is_file()


def test_the_duplicate_report_falls_back_to_the_root_label_when_the_surviving_root_is_offline(tmp_path, mock_db):
    scanner, resolver, _original = _library_original_scanner(tmp_path)
    resolver._probe._states["r_lib"] = ("offline", None)

    result = scanner.index_models(max_workers=1)

    same_as = result["skipped_duplicates"][0]["same_as"]
    assert same_as["path"] == "r_lib/checkpoints/nested/a.safetensors"
    assert same_as["root"] == "r_lib"
