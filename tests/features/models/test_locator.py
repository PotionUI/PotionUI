from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.features.models.indexer import ModelScanner
from src.features.models.locator import ModelFileUnavailable, ModelLocator
from src.features.models.records import Model
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


def _only_model() -> Model:
    return model_repo.get_all(include_providers=False, include_tags=False)[0]


def test_winner_falls_through_to_the_next_present_copy_when_the_file_vanished(tmp_path, mock_db):
    home = tmp_path / "home"
    lib = tmp_path / "lib"
    _write(home / "loras" / "x.safetensors", b"weights")
    _write(lib / "loras" / "x.safetensors", b"weights")
    resolver = _resolver(
        [_root("r_home", home), _root("r_lib", lib)],
        [_binding("r_home", "lora", home / "loras", 0), _binding("r_lib", "lora", lib / "loras", 1)],
    )
    ModelScanner(resolver).index_models(max_workers=1)
    model = _only_model()

    (home / "loras" / "x.safetensors").unlink()

    locator = ModelLocator(resolver)
    resolved = locator.path_for_model(model.id)

    assert resolved == lib / "loras" / "x.safetensors"


def test_conflicted_copies_are_never_returned(tmp_path, mock_db):
    home = tmp_path / "home"
    lib = tmp_path / "lib"
    _write(home / "loras" / "x.safetensors", b"AAAA")
    _write(lib / "loras" / "x.safetensors", b"BBBB")
    resolver = _resolver(
        [_root("r_home", home), _root("r_lib", lib)],
        [_binding("r_home", "lora", home / "loras", 0), _binding("r_lib", "lora", lib / "loras", 1)],
    )
    ModelScanner(resolver).index_models(max_workers=1)
    model = _only_model()

    locator = ModelLocator(resolver)
    resolved = locator.path_for_model(model.id)

    assert resolved == home / "loras" / "x.safetensors"


def test_offline_root_names_the_label_in_the_error(tmp_path, mock_db):
    home = tmp_path / "home"
    usb = tmp_path / "usb"
    _write(usb / "loras" / "y.safetensors", b"weights")
    states: Dict[str, Tuple[str, Optional[str]]] = {}
    resolver = _resolver(
        [_root("r_home", home), _root("r_usb", usb)],
        [_binding("r_home", "lora", home / "loras", 0), _binding("r_usb", "lora", usb / "loras", 1)],
        states=states,
    )
    ModelScanner(resolver).index_models(max_workers=1)
    model = _only_model()

    states["r_usb"] = ("offline", "unplugged")

    locator = ModelLocator(resolver)
    try:
        locator.path_for_model(model.id)
        assert False, "expected ModelFileUnavailable"
    except ModelFileUnavailable as exc:
        assert exc.root_label == "r_usb"
        assert "r_usb" in str(exc)
        assert "offline" in str(exc)


def test_path_for_ref_resolves_a_logical_ref_to_an_absolute_physical_path(tmp_path, mock_db):
    home = tmp_path / "home"
    _write(home / "loras" / "z.safetensors", b"weights")
    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "lora", home / "loras", 0)])
    ModelScanner(resolver).index_models(max_workers=1)

    locator = ModelLocator(resolver)
    resolved = locator.path_for_ref("loras/z.safetensors")

    assert resolved == home / "loras" / "z.safetensors"
    assert resolved.is_absolute()


def test_path_for_ref_names_the_offline_root(tmp_path, mock_db):
    usb = tmp_path / "usb"
    _write(usb / "loras" / "q.safetensors", b"weights")
    states: Dict[str, Tuple[str, Optional[str]]] = {}
    resolver = _resolver([_root("r_usb", usb)], [_binding("r_usb", "lora", usb / "loras", 0)], states=states)
    ModelScanner(resolver).index_models(max_workers=1)

    states["r_usb"] = ("offline", "unplugged")

    locator = ModelLocator(resolver)
    try:
        locator.path_for_ref("loras/q.safetensors")
        assert False, "expected ModelFileUnavailable"
    except ModelFileUnavailable as exc:
        assert exc.root_label == "r_usb"


def test_path_for_ref_rejects_a_non_logical_string(tmp_path, mock_db):
    resolver = _resolver([], [])
    locator = ModelLocator(resolver)

    try:
        locator.path_for_ref("not-a-model-ref")
        assert False, "expected ModelFileUnavailable"
    except ModelFileUnavailable:
        pass


def test_model_for_path_resolves_through_the_bound_directory(tmp_path, mock_db):
    home = tmp_path / "home"
    _write(home / "loras" / "w.safetensors", b"weights")
    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "lora", home / "loras", 0)])
    ModelScanner(resolver).index_models(max_workers=1)
    model = _only_model()

    locator = ModelLocator(resolver)
    found = locator.model_for_path(home / "loras" / "w.safetensors")

    assert found is not None
    assert found.id == model.id


def test_model_for_path_falls_back_to_a_unique_basename_match(tmp_path, mock_db):
    resolver = _resolver([], [])
    model = model_repo.create(Model(filename="only.safetensors", model_type="lora"))

    locator = ModelLocator(resolver)
    found = locator.model_for_path("/some/other/place/only.safetensors")

    assert found is not None
    assert found.id == model.id


def test_model_for_path_returns_none_when_nothing_matches(tmp_path, mock_db):
    resolver = _resolver([], [])
    locator = ModelLocator(resolver)

    assert locator.model_for_path("/nowhere/near/anything.safetensors") is None
